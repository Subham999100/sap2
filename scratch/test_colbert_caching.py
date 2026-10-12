import os
import sys
import io
import time
from typing import Dict, Any, List
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.config import settings

db = SessionLocal()

query = "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"

candidates = retrieval_service.hybrid_search(db, query, top_k=settings.RRF_CANDIDATE_K)
print(f"Testing with {len(candidates)} candidates.")

# Test Cold run of _score_candidates_with_model
t0 = time.perf_counter()
scores_cold = reranking_service._score_candidates_with_model(query, candidates)
t_cold = time.perf_counter() - t0
print(f"Cold _score_candidates_with_model: {t_cold:.3f} s")

# Simulate Chunk Embedding Cache
model = reranking_service._get_model()
doc_cache: Dict[str, np.ndarray] = {}

# Populate cache with candidate chunk embeddings
chunk_texts = [
    f"[{c.get('section')}] {(c.get('chunk_text') or '')[:1000]}" if c.get("section") else (c.get("chunk_text") or "")[:1000]
    for c in candidates
]
embeds = list(model.embed(chunk_texts))
for c, emb in zip(candidates, embeds):
    doc_cache[c["chunk_id"]] = np.asarray(emb)

# Test Warm run using cache
t0 = time.perf_counter()
q_emb = list(model.embed([query]))[0]
cached_doc_embeddings = [doc_cache[c["chunk_id"]] for c in candidates]
warm_scores = []
for d_emb in cached_doc_embeddings:
    sim_mat = np.dot(q_emb, d_emb.T)
    warm_scores.append(float(np.max(sim_mat, axis=1).sum()))
t_warm = time.perf_counter() - t0
print(f"Warm _score_candidates_with_model (using chunk cache): {t_warm*1000:.2f} ms ({t_cold/t_warm:.1f}x faster!)")

db.close()
