import os
import sys
import json
import logging
from typing import List, Dict, Any

import io

# Ensure project root is in sys.path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', write_through=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', write_through=True)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.database import SessionLocal
from backend.app.services.rag_service import rag_service
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service

logging.basicConfig(level=logging.WARNING)

BENCHMARK_CASES = [
    {
        "id": "Q1",
        "category": "Conceptual Distinction",
        "query": "What is the difference between a Price Plan and a Price Table in SAP Convergent Charging?",
        "required_entities": ["price plan", "price table", "convergent charging"],
        "key_concepts": [
            {"name": "Price Plan role", "terms": ["charging tree", "algorithm", "logic", "pricing logic", "tree"]},
            {"name": "Price Table role", "terms": ["table", "matrix", "rates", "values", "rows", "columns", "lookup"]}
        ],
        "multi_parts": ["difference", "price plan definition", "price table definition"],
        "is_negative": False,
    },
    {
        "id": "Q2",
        "category": "Architecture & Technical Role",
        "query": "What is a Price Plan in SAP Convergent Charging, and what role does it play in determining the price of a chargeable service?",
        "required_entities": ["price plan", "convergent charging", "chargeable"],
        "key_concepts": [
            {"name": "Definition", "terms": ["price plan", "charges", "charge plan"]},
            {"name": "Role in pricing", "terms": ["charging tree", "rate", "usage", "recurring", "event"]}
        ],
        "multi_parts": ["what is a price plan", "what role does it play"],
        "is_negative": False,
    },
    {
        "id": "Q3",
        "category": "Multi-Part Scenario Design",
        "query": "Design a ₹499 telecom pricing plan with 100 GB included data, ₹10 per additional GB, weekday/weekend pricing differences, and a 20% discount for the first 3 months across SAP BRIM.",
        "required_entities": ["som", "cc", "ci", "fi-ca"],
        "key_concepts": [
            {"name": "Base fee ₹499", "terms": ["499", "recurring", "base"]},
            {"name": "100 GB allowance", "terms": ["100 gb", "allowance", "included"]},
            {"name": "₹10/GB excess", "terms": ["10", "overage", "additional"]},
            {"name": "Weekday/weekend", "terms": ["weekday", "weekend", "calendar"]},
            {"name": "20% discount 3 months", "terms": ["20%", "discount", "3 months", "three months"]}
        ],
        "multi_parts": ["₹499 base", "100 gb allowance", "₹10/gb excess", "weekday/weekend", "20% discount", "brim architecture"],
        "is_negative": False,
    },
    {
        "id": "Q4",
        "category": "Procedural Process & Prerequisites",
        "query": "What is the process for RAR contract reversal in SAP, and what are the key prerequisites?",
        "required_entities": ["rar", "reversal"],
        "key_concepts": [
            {"name": "RAR contract reversal steps", "terms": ["contract", "reversal", "revenue", "accounting"]},
            {"name": "Prerequisites / POBs", "terms": ["pob", "obligation", "status", "prerequisite", "reversal"]}
        ],
        "multi_parts": ["process for contract reversal", "key prerequisites"],
        "is_negative": False,
    },
    {
        "id": "Q5",
        "category": "Integration & Replication",
        "query": "How does a Subscription Order in SOM replicate to Convergent Charging and FI-CA?",
        "required_entities": ["som", "subscription", "provider contract"],
        "key_concepts": [
            {"name": "Provider contract", "terms": ["provider contract", "contract"]},
            {"name": "Distribution mechanism", "terms": ["odi", "order distribution", "replication", "distribution"]},
            {"name": "Target components", "terms": ["convergent charging", "fi-ca", "contract accounts"]}
        ],
        "multi_parts": ["replication to CC", "replication to FI-CA"],
        "is_negative": False,
    },
    {
        "id": "Q6",
        "category": "Data Objects & Flow",
        "query": "What is the difference between a Billable Item (BIT) and a Consumption Item (CIT) in SAP Convergent Invoicing, and how are they processed?",
        "required_entities": ["billable item", "consumption item", "convergent invoicing"],
        "key_concepts": [
            {"name": "CIT definition", "terms": ["consumption item", "cit", "unrated", "rating", "raw"]},
            {"name": "BIT definition", "terms": ["billable item", "bit", "billing", "invoicing", "rated"]},
            {"name": "Processing lifecycle", "terms": ["billing", "invoicing", "process"]}
        ],
        "multi_parts": ["difference between BIT and CIT", "how they are processed"],
        "is_negative": False,
    },
    {
        "id": "Q7",
        "category": "Technical T-Codes & Functions",
        "query": "Which transaction codes are used to post, change, and display contract account documents in SAP FI-CA, and what is the function of FPL9?",
        "required_entities": ["fi-ca", "fpl9"],
        "key_concepts": [
            {"name": "Posting/Change/Display T-Codes", "terms": ["fpe1", "fpe2", "fpe3"]},
            {"name": "FPL9 function", "terms": ["account display", "account overview", "account balance", "line item", "display"]}
        ],
        "multi_parts": ["t-codes to post/change/display", "function of FPL9"],
        "is_negative": False,
    },
    {
        "id": "Q8",
        "category": "Lifecycle & State Conditions",
        "query": "How does SAP Convergent Charging handle allowance life cycles and partially consumed allowances upon plan switch?",
        "required_entities": ["allowance", "convergent charging"],
        "key_concepts": [
            {"name": "Allowance lifecycle", "terms": ["allowance", "validity", "counter", "expiry", "lifecycle"]},
            {"name": "Plan switch / partial consumption", "terms": ["switch", "change", "consumed", "carry", "proration", "balance"]}
        ],
        "multi_parts": ["allowance lifecycle", "plan switch handling"],
        "is_negative": False,
    },
    {
        "id": "Q9",
        "category": "Security & Architecture",
        "query": "How are authorization roles and Fiori catalogs structured for SAP S/4HANA BRIM users?",
        "required_entities": ["authorization", "fiori"],
        "key_concepts": [
            {"name": "PFCG Roles", "terms": ["role", "pfcg", "authorization"]},
            {"name": "Catalogs and Spaces", "terms": ["catalog", "business catalog", "group", "launchpad", "tile"]}
        ],
        "multi_parts": ["authorization roles", "fiori catalogs"],
        "is_negative": False,
    },
    {
        "id": "Q10",
        "category": "Negative / Unsupported Claim",
        "query": "How do you configure Salesforce CPQ native quote sync inside the SAP Convergent Charging Core Tool without any middleware?",
        "required_entities": ["salesforce", "core tool"],
        "key_concepts": [
            {"name": "Non-hallucination / Refusal / Middleware required", "terms": ["not supported", "not available", "middleware", "cannot", "does not contain", "no native", "refusal", "pi", "cpi", "integration"]}
        ],
        "multi_parts": ["Salesforce CPQ in CC Core Tool without middleware"],
        "is_negative": True,
    }
]

def evaluate_response(case: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    answer = result.get("answer", "")
    citations = result.get("citations", [])
    grounding_score = result.get("grounding_score", 0.0)
    source_type = result.get("source_type", "")
    ans_lower = answer.lower()
    
    score = 0.0
    feedback = []
    
    # Negative question handling
    if case["is_negative"]:
        # Should decline or clearly state native direct sync without middleware is unsupported
        has_negative_indicator = any(term in ans_lower for term in [
            "not supported", "not available", "no native", "cannot", "middleware is required",
            "not possible", "does not support", "without middleware is not", "requires middleware"
        ]) or source_type in ("refusal", "insufficient")
        
        if has_negative_indicator:
            score += 10.0
            feedback.append("Correctly identified unsupported / non-existent capability without hallucinating.")
        else:
            score = 2.0
            feedback.append("Hallucinated or fabricated configuration steps for non-existent native integration.")
            
        return {
            "score": round(score, 1),
            "feedback": feedback,
            "citations_count": len(citations),
            "grounding_score": grounding_score
        }

    # Standard questions (max 10 points):
    # 1. Grounding and Citations (2.0 pts)
    if citations and len(citations) >= 1:
        score += 2.0
        feedback.append(f"Citations present ({len(citations)} citations, score={grounding_score})")
    elif source_type == "web" and result.get("web_sources"):
        score += 1.5
        feedback.append("Web citations present")
    else:
        score += 0.5
        feedback.append("No citations aligned")

    # 2. Required Entities Preservation (2.0 pts)
    req_count = sum(1 for e in case["required_entities"] if e in ans_lower)
    entity_score = (req_count / len(case["required_entities"])) * 2.0
    score += entity_score
    feedback.append(f"Entity preservation: {req_count}/{len(case['required_entities'])} entities")

    # 3. Key Concepts Depth & Accuracy (4.0 pts)
    concept_score = 0.0
    points_per_concept = 4.0 / len(case["key_concepts"])
    for kc in case["key_concepts"]:
        matched = any(t in ans_lower for t in kc["terms"])
        if matched:
            concept_score += points_per_concept
            feedback.append(f"Concept '{kc['name']}' covered")
        else:
            feedback.append(f"Concept '{kc['name']}' MISSING")
    score += concept_score

    # 4. Multi-part Question Coverage (2.0 pts)
    points_per_part = 2.0 / len(case["multi_parts"])
    part_score = 0.0
    for mp in case["multi_parts"]:
        part_terms = [t for t in mp.split() if len(t) > 3]
        if any(pt in ans_lower for pt in part_terms):
            part_score += points_per_part
        else:
            feedback.append(f"Multi-part facet '{mp}' partially covered")
    score += min(part_score, 2.0)

    # Sanity checks
    if "pricing specialist" in ans_lower[:250] and case["id"] in ("Q1", "Q2"):
        score = max(0.0, score - 1.0)
        feedback.append("Penalty: opened with role duties rather than concept definition")
        
    return {
        "score": round(min(10.0, max(0.0, score)), 1),
        "feedback": feedback,
        "citations_count": len(citations),
        "grounding_score": grounding_score
    }

def run_benchmark():
    db = SessionLocal()
    total_score = 0.0
    results_summary = []
    
    print("\n" + "="*80)
    print("EXECUTING SAP BRIM RAG RETRIEVAL QUALITY BENCHMARK (10 EVALUATION QUESTIONS)")
    print("="*80 + "\n")
    
    try:
        for idx, case in enumerate(BENCHMARK_CASES, start=1):
            print(f"[{case['id']}] {case['category']}: '{case['query'][:65]}...'")
            res = rag_service.process_query(db, case["query"])
            eval_res = evaluate_response(case, res)
            score = eval_res["score"]
            total_score += score
            
            print(f"     Score: {score}/10.0 | Grounding: {eval_res['grounding_score']} | Citations: {eval_res['citations_count']}")
            print(f"     Feedback: {'; '.join(eval_res['feedback'][:3])}")
            print("-" * 80)
            
            results_summary.append({
                "id": case["id"],
                "category": case["category"],
                "score": score,
                "grounding_score": eval_res["grounding_score"],
                "citations_count": eval_res["citations_count"],
                "feedback": eval_res["feedback"]
            })
            
        mean_score = total_score / len(BENCHMARK_CASES)
        print("\n" + "="*80)
        print(f"OVERALL BENCHMARK RESULT: {mean_score:.2f} / 10.0")
        print(f"TARGET QUALITY THRESHOLD: 9.00 / 10.0")
        print("="*80 + "\n")
        return mean_score, results_summary
    finally:
        db.close()

if __name__ == "__main__":
    run_benchmark()
