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
q = "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?"

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

print(f"Total candidates retrieved for Q12: {len(candidates)}")
bit_mon_cands = [c for c in candidates if "fkkbixbit_mon" in (c.get("chunk_text") or "").lower()]
print(f"Candidates containing 'FKKBIXBIT_MON': {len(bit_mon_cands)}")
for c in bit_mon_cands:
    print(f"  Chunk: {c['chunk_id'][:8]} | Doc: {c.get('document_name')} | Page: {c.get('page_number')} | RRF: {c.get('rrf_score')}")
    print(f"  Text: {c.get('chunk_text', '')[:180]}...\n")

# Run rerank
scored, _ = reranking_service.rerank(query=q, candidates=candidates, top_n=5)
print("Top 5 Chunks selected for Q12:")
for idx, c in enumerate(scored, 1):
    has_mon = "FKKBIXBIT_MON" in (c.get("chunk_text") or "")
    print(f"Rank {idx}: ID={c['chunk_id'][:8]} | Doc={c.get('document_name')[:25]} | Score={c.get('rerank_score')} | has_mon={has_mon}")
    print(f"  Snippet: {c.get('chunk_text', '')[:140]}...")

db.close()
