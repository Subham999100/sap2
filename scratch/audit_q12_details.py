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

candidates = retrieval_service.hybrid_search(db=db, query=q, top_k=settings.RRF_CANDIDATE_K)

# Let's inspect neural score vs boosts for all candidates
scored_candidates, _ = reranking_service.rerank(query=q, candidates=candidates, top_n=len(candidates))

print("=== Q12 RERANKING DETAILED BREAKDOWN ===")
for rk, c in enumerate(scored_candidates[:15], start=1):
    cid = c['chunk_id'][:8]
    doc = c['document_name'][:25]
    score = c['rerank_score']
    has_mon = "fkkbix" in c['chunk_text'].lower()
    has_status = any(st in c['chunk_text'].lower() for st in ["raw", "billable", "billed"])
    print(f"#{rk:2d} | Score: {score:.4f} | Chunk: {cid} | Doc: {doc} | Section: {c.get('section')} | has_fkkbix: {has_mon} | has_status: {has_status}")
    print(f"     Snippet: {c['chunk_text'][:120]}...\n")

db.close()
