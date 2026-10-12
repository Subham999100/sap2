import os
import sys
import io
import math

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', write_through=True)
sys.path.insert(0, r"c:\Users\Subham Patnaik\OneDrive\Desktop\agent\SAP_Project")

from backend.app.database import SessionLocal
from backend.app.models.chunk import DocumentChunk
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service
from backend.app.services.context_builder import context_builder
from backend.app.config import settings

db = SessionLocal()

BENCHMARK_SET = [
    {
        "id": "Q01",
        "topic": "SOM Order Distribution & Contract Replication",
        "query": "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?",
        "concepts": [
            ("Provider Contract / transition", ["provider contract", "subscription order", "contract"]),
            ("ODI mechanism / steps", ["odi", "order distribution", "distribution step", "distribution infrastructure"]),
            ("Replication to CC", ["convergent charging", "subscriber account", "charging contract"]),
            ("Replication to FI-CA", ["fi-ca", "contract account"])
        ]
    },
    {
        "id": "Q02",
        "topic": "CIT vs BIT Differences & Aggregation Flow",
        "query": "What is the structural difference between a Consumption Item (CIT) and a Billable Item (BIT) in SAP Convergent Invoicing, and what are the procedural steps to rate CITs and aggregate BITs into an invoice?",
        "concepts": [
            ("CIT unrated state", ["consumption item", "cit", "unrated", "rating"]),
            ("BIT rated state", ["billable item", "bit", "billing", "rated"]),
            ("Rating & aggregation", ["rating process", "invoicing", "aggregation", "invoice"])
        ]
    },
    {
        "id": "Q03",
        "topic": "Document Posting, Changing, Display & FPL9",
        "query": "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?",
        "concepts": [
            ("T-Codes FPE1/FPE2/FPE3", ["fpe1", "fpe2", "fpe3", "post document", "change document", "display document"]),
            ("FPL9 Purpose", ["fpl9", "account display", "account balance", "balance overview", "line item"])
        ]
    },
    {
        "id": "Q06",
        "topic": "Dunning Levels, Activities & Proposal Execution",
        "query": "How does the dunning procedure operate in SAP FI-CA, what is the role of dunning levels and dunning activities, and which transaction is used to execute the dunning proposal run?",
        "concepts": [
            ("Dunning procedure operation", ["dunning", "procedure", "overdue", "receivable"]),
            ("Levels & activities", ["dunning level", "dunning activit", "interest", "charge", "notice", "block"]),
            ("Dunning proposal run (FPVA/FPVB)", ["fpva", "fpvb", "dunning proposal", "proposal run"])
        ]
    },
    {
        "id": "Q12",
        "topic": "BIT Statuses & Monitoring with FKKBIX_BIT_MON",
        "query": "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?",
        "concepts": [
            ("BIT statuses (Raw, Billable, Billed)", ["raw", "billable", "billed", "status"]),
            ("FKKBIXBIT_MON error analysis", ["fkkbixbit_mon", "fkkbix_bit_mon", "monitor", "error", "exception", "modify"])
        ]
    }
]

def check_concept_coverage(chunks, concepts):
    """Returns fraction of concepts covered (Recall) and first rank of fully relevant chunk."""
    combined_texts = [(idx, (c.get("chunk_text") or "").lower()) for idx, c in enumerate(chunks, 1)]
    covered_concepts = 0
    first_rank_any_concept = None
    first_rank_all_concepts = None
    
    for c_name, terms in concepts:
        found = False
        for rk, text in combined_texts:
            if any(t in text for t in terms):
                found = True
                if first_rank_any_concept is None or rk < first_rank_any_concept:
                    first_rank_any_concept = rk
                break
        if found:
            covered_concepts += 1
            
    coverage_ratio = covered_concepts / len(concepts) if concepts else 0.0
    return coverage_ratio, first_rank_any_concept

def calculate_mrr(rank):
    return (1.0 / rank) if rank else 0.0

results = []

print("="*90)
print("PHASE 6: CONTROLLED RETRIEVAL COMPARISON EXPERIMENT")
print("="*90)

for item in BENCHMARK_SET:
    qid = item["id"]
    qtext = item["query"]
    concepts = item["concepts"]
    print(f"\nEvaluating [{qid}]: {item['topic']}...")
    
    # 1. Vector Search Only
    v_hits_30 = retrieval_service.vector_search(db, qtext, top_k=30)
    v_cov_10, v_rank_10 = check_concept_coverage(v_hits_30[:10], concepts)
    v_cov_30, v_rank_30 = check_concept_coverage(v_hits_30, concepts)
    v_mrr = calculate_mrr(v_rank_10)
    
    # 2. Lexical Search Only
    l_hits_30 = retrieval_service.lexical_search(db, qtext, top_k=30)
    l_cov_10, l_rank_10 = check_concept_coverage(l_hits_30[:10], concepts)
    l_cov_30, l_rank_30 = check_concept_coverage(l_hits_30, concepts)
    l_mrr = calculate_mrr(l_rank_10)
    
    # 3. Hybrid RRF Search Only (No Rerank)
    h_hits_30 = retrieval_service.hybrid_search(db, qtext, top_k=30)
    h_cov_10, h_rank_10 = check_concept_coverage(h_hits_30[:10], concepts)
    h_cov_30, h_rank_30 = check_concept_coverage(h_hits_30, concepts)
    h_mrr = calculate_mrr(h_rank_10)
    
    # 4. Hybrid RRF + ColBERT Reranker (Current Production Pipeline)
    top_chunks, sufficient = retrieval_service.retrieve_and_rerank(
        db, qtext, candidate_k=30, final_k=5, threshold=0.60, enable_rerank=True, enable_adjacent=False
    )
    r_cov_5, r_rank_5 = check_concept_coverage(top_chunks[:5], concepts)
    r_mrr = calculate_mrr(r_rank_5)
    
    # 5. Check Context Builder Retention
    context_str = context_builder.build_context(top_chunks, source_type="knowledge_base")
    ctx_covered = sum(1 for c_name, terms in concepts if any(t in context_str.lower() for t in terms))
    ctx_cov_ratio = ctx_covered / len(concepts)
    
    row = {
        "qid": qid,
        "topic": item["topic"],
        "v_cov_10": v_cov_10, "v_cov_30": v_cov_30, "v_mrr": v_mrr,
        "l_cov_10": l_cov_10, "l_cov_30": l_cov_30, "l_mrr": l_mrr,
        "h_cov_10": h_cov_10, "h_cov_30": h_cov_30, "h_mrr": h_mrr,
        "r_cov_5": r_cov_5, "r_mrr": r_mrr,
        "ctx_cov": ctx_cov_ratio,
        "sufficient": sufficient
    }
    results.append(row)
    print(f"  Vector Only:  Rec@10={v_cov_10:.2f}, Rec@30={v_cov_30:.2f}, MRR={v_mrr:.2f}")
    print(f"  Lexical Only: Rec@10={l_cov_10:.2f}, Rec@30={l_cov_30:.2f}, MRR={l_mrr:.2f}")
    print(f"  Hybrid RRF:   Rec@10={h_cov_10:.2f}, Rec@30={h_cov_30:.2f}, MRR={h_mrr:.2f}")
    print(f"  RRF+ColBERT:  Top-5 Concept Recall={r_cov_5:.2f}, MRR={r_mrr:.2f}")
    print(f"  In Final Context: Recall={ctx_cov_ratio:.2f}")

# Compute Aggregate Metrics
print("\n" + "="*90)
print("AGGREGATE CONTROLLED COMPARISON RESULTS")
print("="*90)

avg_v_10 = sum(r["v_cov_10"] for r in results) / len(results)
avg_v_30 = sum(r["v_cov_30"] for r in results) / len(results)
avg_v_mrr = sum(r["v_mrr"] for r in results) / len(results)

avg_l_10 = sum(r["l_cov_10"] for r in results) / len(results)
avg_l_30 = sum(r["l_cov_30"] for r in results) / len(results)
avg_l_mrr = sum(r["l_mrr"] for r in results) / len(results)

avg_h_10 = sum(r["h_cov_10"] for r in results) / len(results)
avg_h_30 = sum(r["h_cov_30"] for r in results) / len(results)
avg_h_mrr = sum(r["h_mrr"] for r in results) / len(results)

avg_r_5 = sum(r["r_cov_5"] for r in results) / len(results)
avg_r_mrr = sum(r["r_mrr"] for r in results) / len(results)
avg_ctx = sum(r["ctx_cov"] for r in results) / len(results)

print(f"| {'Configuration':<30} | {'Recall@10':<11} | {'Recall@30':<11} | {'MRR':<8} | {'Final Context Coverage':<22} |")
print("|" + "-"*32 + "|" + "-"*13 + "|" + "-"*13 + "|" + "-"*10 + "|" + "-"*24 + "|")
print(f"| {'1. Vector Only':<30} | {avg_v_10:<11.3f} | {avg_v_30:<11.3f} | {avg_v_mrr:<8.3f} | {'N/A (Candidate stage)':<22} |")
print(f"| {'2. Lexical Only (ts_rank_cd)':<30} | {avg_l_10:<11.3f} | {avg_l_30:<11.3f} | {avg_l_mrr:<8.3f} | {'N/A (Candidate stage)':<22} |")
print(f"| {'3. Hybrid RRF (Candidate Pool)':<30} | {avg_h_10:<11.3f} | {avg_h_30:<11.3f} | {avg_h_mrr:<8.3f} | {'N/A (Pre-rerank)':<22} |")
print(f"| {'4. Hybrid RRF + ColBERT Top-5':<30} | {'N/A (Top 5)':<11} | {avg_r_5:<11.3f} | {avg_r_mrr:<8.3f} | {avg_ctx:<22.3f} |")

db.close()
