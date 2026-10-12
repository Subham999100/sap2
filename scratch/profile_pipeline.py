import os
import sys
import io
import time
from typing import Dict, Any, List

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.models.chunk import DocumentChunk
from backend.app.services.embedding_service import embedding_service
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.services.context_builder import ContextBuilder
from backend.app.services.rag_service import rag_service
from backend.app.services.llm_service import llm_service
from backend.app.config import settings

db = SessionLocal()
context_builder = ContextBuilder()

queries = [
    ("Q03", "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"),
    ("Q12", "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?"),
    ("Q01", "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?")
]

print("="*90)
print("PROFILING END-TO-END RAG PIPELINE LATENCY ACROSS ALL PIPELINE STAGES")
print("="*90)

# Warm up FastEmbed / models if needed
print("Warming up models...")
_ = embedding_service.embed_text("warmup")
_ = reranking_service._get_model()
print("Models ready.\n")

timing_records = []

for qid, qtext in queries:
    print(f"\n>>> Running Profile for [{qid}]: {qtext[:75]}...")
    
    # 1. Query Embedding Timing
    t0 = time.perf_counter()
    analysis = retrieval_service.analyze_query(qtext)
    processed_query = analysis["cleaned_query"]
    query_vector = embedding_service.embed_text(processed_query)
    t_emb = time.perf_counter() - t0
    
    # 2. Vector Search Timing
    t0 = time.perf_counter()
    k = settings.RRF_CANDIDATE_K
    vector_col = DocumentChunk.embedding_new if len(query_vector) == 1536 else DocumentChunk.embedding
    vec_hits = (
        db.query(DocumentChunk)
        .filter(vector_col.isnot(None))
        .order_by(vector_col.op("<=>")(query_vector))
        .limit(k * 2)
        .all()
    )
    t_vec = time.perf_counter() - t0
    
    # 2b. Facet Vector Searches Timing (if any facets)
    t0 = time.perf_counter()
    facet_count = 0
    if analysis["facets"]:
        facet_count = len(analysis["facets"])
        for facet in analysis["facets"]:
            f_vec = embedding_service.embed_text(facet)
            f_hits = (
                db.query(DocumentChunk)
                .filter(vector_col.isnot(None))
                .order_by(vector_col.op("<=>")(f_vec))
                .limit(10)
                .all()
            )
    t_facet_vec = time.perf_counter() - t0
    
    # 3. Lexical Search Timing (GIN tsvector)
    t0 = time.perf_counter()
    from sqlalchemy import func, desc
    lex_q = (
        db.query(DocumentChunk, func.ts_rank_cd(
            func.to_tsvector('english', DocumentChunk.chunk_text),
            func.plainto_tsquery('english', processed_query)
        ).label('lex_score'))
        .filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.plainto_tsquery('english', processed_query)))
        .order_by(desc('lex_score'))
        .limit(k * 2)
    )
    lex_hits = lex_q.all()
    # Check focused expansion if needed
    focused_tsquery = retrieval_service._build_focused_tsquery(processed_query, analysis["expansion_terms"])
    if focused_tsquery and len(lex_hits) < 15:
        exp_q = (
            db.query(DocumentChunk, func.ts_rank_cd(
                func.to_tsvector('english', DocumentChunk.chunk_text),
                func.to_tsquery('english', focused_tsquery)
            ).label('lex_score'))
            .filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.to_tsquery('english', focused_tsquery)))
            .order_by(desc('lex_score'))
            .limit(k * 2)
        )
        _ = exp_q.all()
    t_lex = time.perf_counter() - t0
    
    # 4. Total Hybrid Search Execution
    t0 = time.perf_counter()
    candidates = retrieval_service.hybrid_search(db=db, query=qtext, top_k=settings.RRF_CANDIDATE_K)
    t_hybrid_total = time.perf_counter() - t0
    
    # 5. Facet Enrichment Calls in retrieve_and_rerank
    t0 = time.perf_counter()
    facet_candidates = []
    if analysis.get("facets") and len(analysis["facets"]) > 1:
        seen_ids = {c["chunk_id"] for c in candidates}
        facet_slots = max(2, min(5, (k // (len(analysis["facets"]) + 1))))
        for facet in analysis["facets"]:
            f_hits = retrieval_service.hybrid_search(db=db, query=facet, top_k=facet_slots)
            for fc in f_hits:
                if fc["chunk_id"] not in seen_ids:
                    seen_ids.add(fc["chunk_id"])
                    facet_candidates.append(fc)
    all_candidates = candidates + facet_candidates
    t_facet_enrichment = time.perf_counter() - t0
    
    # 6. Reranking Timing (ColBERT + Heuristics + Deduplication)
    t0 = time.perf_counter()
    # Let's break down ColBERT neural scoring vs Heuristics vs Deduplication
    t_colbert_start = time.perf_counter()
    neural_scores = reranking_service._score_candidates_with_model(qtext, all_candidates)
    t_colbert_only = time.perf_counter() - t_colbert_start
    
    t_heur_start = time.perf_counter()
    scored, sufficient = reranking_service.rerank(qtext, all_candidates, top_n=settings.FINAL_TOP_K)
    t_rerank_total = time.perf_counter() - t0
    t_heur_and_dedup = t_rerank_total - t_colbert_only
    
    # 7. Context Building Timing
    t0 = time.perf_counter()
    built_context = context_builder.build_context(scored)
    t_context = time.perf_counter() - t0
    
    # 8. Full retrieve_and_rerank Timing
    t0 = time.perf_counter()
    retrieved_chunks, has_sufficient = retrieval_service.retrieve_and_rerank(
        db=db,
        query=qtext,
        candidate_k=settings.RRF_CANDIDATE_K,
        final_k=settings.FINAL_TOP_K,
        enable_rerank=True,
        enable_adjacent=True
    )
    t_retrieve_rerank_total = time.perf_counter() - t0
    
    rec = {
        "qid": qid,
        "query_embedding": t_emb,
        "vector_search": t_vec,
        "facet_vector": t_facet_vec,
        "lexical_search": t_lex,
        "hybrid_total": t_hybrid_total,
        "facet_enrichment": t_facet_enrichment,
        "candidate_pool_size": len(all_candidates),
        "colbert_model": t_colbert_only,
        "heur_and_dedup": t_heur_and_dedup,
        "rerank_total": t_rerank_total,
        "context_building": t_context,
        "retrieve_and_rerank_total": t_retrieve_rerank_total
    }
    timing_records.append(rec)
    
    print(f"  Stage Timings for {qid}:")
    print(f"    - Query Embedding:            {t_emb*1000:7.2f} ms")
    print(f"    - Base Vector Search:         {t_vec*1000:7.2f} ms")
    print(f"    - Facet Vector ({facet_count} facets):     {t_facet_vec*1000:7.2f} ms")
    print(f"    - Lexical Search:             {t_lex*1000:7.2f} ms")
    print(f"    - Stage 1 Hybrid Total:       {t_hybrid_total*1000:7.2f} ms")
    print(f"    - Facet Enrichment Hybrid:    {t_facet_enrichment*1000:7.2f} ms ({len(all_candidates)} candidates total)")
    print(f"    - ColBERT Neural Model Only:  {t_colbert_only*1000:7.2f} ms ({len(all_candidates)} docs)")
    print(f"    - Heuristics & Dedup:         {t_heur_and_dedup*1000:7.2f} ms")
    print(f"    - Stage 2 Rerank Total:       {t_rerank_total*1000:7.2f} ms")
    print(f"    - Context Building:           {t_context*1000:7.2f} ms")
    print(f"    => Total retrieve_and_rerank: {t_retrieve_rerank_total:7.3f} s")

# 9. LLM Generation timing check (if client available / mock / local)
print("\n" + "="*90)
print("CHECKING LLM GENERATION LATENCY")
print("="*90)
t0 = time.perf_counter()
try:
    test_context = context_builder.build_context(scored)
    prep_state = {
        "status": "ready",
        "source_type": "knowledge_base",
        "internal_evidence": scored,
        "web_evidence": None,
        "chat_history": []
    }
    # Test non-streaming generate_answer if credentials available
    t_llm_start = time.perf_counter()
    ans, citations, g_score, src, web_s = rag_service.generate_answer(db, "What is SAP FI-CA?", chat_id=None)
    t_llm = time.perf_counter() - t_llm_start
    print(f"End-to-end generate_answer took: {t_llm:.3f} s (Answer length: {len(ans)} chars)")
except Exception as e:
    print(f"LLM execution check notice: {e}")

db.close()
