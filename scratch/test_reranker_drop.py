import os
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.models.chunk import DocumentChunk
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.config import settings

db = SessionLocal()

queries = [
    ("Q03", "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"),
    ("Q12", "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?"),
    ("Q01", "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?")
]

print("="*80)
print("INVESTIGATING WHY TECHNICAL CHUNKS DROP IN RANK AFTER RERANKING")
print("="*80)

for qid, qtext in queries:
    print(f"\n>>> [{qid}]: {qtext[:80]}...")
    t0 = time.time()
    candidates = retrieval_service.hybrid_search(db, qtext, top_k=settings.RRF_CANDIDATE_K)
    
    # Check facet candidate addition
    analysis = retrieval_service.analyze_query(qtext)
    if analysis.get("facets") and len(analysis["facets"]) > 1:
        seen_ids = {c["chunk_id"] for c in candidates}
        for facet in analysis["facets"]:
            f_hits = retrieval_service.hybrid_search(db=db, query=facet, top_k=3)
            for fc in f_hits:
                if fc["chunk_id"] not in seen_ids:
                    seen_ids.add(fc["chunk_id"])
                    candidates.append(fc)
                    
    pre_ranks = {c["chunk_id"]: idx for idx, c in enumerate(candidates, 1)}
    
    scored_candidates, sufficient = reranking_service.rerank(query=qtext, candidates=candidates, top_n=len(candidates))
    elapsed = time.time() - t0
    print(f"Reranked {len(candidates)} candidates in {elapsed:.3f}s. Sufficient: {sufficient}")
    
    print("\nTop 10 Chunks Post-Rerank:")
    for post_rank, c in enumerate(scored_candidates[:10], start=1):
        cid = c["chunk_id"]
        pre_r = pre_ranks.get(cid, "N/A")
        delta = pre_r - post_rank if isinstance(pre_r, int) else 0
        arrow = f"▲ +{delta}" if delta > 0 else (f"▼ {delta}" if delta < 0 else "=")
        doc = (c.get("document_name") or "")[:20]
        sec = (c.get("section") or "")[:20]
        score = c.get("rerank_score", 0.0)
        cov = c.get("query_coverage", 0.0)
        matches = c.get("identifier_matches", 0)
        text_preview = (c.get("chunk_text") or "").replace("\n", " ")[:60]
        print(f"  #{post_rank:2d} (was #{pre_r:2d}, {arrow:<5}) | Score: {score:.4f} | Cov: {cov:.2f} | TechMatch: {matches} | {doc} | {sec} | {text_preview}")

    # Specifically check if high-value technical chunks dropped
    print("\nTechnical Chunks in Candidate Pool and Their Movement:")
    for cid, pre_r in pre_ranks.items():
        c_obj = next(c for c in candidates if c["chunk_id"] == cid)
        t_lower = (c_obj.get("chunk_text") or "").lower()
        # Look for explicit T-codes, config identifiers, or component terms
        is_tech = any(k in t_lower for k in ["fpe1", "fpe2", "fpe3", "fpl9", "fkkbix", "odi", "step", "status 0", "raw"])
        if is_tech:
            post_c = next((c for c in scored_candidates if c["chunk_id"] == cid), None)
            post_r = scored_candidates.index(post_c) + 1 if post_c else "Dropped"
            delta = pre_r - post_r if isinstance(post_r, int) else 0
            arrow = f"▲ +{delta}" if delta > 0 else (f"▼ {delta}" if delta < 0 else "=")
            score = post_c.get("rerank_score", 0.0) if post_c else 0.0
            print(f"  [Tech Chunk] {cid[:8]} (pre #{pre_r} -> post #{post_r}, {arrow}) | Score: {score:.4f} | {c_obj.get('document_name')[:20]}: {t_lower[:70]}...")

db.close()
