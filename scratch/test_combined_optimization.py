import os
import sys
import io
import time
from typing import List, Dict, Any

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.models.chunk import DocumentChunk
from backend.app.services.embedding_service import embedding_service
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.services.context_builder import ContextBuilder
from backend.app.config import settings

db = SessionLocal()
context_builder = ContextBuilder()

# 1. Implement and patch LRU embedding cache
_embedding_cache: Dict[str, List[float]] = {}
orig_embed_text = embedding_service.embed_text

def cached_embed_text(text: str, provider: str | None = None, target_dim: int | None = None) -> List[float]:
    key = f"{provider or ''}_{target_dim or ''}_{text.strip()}"
    if key in _embedding_cache:
        return _embedding_cache[key]
    res = orig_embed_text(text, provider=provider, target_dim=target_dim)
    _embedding_cache[key] = res
    return res

embedding_service.embed_text = cached_embed_text

# 2. Optimized retrieve_and_rerank
def optimized_retrieve_and_rerank(
    db,
    query: str,
    candidate_k: int = 30,
    final_k: int = 5,
    threshold: float | None = None,
    document_filter: str | None = None,
    enable_rerank: bool = True,
    enable_adjacent: bool = True
):
    # Stage 1: Hybrid candidate retrieval via RRF
    candidates = retrieval_service.hybrid_search(
        db=db,
        query=query,
        top_k=candidate_k,
        document_filter=document_filter
    )
    
    # Stage 1b: Streamlined facet enrichment using vector_search with cached embeddings
    analysis = retrieval_service.analyze_query(query)
    if analysis.get("facets") and len(analysis["facets"]) > 1:
        seen_ids = {c["chunk_id"] for c in candidates}
        facet_slots = max(2, min(4, (candidate_k // (len(analysis["facets"]) + 1))))
        facet_candidates = []
        for facet in analysis["facets"]:
            f_hits = retrieval_service.vector_search(db=db, query=facet, top_k=facet_slots, document_filter=document_filter)
            for fc in f_hits:
                if fc["chunk_id"] not in seen_ids:
                    seen_ids.add(fc["chunk_id"])
                    facet_candidates.append(fc)
        if facet_candidates:
            candidates = candidates + facet_candidates

    if not candidates:
        return [], False

    # Stage 2: Reranker stage
    if enable_rerank:
        top_chunks, has_sufficient = reranking_service.rerank(
            query=query,
            candidates=candidates,
            top_n=final_k,
            threshold=threshold
        )
    else:
        top_chunks = candidates[:final_k]
        thresh = threshold if threshold is not None else settings.SIMILARITY_THRESHOLD
        has_sufficient = bool(top_chunks and (top_chunks[0].get("score", 0.0) >= thresh))

    # Stage 3: Adjacent-chunk retrieval
    if enable_adjacent and top_chunks and has_sufficient:
        enriched_chunks = retrieval_service.fetch_adjacent_chunks(db=db, chunks=top_chunks, max_adjacent=2)
        return enriched_chunks, has_sufficient

    return top_chunks, has_sufficient

queries = [
    ("Q03", "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"),
    ("Q12", "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?"),
    ("Q01", "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?")
]

print("="*80)
print("VERIFYING OPTIMIZED PIPELINE: TIMINGS & TECHNICAL EVIDENCE RETENTION")
print("="*80)

for qid, qtext in queries:
    print(f"\n>>> Running [{qid}]: {qtext[:75]}...")
    t0 = time.perf_counter()
    chunks, sufficient = optimized_retrieve_and_rerank(db, qtext, candidate_k=settings.RRF_CANDIDATE_K, final_k=settings.FINAL_TOP_K)
    elapsed = time.perf_counter() - t0
    
    print(f"  Optimized retrieve_and_rerank took: {elapsed:.3f} s (Sufficient: {sufficient})")
    print("  Top Chunks:")
    for rank, c in enumerate(chunks[:5], 1):
        doc = (c.get("document_name") or "")[:20]
        sec = (c.get("section") or "")[:20]
        score = c.get("rerank_score", 0.0)
        t_preview = (c.get("chunk_text") or "").replace("\n", " ")[:60]
        print(f"    #{rank} | Score: {score:.4f} | {doc} | {sec} | {t_preview}")

db.close()
