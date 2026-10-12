import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from sqlalchemy import func, desc, text
from backend.app.database import SessionLocal
from backend.app.models.chunk import DocumentChunk
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.services.embedding_service import embedding_service
from backend.app.config import settings

db = SessionLocal()

query = "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"
k = 30

analysis = retrieval_service.analyze_query(query)
processed_query = analysis["cleaned_query"]
query_vector = embedding_service.embed_text(processed_query)
vector_col = DocumentChunk.embedding_new

# Vector search
vec_q = (
    db.query(DocumentChunk)
    .filter(vector_col.isnot(None))
    .order_by(vector_col.op("<=>")(query_vector))
    .limit(k * 2)
)
vec_hits = vec_q.all()

# Lexical search
# 3a. Strict
lex_q = db.query(DocumentChunk, func.ts_rank_cd(
    func.to_tsvector('english', DocumentChunk.chunk_text),
    func.plainto_tsquery('english', processed_query)
).label('lex_score'))
lex_q = (
    lex_q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.plainto_tsquery('english', processed_query)))
    .order_by(desc('lex_score'))
    .limit(k * 2)
)
lex_hits = [(c, s) for c, s in lex_q.all()]

# 3b. Focused
focused_tsquery = retrieval_service._build_focused_tsquery(processed_query, analysis["expansion_terms"])
if focused_tsquery and len(lex_hits) < 15:
    exp_q = db.query(DocumentChunk, func.ts_rank_cd(
        func.to_tsvector('english', DocumentChunk.chunk_text),
        func.to_tsquery('english', focused_tsquery)
    ).label('lex_score'))
    exp_q = (
        exp_q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.to_tsquery('english', focused_tsquery)))
        .order_by(desc('lex_score'))
        .limit(k * 2)
    )
    exp_results = [(c, s) for c, s in exp_q.all()]
    seen_lex_ids = {c.id for c, _ in lex_hits}
    for c, s in exp_results:
        if c.id not in seen_lex_ids:
            lex_hits.append((c, s))
            seen_lex_ids.add(c.id)

# 3c. Fallback
if len(lex_hits) < 10:
    tokens = retrieval_service._extract_lexical_tokens(processed_query)
    if tokens:
        or_expr = " | ".join(tokens[:10])
        fallback_q = db.query(DocumentChunk, func.ts_rank_cd(
            func.to_tsvector('english', DocumentChunk.chunk_text),
            func.to_tsquery('english', or_expr)
        ).label('lex_score'))
        fallback_q = (
            fallback_q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.to_tsquery('english', or_expr)))
            .order_by(desc('lex_score'))
            .limit(k * 2)
        )
        fallback_hits = [(c, s) for c, s in fallback_q.all()]
        seen_ids = {c.id for c, _ in lex_hits}
        for c, s in fallback_hits:
            if c.id not in seen_ids:
                lex_hits.append((c, s))
                seen_ids.add(c.id)
        lex_hits = lex_hits[:k * 2]

denom = 60
vec_ranks = {chunk.id: (rank, chunk) for rank, chunk in enumerate(vec_hits, start=1)}
lex_ranks = {chunk.id: (rank, chunk, score) for rank, (chunk, score) in enumerate(lex_hits, start=1)}

scores = {}
for rank, chunk in enumerate(vec_hits, start=1):
    scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (denom + rank)
for rank, (chunk, _) in enumerate(lex_hits, start=1):
    scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (denom + rank)

id_to_chunk = {c.id: c for c in vec_hits}
id_to_chunk.update({c.id: c for c, _ in lex_hits})

sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
top_fused = sorted_items[:k]

print(f"Total Unique Candidates across both: {len(scores)}")
print(f"Vector hits count: {len(vec_hits)}, Lexical hits count: {len(lex_hits)}")
print(f"Fused candidate pool size: {len(top_fused)}")

print("\nDIAGNOSTIC TABLE (TOP 20 FUSED CANDIDATES):")
print(f"| {'Chunk ID':<10} | {'Vec Rank':<8} | {'Lex Rank':<8} | {'Vec Dist':<8} | {'Lex Score':<9} | {'RRF Score':<9} | {'Fused Rank':<10} | {'Document & Snippet':<50} |")
print("|" + "-"*12 + "|" + "-"*10 + "|" + "-"*10 + "|" + "-"*10 + "|" + "-"*11 + "|" + "-"*11 + "|" + "-"*12 + "|" + "-"*52 + "|")

for fused_rank, (cid, rrf_score) in enumerate(sorted_items[:35], start=1):
    v_info = vec_ranks.get(cid)
    l_info = lex_ranks.get(cid)
    v_rank = str(v_info[0]) if v_info else "None"
    l_rank = str(l_info[0]) if l_info else "None"
    l_score = f"{l_info[2]:.4f}" if l_info else "None"
    chunk = id_to_chunk[cid]
    doc_name = (chunk.document_name or "")[:25]
    text_snip = (chunk.chunk_text or "").replace("\n", " ")[:40]
    in_pool = "TOP 30" if fused_rank <= 30 else "LOST (>30)"
    print(f"| {cid[:10]:<10} | {v_rank:<8} | {l_rank:<8} | {'--':<8} | {l_score:<9} | {rrf_score:.6f} | {fused_rank:<4} ({in_pool}) | {doc_name}: {text_snip} |")

# Also check chunks that specifically mention FPL9 or FPE1 in the entire DB!
print("\n" + "="*80)
print("GROUND-TRUTH CHUNKS IN DB CONTAINING 'FPL9' or 'FPE1':")
fpl9_chunks = db.query(DocumentChunk).filter(DocumentChunk.chunk_text.ilike("%fpl9%")).all()
print(f"Total chunks containing 'FPL9' in entire database: {len(fpl9_chunks)}")
for fc in fpl9_chunks:
    v_r = vec_ranks.get(fc.id, ("None",))[0]
    l_r = lex_ranks.get(fc.id, ("None",))[0]
    rrf_sc = scores.get(fc.id, 0.0)
    # find rank in sorted_items
    fused_r = next((idx for idx, (cid, _) in enumerate(sorted_items, start=1) if cid == fc.id), "None")
    print(f"Chunk ID {fc.id} (Doc: {fc.document_name}, Page: {fc.page_number}): Vec Rank={v_r}, Lex Rank={l_r}, RRF Score={rrf_sc:.6f}, Fused Rank={fused_r}")
    print(f"  Snippet: {fc.chunk_text[:120]}...\n")

db.close()
