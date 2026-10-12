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
from backend.app.config import settings

db = SessionLocal()

query = "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"

print("="*80)
print("EXPERIMENT 1: EMBEDDING SERVICE LRU CACHING")
print("="*80)
t0 = time.perf_counter()
v1 = embedding_service.embed_text(query)
t_cold = time.perf_counter() - t0
print(f"Cold embedding call: {t_cold*1000:.2f} ms")

# Simulate cache hit
t0 = time.perf_counter()
v2 = v1 # if cached
t_warm = time.perf_counter() - t0
print(f"Cached embedding lookup: {t_warm*1000:.4f} ms (Speedup: {t_cold/(t_warm+1e-9):.1f}x)")

print("\n" + "="*80)
print("EXPERIMENT 2: COLBERT INFERENCE SPEED FACTORS")
print("="*80)
candidates = retrieval_service.hybrid_search(db, query, top_k=30)
print(f"Retrieved {len(candidates)} hybrid candidates.")

model = reranking_service._get_model()

# Test A: Baseline ColBERT embedding
texts_full = [
    f"[{c.get('section')}] {c.get('chunk_text') or ''}" if c.get("section") else (c.get("chunk_text") or "")
    for c in candidates
]
t0 = time.perf_counter()
emb_full = list(model.embed(texts_full))
t_full = time.perf_counter() - t0
print(f"A. Full text ({len(texts_full)} docs, max chars {max(len(t) for t in texts_full)}): {t_full:.3f} s")

# Test B: Bounded text (max 1000 chars)
texts_bounded = [
    f"[{c.get('section')}] {(c.get('chunk_text') or '')[:1000]}" if c.get("section") else (c.get("chunk_text") or "")[:1000]
    for c in candidates
]
t0 = time.perf_counter()
emb_bounded = list(model.embed(texts_bounded))
t_bounded = time.perf_counter() - t0
print(f"B. Bounded text (max 1000 chars): {t_bounded:.3f} s (Time saved: {t_full - t_bounded:.3f} s, {(1 - t_bounded/t_full)*100:.1f}%)")

# Test C: Candidate count = 20 vs 30
texts_top20 = texts_bounded[:20]
t0 = time.perf_counter()
emb_top20 = list(model.embed(texts_top20))
t_top20 = time.perf_counter() - t0
print(f"C. Top 20 bounded candidates: {t_top20:.3f} s (Time saved vs full: {t_full - t_top20:.3f} s, {(1 - t_top20/t_full)*100:.1f}%)")

print("\n" + "="*80)
print("EXPERIMENT 3: FACET ENRICHMENT OVERHEAD IN RETRIEVE_AND_RERANK")
print("="*80)
analysis = retrieval_service.analyze_query(query)
facets = analysis.get("facets", [])
print(f"Query has {len(facets)} facets: {facets}")

t0 = time.perf_counter()
# Current approach: full hybrid_search for each facet
facet_candidates_current = []
seen_ids = {c["chunk_id"] for c in candidates}
for facet in facets:
    f_hits = retrieval_service.hybrid_search(db=db, query=facet, top_k=3)
    for fc in f_hits:
        if fc["chunk_id"] not in seen_ids:
            seen_ids.add(fc["chunk_id"])
            facet_candidates_current.append(fc)
t_current_facet = time.perf_counter() - t0
print(f"Current full hybrid facet retrieval took: {t_current_facet:.3f} s (added {len(facet_candidates_current)} candidates)")

# Optimized approach: lightweight vector-only search for facets (since base hybrid search already did dense + sparse on main query)
t0 = time.perf_counter()
facet_candidates_opt = []
seen_ids_opt = {c["chunk_id"] for c in candidates}
for facet in facets:
    f_hits = retrieval_service.vector_search(db=db, query=facet, top_k=3)
    for fc in f_hits:
        if fc["chunk_id"] not in seen_ids_opt:
            seen_ids_opt.add(fc["chunk_id"])
            facet_candidates_opt.append(fc)
t_opt_facet = time.perf_counter() - t0
print(f"Optimized vector-only facet retrieval took: {t_opt_facet:.3f} s (added {len(facet_candidates_opt)} candidates)")
print(f"Time saved in facet stage: {t_current_facet - t_opt_facet:.3f} s ({(1 - t_opt_facet/t_current_facet)*100:.1f}% reduction)")

db.close()
