"""
Final Comprehensive SAP BRIM RAG Answer-Quality Evaluation Runner.
Evaluates 15 representative, professional SAP BRIM questions across:
- SAP CC, CI, FI-CA, SOM, RAR
- Billable and Consumption Items (BITs/CITs)
- Integration flows, procedures, prerequisites, configuration and troubleshooting
- Multi-part complex scenarios
- Negative/Absent knowledge questions

Outputs detailed per-question reports, saves results to markdown,
calculates overall average, and identifies the five weakest answers with failure reasons.
"""

import os
import sys
import io
import json
import logging
from typing import List, Dict, Any

# Ensure UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', write_through=True)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.database import SessionLocal
from backend.app.services.rag_service import rag_service

logging.basicConfig(level=logging.WARNING)

EVALUATION_BENCHMARK = [
    {
        "id": "Q01",
        "category": "SOM to CC/FI-CA Integration & Replication",
        "topic": "SOM Order Distribution & Contract Replication",
        "query": "How does a Subscription Order in SAP SOM transition into a Provider Contract, and how does the Order Distribution Infrastructure (ODI) replicate contract data to SAP Convergent Charging and FI-CA?",
        "multi_parts": [
            "Subscription order to provider contract transition",
            "ODI distribution mechanism and steps",
            "Replication to Convergent Charging",
            "Replication to FI-CA contract accounts"
        ],
        "key_concepts": [
            {"name": "Provider Contract generation", "terms": ["provider contract", "subscription order", "contract"]},
            {"name": "ODI mechanism", "terms": ["odi", "order distribution", "distribution", "step", "status"]},
            {"name": "Replication to CC", "terms": ["convergent charging", "cc", "subscriber account", "charging"]},
            {"name": "Replication to FI-CA", "terms": ["fi-ca", "contract account", "fpl9", "fpe"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q02",
        "category": "CI Data Objects & Rating Lifecycle",
        "topic": "CIT vs BIT Differences & Aggregation Flow",
        "query": "What is the structural difference between a Consumption Item (CIT) and a Billable Item (BIT) in SAP Convergent Invoicing, and what are the procedural steps to rate CITs and aggregate BITs into an invoice?",
        "multi_parts": [
            "Structural/status difference between CIT and BIT",
            "Rating CITs into BITs",
            "Aggregating BITs during billing into invoicing"
        ],
        "key_concepts": [
            {"name": "CIT definition & unrated state", "terms": ["consumption item", "cit", "unrated", "rating", "raw"]},
            {"name": "BIT definition & rated state", "terms": ["billable item", "bit", "billing", "rated"]},
            {"name": "Processing & aggregation", "terms": ["billing", "invoicing", "aggregation", "invoice", "process"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q03",
        "category": "FI-CA Technical T-Codes & Document Management",
        "topic": "Document Posting, Changing, Display & FPL9",
        "query": "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the specific operational purpose of transaction FPL9?",
        "multi_parts": [
            "T-Codes to post, change, and display documents (FPE1, FPE2, FPE3)",
            "Operational purpose of FPL9"
        ],
        "key_concepts": [
            {"name": "Posting/Change/Display T-Codes", "terms": ["fpe1", "fpe2", "fpe3"]},
            {"name": "FPL9 Account Balance Display", "terms": ["fpl9", "account display", "account overview", "account balance", "line item"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q04",
        "category": "RAR Procedural Process & Prerequisites",
        "topic": "RAR Contract Reversal & POB Conditions",
        "query": "What is the end-to-end procedural process for performing a contract reversal in SAP Revenue Accounting and Reporting (RAR), and what prerequisites must be satisfied regarding Performance Obligations (POBs) before reversal?",
        "multi_parts": [
            "End-to-end procedural reversal steps",
            "POB prerequisites and status requirements"
        ],
        "key_concepts": [
            {"name": "Reversal process", "terms": ["contract", "reversal", "revenue", "accounting", "reverse"]},
            {"name": "POB prerequisites", "terms": ["pob", "performance obligation", "status", "prerequisite", "fulfilled", "invoiced"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q05",
        "category": "CC Allowance Management & State Conditions",
        "topic": "Allowance Validity & Plan Switch Rules",
        "query": "How does SAP Convergent Charging manage the validity periods and consumption counters of allowances, and what happens to an allowance balance during a subscription plan switch?",
        "multi_parts": [
            "Allowance validity periods and consumption counters",
            "Allowance treatment upon subscription plan switch"
        ],
        "key_concepts": [
            {"name": "Validity & counters", "terms": ["allowance", "validity", "counter", "start", "end", "period", "expiration"]},
            {"name": "Plan switch / carryover", "terms": ["switch", "change", "proration", "balance", "expire", "carried"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q06",
        "category": "FI-CA Dunning & Collections Procedure",
        "topic": "Dunning Levels, Activities & Proposal Execution",
        "query": "How does the dunning procedure operate in SAP FI-CA, what is the role of dunning levels and dunning activities, and which transaction is used to execute the dunning proposal run?",
        "multi_parts": [
            "Dunning procedure operation in FI-CA",
            "Role of dunning levels and dunning activities",
            "Transaction code to execute the dunning proposal run (FPVA / FPVB)"
        ],
        "key_concepts": [
            {"name": "Dunning procedure", "terms": ["dunning", "procedure", "overdue", "receivable"]},
            {"name": "Levels & activities", "terms": ["dunning level", "dunning activit", "interest", "charge", "notice", "block"]},
            {"name": "Dunning proposal T-Code", "terms": ["fpva", "fpvb", "proposal"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q07",
        "category": "CI Invoicing Configuration & Billing Cycles",
        "topic": "Billing Cycles, Grouping Variants & FI-CA Release",
        "query": "Explain how billing cycles and grouping variants control the aggregation of billable items during the billing run in SAP Convergent Invoicing, and what accounting documents are generated upon invoice release to FI-CA.",
        "multi_parts": [
            "Billing cycles and grouping variants role in BIT aggregation",
            "Accounting documents generated upon invoice release to FI-CA"
        ],
        "key_concepts": [
            {"name": "Billing cycles & grouping variants", "terms": ["billing cycle", "grouping variant", "aggregation", "billable item", "bit"]},
            {"name": "FI-CA document generation", "terms": ["fi-ca", "invoicing document", "posting", "reconciliation key", "contract account document", "gl"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q08",
        "category": "End-to-End BRIM Multi-Part Scenario Design",
        "topic": "Telecom Plan Design Across SOM, CC, CI, FI-CA",
        "query": "Design a telecom pricing scenario in SAP BRIM for a ₹499 monthly subscription with 100 GB included data, ₹10 per additional GB overage, and detail which specific BRIM components (SOM, CC, CI, FI-CA) configure and execute each part of the offer.",
        "multi_parts": [
            "₹499 monthly subscription definition",
            "100 GB included data allowance",
            "₹10 per additional GB excess rating",
            "Component mapping across SOM, CC, CI, and FI-CA"
        ],
        "key_concepts": [
            {"name": "₹499 subscription fee", "terms": ["499", "recurring", "subscription", "monthly"]},
            {"name": "100 GB allowance", "terms": ["100 gb", "allowance", "included"]},
            {"name": "₹10 overage", "terms": ["10", "overage", "additional", "usage"]},
            {"name": "Component architecture", "terms": ["som", "cc", "ci", "fi-ca"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q09",
        "category": "SOM Device-as-a-Service (DaaS) Integration",
        "topic": "Hardware Bundling & SD Logistics Flow",
        "query": "In an SAP S/4HANA BRIM Device as a Service (DaaS) scenario, how does SOM handle bundled hardware delivery alongside subscription services, and how does it interface with standard SD sales orders or delivery documents?",
        "multi_parts": [
            "Bundled hardware and subscription handling in SOM",
            "Interface with SD sales orders and delivery documents"
        ],
        "key_concepts": [
            {"name": "DaaS bundling", "terms": ["device", "daas", "hardware", "bundle", "subscription"]},
            {"name": "SD interface", "terms": ["sales order", "delivery", "sd", "logistics", "goods issue", "outbound"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q10",
        "category": "FI-CA Budget Billing & Periodic Settlement",
        "topic": "Budget Billing Plan & Periodic Settlement Cycle",
        "query": "What is the purpose of Budget Billing in SAP FI-CA, how are budget billing plans created and maintained, and how does the periodic settlement/reconciliation cycle function?",
        "multi_parts": [
            "Purpose of Budget Billing in FI-CA",
            "Creation and maintenance of budget billing plans",
            "Periodic settlement and reconciliation cycle"
        ],
        "key_concepts": [
            {"name": "Budget billing purpose", "terms": ["budget billing", "installment", "equal", "utility", "periodic", "fluctuat"]},
            {"name": "Plan creation & maintenance", "terms": ["plan", "schedule", "amount", "due date"]},
            {"name": "Settlement & reconciliation", "terms": ["settlement", "reconciliation", "final", "actual", "difference", "credit"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q11",
        "category": "Security & Authorization Architecture",
        "topic": "PFCG Roles, Fiori Catalogs & Launchpad Permissions",
        "query": "How are authorization roles structured in SAP S/4HANA BRIM using PFCG, and how do Fiori catalogs and business groups grant access to BRIM apps on the Fiori launchpad?",
        "multi_parts": [
            "Authorization role structure via PFCG",
            "Fiori catalogs and business groups on the Launchpad"
        ],
        "key_concepts": [
            {"name": "PFCG role structure", "terms": ["pfcg", "role", "authorization", "profile", "single role", "composite"]},
            {"name": "Fiori catalogs and groups", "terms": ["catalog", "business catalog", "group", "launchpad", "tile", "target mapping"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q12",
        "category": "CI Troubleshooting & Monitoring",
        "topic": "BIT Statuses & Monitoring with FKKBIX_BIT_MON",
        "query": "What are the possible status values of a Billable Item (BIT) in SAP Convergent Invoicing (such as raw, billable, billed), and how can a functional consultant analyze and resolve BIT processing errors using transaction FKKBIX_BIT_MON?",
        "multi_parts": [
            "Possible status values of a BIT (Raw, Billable, Billed)",
            "Analyzing and resolving processing errors via FKKBIX_BIT_MON"
        ],
        "key_concepts": [
            {"name": "BIT statuses", "terms": ["raw", "billable", "billed", "status", "0", "1", "2"]},
            {"name": "FKKBIX_BIT_MON analysis", "terms": ["fkkbix_bit_mon", "monitor", "error", "exception", "display", "analysis", "reverse"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q13",
        "category": "SOM ODI Infrastructure & Troubleshooting",
        "topic": "ODI Categories, Steps & Monitoring Tools",
        "query": "What is the role of Order Distribution Infrastructure (ODI) categories and steps in SAP SOM, and which transaction code or tool is used to monitor and troubleshoot failed ODI distribution steps?",
        "multi_parts": [
            "Role of ODI categories and distribution steps",
            "Monitoring and troubleshooting tool for failed ODI steps"
        ],
        "key_concepts": [
            {"name": "ODI categories and steps", "terms": ["odi", "category", "step", "distribution", "schema", "sequence"]},
            {"name": "ODI monitoring tool", "terms": ["monitor", "order distribution", "crmd_order", "crm_ui", "log", "restart"]}
        ],
        "is_negative": False,
    },
    {
        "id": "Q14",
        "category": "Negative / Unsupported Claim (No Middleware)",
        "topic": "Salesforce CPQ Native Quote Sync in CC Core Tool",
        "query": "How do you configure Salesforce CPQ native quote sync inside the SAP Convergent Charging Core Tool without any middleware or SAP Integration Suite?",
        "multi_parts": [
            "Native Salesforce CPQ configuration in CC Core Tool without middleware"
        ],
        "key_concepts": [
            {"name": "Refusal / Non-hallucination", "terms": ["not supported", "not available", "middleware", "cannot", "does not contain", "no native", "refusal", "cpi", "integration suite", "third-party"]}
        ],
        "is_negative": True,
    },
    {
        "id": "Q15",
        "category": "Negative / Unsupported Claim (Fictitious Module)",
        "topic": "Quantum Distributed Ledger in CI Module CI-QLEDGER",
        "query": "What is the configuration path in SPRO for activating quantum entangled distributed ledgers inside SAP Convergent Invoicing module CI-QLEDGER?",
        "multi_parts": [
            "Configuration path in SPRO for CI-QLEDGER quantum ledgers"
        ],
        "key_concepts": [
            {"name": "Refusal / Non-hallucination", "terms": ["not available", "does not exist", "not found", "not supported", "no such", "fictitious", "refusal", "no documentation"]}
        ],
        "is_negative": True,
    }
]


def score_response(case: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    answer = result.get("answer", "")
    citations = result.get("citations", [])
    grounding_score = float(result.get("grounding_score", 0.0))
    source_type = result.get("source_type", "")
    ans_lower = answer.lower()
    
    is_neg = case.get("is_negative", False)
    
    # -------------------------------------------------------------
    # 1. COMPLETENESS SCORE (0 - 10)
    # -------------------------------------------------------------
    multi_parts = case.get("multi_parts", [])
    key_concepts = case.get("key_concepts", [])
    
    if is_neg:
        # Negative question: Must clearly refuse or state missing/unsupported
        refusal_phrases = [
            "not available", "not supported", "does not support", "cannot be configured",
            "no native", "not possible", "middleware is required", "does not exist",
            "not mentioned", "refusal", "no documentation", "unsupported", "not present"
        ]
        has_refusal = any(p in ans_lower for p in refusal_phrases) or source_type == "refusal"
        hallucination_phrases = ["step 1: navigate to spro", "click on qledger", "select salesforce sync", "in core tool select salesforce"]
        has_hallucination = any(p in ans_lower for p in hallucination_phrases)
        
        if has_refusal and not has_hallucination:
            completeness_score = 10.0
            completeness_feedback = "Correctly refused unsupported/fictitious claim without hallucinating."
        elif has_hallucination:
            completeness_score = 0.0
            completeness_feedback = "PENALTY: Hallucinated steps for non-existent capability."
        else:
            completeness_score = 5.0
            completeness_feedback = "Partially clarified but lacked explicit refusal statement."
    else:
        # Positive question: Evaluate coverage of key concepts and multi-parts
        concepts_hit = 0
        missed_concepts = []
        for c in key_concepts:
            if any(term in ans_lower for term in c["terms"]):
                concepts_hit += 1
            else:
                missed_concepts.append(c["name"])
        
        ratio = concepts_hit / len(key_concepts) if key_concepts else 1.0
        completeness_score = round(ratio * 10.0, 1)
        completeness_feedback = f"Covered {concepts_hit}/{len(key_concepts)} concepts."
        if missed_concepts:
            completeness_feedback += f" Missed: {', '.join(missed_concepts)}"

    # -------------------------------------------------------------
    # 2. FACTUAL GROUNDING SCORE (0 - 10)
    # -------------------------------------------------------------
    if is_neg:
        # For negative test, grounding means 0 citations attached or 0.0 grounding score
        if len(citations) == 0 or grounding_score <= 0.2:
            factual_grounding = 10.0
            grounding_feedback = "Strict refusal with zero false citations."
        else:
            factual_grounding = 5.0
            grounding_feedback = f"Attached {len(citations)} citations to a negative question."
    else:
        # Verify citation alignment with claims
        if len(citations) >= 2:
            cit_quality = 1.0
        elif len(citations) == 1:
            cit_quality = 0.8
        else:
            cit_quality = 0.4
            
        base_grounding = min(1.0, max(0.0, grounding_score))
        
        # Check if citations actually support claims by matching citation snippets with concepts
        cit_text_pool = " ".join([c.get("snippet", "").lower() for c in citations])
        supported_in_citations = sum(1 for c in key_concepts if any(t in cit_text_pool for t in c["terms"]))
        cit_support_ratio = supported_in_citations / len(key_concepts) if key_concepts else 1.0
        
        factual_grounding = round((0.4 * base_grounding + 0.3 * cit_quality + 0.3 * cit_support_ratio) * 10.0, 1)
        grounding_feedback = f"{len(citations)} citations (grounding={grounding_score:.2f}, cit_support={supported_in_citations}/{len(key_concepts)})"

    # -------------------------------------------------------------
    # 3. OVERALL SCORE (0 - 10)
    # -------------------------------------------------------------
    overall_score = round(0.5 * completeness_score + 0.5 * factual_grounding, 2)
    
    failure_reasons = []
    if completeness_score < 8.0:
        failure_reasons.append(f"Incomplete sub-answers: {completeness_feedback}")
    if factual_grounding < 7.5:
        failure_reasons.append(f"Grounding or citation gap: {grounding_feedback}")
    if is_neg and completeness_score < 10.0:
        failure_reasons.append("Failed to issue unambiguous grounded refusal")

    return {
        "id": case["id"],
        "category": case["category"],
        "topic": case["topic"],
        "query": case["query"],
        "answer": answer,
        "citations_count": len(citations),
        "citations": [
            {
                "document": c.get("document") or c.get("document_name"),
                "page": c.get("page") or c.get("page_number"),
                "section": c.get("section"),
                "score": round(float(c.get("score") or c.get("rerank_score") or 0.0), 3)
            }
            for c in citations[:5]
        ],
        "completeness_score": completeness_score,
        "completeness_feedback": completeness_feedback,
        "factual_grounding_score": factual_grounding,
        "grounding_feedback": grounding_feedback,
        "overall_score": overall_score,
        "failure_reasons": failure_reasons
    }


def run_full_evaluation():
    db = SessionLocal()
    results = []
    
    print("\n" + "="*85)
    print("STARTING FINAL SAP BRIM RAG ANSWER-QUALITY EVALUATION")
    print(f"Total Test Cases: {len(EVALUATION_BENCHMARK)}")
    print("="*85 + "\n")
    
    try:
        for idx, case in enumerate(EVALUATION_BENCHMARK, start=1):
            print(f"[{idx}/{len(EVALUATION_BENCHMARK)}] Evaluating: {case['id']} - {case['topic']} ...", flush=True)
            res = rag_service.process_query(db, case["query"])
            eval_record = score_response(case, res)
            results.append(eval_record)
            
            print(f"    --> Score: {eval_record['overall_score']:.1f}/10.0 (Completeness: {eval_record['completeness_score']:.1f}, Grounding: {eval_record['factual_grounding_score']:.1f}, Citations: {eval_record['citations_count']})", flush=True)
    finally:
        db.close()
        
    # Calculate statistics
    avg_overall = sum(r["overall_score"] for r in results) / len(results)
    avg_completeness = sum(r["completeness_score"] for r in results) / len(results)
    avg_grounding = sum(r["factual_grounding_score"] for r in results) / len(results)
    
    # Sort to find the five weakest answers
    sorted_by_score = sorted(results, key=lambda x: x["overall_score"])
    five_weakest = sorted_by_score[:5]
    
    # Generate Markdown Report
    report_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scratch", "final_rag_evaluation_report.md"))
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# SAP BRIM RAG Final Answer Quality Evaluation Report\n\n")
        f.write("## Executive Summary\n\n")
        f.write(f"- **Total Evaluated Questions:** {len(results)}\n")
        f.write(f"- **Overall Mean Quality Score:** **{avg_overall:.2f} / 10.0**\n")
        f.write(f"- **Mean Completeness Score:** **{avg_completeness:.2f} / 10.0**\n")
        f.write(f"- **Mean Factual Grounding Score:** **{avg_grounding:.2f} / 10.0**\n")
        f.write(f"- **Target Score Threshold (9.0/10.0):** **{'ACHIEVED' if avg_overall >= 9.0 else 'NOT ACHIEVED'}**\n\n")
        f.write("---\n\n")
        
        f.write("## Summary Table\n\n")
        f.write("| ID | Category / Topic | Completeness | Grounding | Citations | Overall Score |\n")
        f.write("|---|---|:---:|:---:|:---:|:---:|\n")
        for r in results:
            f.write(f"| **{r['id']}** | {r['topic']} | {r['completeness_score']:.1f}/10 | {r['factual_grounding_score']:.1f}/10 | {r['citations_count']} | **{r['overall_score']:.1f}/10** |\n")
        f.write(f"| **AVERAGE** | **Entire Benchmark Evaluation Set** | **{avg_completeness:.2f}/10** | **{avg_grounding:.2f}/10** | - | **{avg_overall:.2f}/10** |\n\n")
        f.write("---\n\n")
        
        f.write("## The Five Weakest Answers & Concrete Failure Reasons\n\n")
        for idx, w in enumerate(five_weakest, start=1):
            f.write(f"### {idx}. [{w['id']}] {w['topic']} — Overall Score: {w['overall_score']:.1f}/10.0\n")
            f.write(f"- **Query:** *\"{w['query']}\"*\n")
            f.write(f"- **Completeness Score:** {w['completeness_score']:.1f}/10.0 ({w['completeness_feedback']})\n")
            f.write(f"- **Factual Grounding Score:** {w['factual_grounding_score']:.1f}/10.0 ({w['grounding_feedback']})\n")
            f.write(f"- **Identified Failure Reasons:**\n")
            if w['failure_reasons']:
                for reason in w['failure_reasons']:
                    f.write(f"  - {reason}\n")
            else:
                f.write("  - High-performing query with minimal penalties across edge-case sub-clauses.\n")
            f.write(f"- **Generated Answer Summary:**\n> {w['answer'][:400]} ...\n\n")
            f.write("---\n\n")
            
        f.write("## Detailed Question-by-Question Evaluation\n\n")
        for r in results:
            f.write(f"### [{r['id']}] {r['topic']} ({r['category']})\n\n")
            f.write(f"**Question:**\n> {r['query']}\n\n")
            f.write(f"**Scores:** Overall: **{r['overall_score']:.1f}/10.0** | Completeness: **{r['completeness_score']:.1f}/10.0** | Factual Grounding: **{r['factual_grounding_score']:.1f}/10.0**\n\n")
            f.write(f"**Feedback & Analysis:**\n- {r['completeness_feedback']}\n- {r['grounding_feedback']}\n\n")
            f.write(f"**Supporting Citations ({len(r['citations'])}):**\n")
            if r['citations']:
                for c in r['citations']:
                    f.write(f"- **{c['document']}** (Page {c['page'] or 'N/A'}, Section: `{c['section'] or 'General'}`) [Relevance: {c['score']}]\n")
            else:
                f.write("- None (Correct for refusal / negative evaluation).\n")
            f.write(f"\n**Generated Answer:**\n\n{r['answer']}\n\n")
            f.write("---\n\n")
            
    print("\n" + "="*85)
    print("EVALUATION COMPLETED SUCCESSFULLY")
    print(f"Overall Benchmark Mean Score: {avg_overall:.2f} / 10.0")
    print(f"Mean Completeness:           {avg_completeness:.2f} / 10.0")
    print(f"Mean Factual Grounding:       {avg_grounding:.2f} / 10.0")
    print(f"Report File Written to:       {report_path}")
    print("="*85 + "\n")
    
    print("THE FIVE WEAKEST ANSWERS:")
    for idx, w in enumerate(five_weakest, start=1):
        print(f"  {idx}. [{w['id']}] {w['topic']}: Score {w['overall_score']:.1f}/10.0")
        for r in w['failure_reasons']:
            print(f"     -> {r}")
    print("\n")


if __name__ == "__main__":
    run_full_evaluation()
