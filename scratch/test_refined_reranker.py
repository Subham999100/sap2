import os
import sys
import io
import time
import re
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service, TOKEN_PATTERN, STOP_WORDS
from backend.app.config import settings

db = SessionLocal()

# Expanded technical identifiers
EXTENDED_TECHNICAL_IDENTIFIERS = {
    # T-Codes
    "fpl9", "fpe1", "fpe2", "fpe3", "fp04", "fpy1", "fpva", "fpvb",
    "fpo1", "fp06", "fp07", "fp08", "fp40", "spro", "pfcg", "sm30",
    "fkkbix", "fkkbixbit_mon", "fkkbix_bit_mon", "fkkbixcit_mon", "fkkbix_cit_mon",
    # Core SAP BRIM components & acronyms
    "som", "cc", "ci", "fi-ca", "fica", "rar", "sd", "s4hana", "btp",
    # Domain concepts & objects
    "pob", "odi", "bit", "cit", "rap", "daas",
}

def normalize_token(t: str) -> str:
    return t.replace("_", "").replace("-", "").replace("/", "")

EXTENDED_TECH_NORM = {normalize_token(t) for t in EXTENDED_TECHNICAL_IDENTIFIERS}

def refined_heuristic_features(query: str, candidate: Dict[str, Any]) -> Dict[str, float]:
    query_lower = query.lower()
    raw_query_tokens = TOKEN_PATTERN.findall(query_lower)
    candidate_text = candidate.get("chunk_text") or ""
    text_lower = candidate_text.lower()
    raw_text_tokens = TOKEN_PATTERN.findall(text_lower)
    
    norm_query_tokens = [normalize_token(t) for t in raw_query_tokens]
    norm_text_tokens = set(normalize_token(t) for t in raw_text_tokens)
    
    # Exact or normalized identifier matching
    identifier_matches = sum(
        1
        for t, norm_t in zip(raw_query_tokens, norm_query_tokens)
        if norm_t in EXTENDED_TECH_NORM and norm_t in norm_text_tokens
    )
    
    significant_tokens = [
        t for t in norm_query_tokens
        if t not in STOP_WORDS and len(t) > 2
    ]
    sig_set = set(significant_tokens)
    
    section_lower = (candidate.get("section") or "").lower()
    norm_section_tokens = set(normalize_token(t) for t in TOKEN_PATTERN.findall(section_lower))
    
    section_matches = sum(1 for t in sig_set if t in norm_section_tokens)
    content_matches = sum(1 for t in sig_set if t in norm_text_tokens)
    
    coverage = content_matches / len(sig_set) if sig_set else 0.0
    section_boost = min(section_matches * 0.04, 0.12)
    
    query_terms = [t for t in raw_query_tokens if t not in STOP_WORDS]
    ngram_matches = 0
    for idx in range(len(query_terms) - 1):
        phrase = f"{query_terms[idx]} {query_terms[idx + 1]}"
        if phrase in text_lower:
            ngram_matches += 1
            
    ngram_boost = min(ngram_matches * 0.025, 0.10)
    
    direct_boost = 0.0
    if re.search(r"\b(?:is defined as|refers to|represents|consists of)\b", text_lower):
        direct_boost += 0.025
    if re.search(r"\b(?:step\s+\d+|prerequisites|procedure|process flow|transaction code)\b", text_lower):
        direct_boost += 0.025
        
    direct_boost += min(identifier_matches * 0.07, 0.18)
    
    if "price plan" in query_lower and "price table" in query_lower:
        if "price plan" in text_lower and "price table" in text_lower:
            direct_boost += 0.06
            
    document_lower = (candidate.get("document_name") or "").lower()
    noise_penalty = 0.0
    if "catalog" in document_lower or "press-catalog" in document_lower:
        noise_penalty += 0.12
    if text_lower.count(".....") > 3:
        noise_penalty += 0.10
        
    return {
        "section_boost": section_boost,
        "ngram_boost": ngram_boost,
        "direct_boost": direct_boost,
        "noise_penalty": noise_penalty,
        "query_coverage": coverage,
        "identifier_matches": float(identifier_matches),
    }

def deduplicate_chunks(candidates: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    selected = []
    seen_prefixes = set()
    for c in candidates:
        text = (c.get("chunk_text") or "").strip()
        # Fingerprint: normalized first 150 alphanumeric characters
        fp = re.sub(r"\W+", "", text[:200].lower())
        if fp and fp in seen_prefixes:
            continue
        if fp:
            seen_prefixes.add(fp)
        selected.append(c)
        if len(selected) >= top_n:
            break
    return selected

def refined_rerank(query: str, candidates: List[Dict[str, Any]], top_n: int = 5):
    # Score candidates using neural model with section prepended
    model = reranking_service._get_model()
    
    # Prepend section for neural context
    chunk_texts = [
        f"[{c.get('section')}] {c.get('chunk_text') or ''}" if c.get("section") else (c.get("chunk_text") or "")
        for c in candidates
    ]
    query_embeddings = list(model.embed([query]))
    doc_embeddings = list(model.embed(chunk_texts))
    
    q_emb = np.asarray(query_embeddings[0])
    scores = []
    for d_emb in doc_embeddings:
        d_emb = np.asarray(d_emb)
        sim_mat = np.dot(q_emb, d_emb.T)
        scores.append(float(np.max(sim_mat, axis=1).sum()))
        
    s_min, s_max = min(scores), max(scores)
    s_range = s_max - s_min
    norm_scores = [(s - s_min) / s_range if s_range > 1e-12 else 0.5 for s in scores]
    
    scored = []
    for idx, cand in enumerate(candidates):
        item = dict(cand)
        raw_base = float(cand.get("rrf_score", cand.get("score", 0.0)) or 0.0)
        feats = refined_heuristic_features(query, cand)
        
        # Neural score + boosts + query_coverage boost (0.10)
        comb = (
            0.70 * norm_scores[idx]
            + 0.10 * feats["query_coverage"]
            + feats["section_boost"]
            + feats["ngram_boost"]
            + feats["direct_boost"]
            - feats["noise_penalty"]
        )
        
        item["rerank_score"] = round(float(comb), 6)
        item["score"] = item["rerank_score"]
        item["retrieval_method"] = "hybrid_rrf_rerank"
        item["rrf_score"] = raw_base
        item["query_coverage"] = round(feats["query_coverage"], 4)
        item["identifier_matches"] = int(feats["identifier_matches"])
        scored.append(item)
        
    scored.sort(key=lambda x: (x["rerank_score"], x["rrf_score"]), reverse=True)
    
    top_chunks = deduplicate_chunks(scored, top_n)
    return scored, top_chunks

queries = [
    ("Q03", "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"),
    ("Q12", "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?"),
    ("Q01", "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?")
]

print("="*80)
print("TESTING REFINED RERANKING WITH EXTENDED IDENTIFIERS & DEDUPLICATION")
print("="*80)

for qid, qtext in queries:
    print(f"\n>>> [{qid}]: {qtext[:80]}...")
    t0 = time.time()
    candidates = retrieval_service.hybrid_search(db, qtext, top_k=settings.RRF_CANDIDATE_K)
    
    all_scored, top_5 = refined_rerank(qtext, candidates, top_n=5)
    elapsed = time.time() - t0
    print(f"Refined rerank completed in {elapsed:.3f}s. Selected {len(top_5)} distinct chunks.")
    
    print("\nTop 5 Chunks Post-Refined-Rerank:")
    for post_r, c in enumerate(top_5, 1):
        doc = (c.get("document_name") or "")[:20]
        sec = (c.get("section") or "")[:20]
        score = c.get("rerank_score", 0.0)
        cov = c.get("query_coverage", 0.0)
        m = c.get("identifier_matches", 0)
        text_p = (c.get("chunk_text") or "").replace("\n", " ")[:60]
        print(f"  #{post_r} | Score: {score:.4f} | Cov: {cov:.2f} | TechMatch: {m} | {doc} | {sec} | {text_p}")

db.close()
