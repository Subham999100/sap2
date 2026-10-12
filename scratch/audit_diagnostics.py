import os
import sys
import io
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from sqlalchemy import func, desc, text
from backend.app.database import SessionLocal
from backend.app.models.chunk import DocumentChunk
from backend.app.services.retrieval_service import retrieval_service, SAP_TAXONOMY, STOP_WORDS
from backend.app.services.reranking_service import reranking_service
from backend.app.services.context_builder import context_builder
from backend.app.services.grounding_service import grounding_service
from backend.app.config import settings

db = SessionLocal()

# Representative queries:
queries = [
    ("Q01", "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?"),
    ("Q02", "What is the structural difference between a Consumption Item (CIT) and a Billable Item (BIT) in SAP Convergent Invoicing, and what are the procedural steps to rate CITs and aggregate BITs into an invoice?"),
    ("Q03", "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?"),
    ("Q08", "Design an end-to-end telecom pricing and billing solution across SAP SOM, CC, CI, and FI-CA for a postpaid mobile plan with ₹499 monthly base charge, 100 GB bundled data, and ₹50 per 5 GB overage."),
    ("Q12", "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?"),
]

print("="*80)
print("AUDIT DIAGNOSTICS: PHASE 2 & PHASE 3")
print("="*80)

for q_id, q_text in queries:
    print(f"\n>>> Query [{q_id}]: {q_text[:90]}...")
    analysis = retrieval_service.analyze_query(q_text)
    processed = analysis["cleaned_query"]
    tokens = retrieval_service._extract_lexical_tokens(processed)
    expansions = analysis["expansion_terms"]
    focused_tsquery = retrieval_service._build_focused_tsquery(processed, expansions)
    
    # 1. Test Strict tsquery
    strict_res = db.execute(text(
        "SELECT plainto_tsquery('english', :q)::text"
    ), {"q": processed}).scalar()
    
    strict_count = db.execute(text(
        "SELECT count(*) FROM document_chunks WHERE to_tsvector('english', chunk_text) @@ plainto_tsquery('english', :q)"
    ), {"q": processed}).scalar()
    
    print(f"  Strict plainto_tsquery: '{strict_res}' -> Count: {strict_count}")
    
    # 2. Test Focused tsquery
    focused_count = 0
    if focused_tsquery:
        try:
            focused_res = db.execute(text(
                "SELECT to_tsquery('english', :q)::text"
            ), {"q": focused_tsquery}).scalar()
            focused_count = db.execute(text(
                "SELECT count(*) FROM document_chunks WHERE to_tsvector('english', chunk_text) @@ to_tsquery('english', :q)"
            ), {"q": focused_tsquery}).scalar()
            print(f"  Focused tsquery: '{focused_res}' -> Count: {focused_count}")
        except Exception as e:
            print(f"  Focused tsquery error: {e}")
            
    # 3. Test Fallback OR query
    if tokens:
        or_expr = " | ".join(tokens[:10])
        try:
            or_count = db.execute(text(
                "SELECT count(*) FROM document_chunks WHERE to_tsvector('english', chunk_text) @@ to_tsquery('english', :q)"
            ), {"q": or_expr}).scalar()
            print(f"  Fallback OR tsquery: '{or_expr}' -> Count: {or_count}")
        except Exception as e:
            print(f"  Fallback tsquery error: {e}")

db.close()
