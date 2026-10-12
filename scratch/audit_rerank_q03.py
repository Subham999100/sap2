import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.config import settings

db = SessionLocal()
q = "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"

print("--- RUNNING RETRIEVE AND RERANK FOR Q03 ---")
top_chunks, has_sufficient = retrieval_service.retrieve_and_rerank(
    db=db,
    query=q,
    candidate_k=settings.RRF_CANDIDATE_K,
    final_k=settings.FINAL_TOP_K,
    threshold=settings.SIMILARITY_THRESHOLD,
    enable_rerank=True,
    enable_adjacent=True
)

print(f"has_sufficient: {has_sufficient}")
print(f"Final chunk count: {len(top_chunks)}")
for idx, c in enumerate(top_chunks, 1):
    print(f"\n[Final Rank {idx}] ID: {c.get('chunk_id')}")
    print(f"  Doc: {c.get('document_name')} | Page: {c.get('page_number')} | Section: {c.get('section')}")
    print(f"  Rerank Score: {c.get('rerank_score')} | Method: {c.get('retrieval_method')}")
    print(f"  Snippet: {c.get('chunk_text', '')[:200]}...")

# Check if target chunk 9b2578de was among the candidates passed to reranking
analysis = retrieval_service.analyze_query(q)
candidates = retrieval_service.hybrid_search(db=db, query=q, top_k=settings.RRF_CANDIDATE_K)
if analysis.get("facets") and len(analysis["facets"]) > 1:
    seen_ids = {c["chunk_id"] for c in candidates}
    for facet in analysis["facets"]:
        f_hits = retrieval_service.hybrid_search(db=db, query=facet, top_k=2)
        for fc in f_hits:
            if fc["chunk_id"] not in seen_ids:
                seen_ids.add(fc["chunk_id"])
                candidates.append(fc)

print(f"\nTotal candidates passed to reranker: {len(candidates)}")
target_id = "9b2578de-0d24-4554-96dc-f9d82ba9d3d7"
target_cand = next((c for c in candidates if c["chunk_id"] == target_id), None)
print(f"Target 9b2578de in candidates? {target_cand is not None}")

# Now let's see how the reranker scored all candidates
scored_chunks, _ = reranking_service.rerank(query=q, candidates=candidates, top_n=len(candidates))
print("\n--- ALL CANDIDATES RANKED BY RERANKER ---")
for rk, c in enumerate(scored_chunks, 1):
    is_target = " <<< TARGET (FPE1, FPE2, FPE3)" if c["chunk_id"] == target_id else ""
    is_fpl9 = " <<< HAS FPL9" if "fpl9" in (c.get("chunk_text") or "").lower() else ""
    print(f"Rerank #{rk:2d} (Score: {c.get('rerank_score'):.4f}) | Chunk: {c['chunk_id'][:8]} | Doc: {c.get('document_name')[:25]} | Sec: {c.get('section')}{is_target}{is_fpl9}")

db.close()
