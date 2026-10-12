import pytest
from backend.app.database import SessionLocal
from backend.app.services.grounding_service import grounding_service
from backend.app.services.context_builder import context_builder
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.rag_service import rag_service
from backend.app.services.prompts import build_brim_user_prompt, SAP_BRIM_SYSTEM_PROMPT


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_scenario_design_with_user_pricing_values():
    """
    1. Scenario-design question with user-provided pricing values.
    Verifies that user-supplied numerical/commercial inputs (₹499, 100 GB, 3 months, 20% discount)
    do not trigger zero-coverage refusals when relevant SAP CC architectural evidence is present.
    """
    query = (
        "Design a mobile postpaid plan with a INR 499 monthly fee, 100 GB bundled high-speed data, "
        "INR 10 per additional GB, weekday and weekend pricing, and a 20% promotional discount for the first 3 months in SAP CC."
    )
    
    # Representative architectural evidence from SAP CC (charge plan, recurring charge, usage charge, allowances)
    cc_evidence = [
        {
            "chunk_id": "cc-chk-01",
            "document_name": "SAP CC.pdf",
            "section": "Charge Plans and Pricing Logic",
            "page_number": 12,
            "rerank_score": 0.945,
            "chunk_text": (
                "A Charge Plan in SAP Convergent Charging defines the combination of recurring charges, "
                "one-off charges, and usage-based pricing components. Recurring rates are triggered by a schedule "
                "or cyclic period, such as a monthly subscription fee. Usage rates evaluate consumption events."
            )
        },
        {
            "chunk_id": "cc-chk-02",
            "document_name": "SAP CC.pdf",
            "section": "Allowances and Overage Charges",
            "page_number": 28,
            "rerank_score": 0.920,
            "chunk_text": (
                "Allowances define credited quantities such as bundled data volumes (e.g., free gigabytes). "
                "When the allowance balance is exhausted, the price plan logic branches into overage tiers, "
                "applying an incremental rate per unit of additional volume consumed."
            )
        }
    ]

    is_sufficient, reason = grounding_service.evaluate_evidence_sufficiency(query, cc_evidence)
    assert is_sufficient is True, f"Scenario query was incorrectly marked insufficient: {reason}"

    grounding_score = grounding_service.calculate_evidence_grounding_score(query, cc_evidence, is_sufficient)
    assert grounding_score >= 0.60


def test_multipart_question_spans_multiple_brim_components(db):
    """
    2. Multi-part question spanning different BRIM components.
    Verifies that multi-part queries retrieve candidates for distinct components (e.g. SOM + CI)
    and that sub-facet hybrid retrieval enriches the candidate pool before reranking.
    """
    multipart_query = (
        "Explain how a subscription order in Subscription Order Management (SOM) "
        "replicates to Convergent Invoicing (CI) to create billable items."
    )

    reranked, has_sufficient = retrieval_service.retrieve_and_rerank(db, multipart_query, final_k=8)
    assert len(reranked) > 0, "Retrieve and rerank should return candidates for multi-part query"

    doc_names = [c.get("document_name", "") for c in reranked]
    combined_text = " ".join([c.get("chunk_text", "") for c in reranked]).lower()

    # Verify both SOM and CI concepts are present in retrieved chunks
    has_som_concept = any("som" in d.lower() or "order" in d.lower() for d in doc_names) or ("subscription" in combined_text or "order" in combined_text)
    has_ci_concept = any("ci" in d.lower() or "billing" in d.lower() for d in doc_names) or ("billable" in combined_text or "invoicing" in combined_text)

    assert has_som_concept, f"SOM evidence missing from retrieved chunks: {doc_names}"
    assert has_ci_concept, f"CI evidence missing from retrieved chunks: {doc_names}"


def test_detailed_query_with_relevant_evidence(db):
    """
    3. A detailed query with relevant evidence.
    Verifies that high-relevance domain questions produce sufficient evidence and positive grounding.
    """
    query = "What is a Chargeable Item in SAP Convergent Charging and how is it processed during rating?"

    reranked, has_sufficient = retrieval_service.retrieve_and_rerank(db, query, final_k=5)
    assert len(reranked) > 0

    top_chunk = reranked[0]
    assert top_chunk.get("rerank_score", 0.0) >= 0.60

    is_sufficient, reason = grounding_service.evaluate_evidence_sufficiency(query, reranked)
    assert is_sufficient is True
    grounding_score = grounding_service.calculate_evidence_grounding_score(query, reranked, is_sufficient)
    assert grounding_score >= 0.65


def test_partial_evidence_answering_without_hallucinating():
    """
    4. A question with partial evidence.
    Ensures supported parts can be answered without inventing missing facts,
    and prompt contract directs the LLM to identify gaps explicitly.
    """
    query = (
        "Explain how allowance packages work in SAP CC, and configure 5G network slicing "
        "quality of service parameters in Convergent Mediation."
    )
    
    # Evidence only contains SAP CC allowance documentation; nothing on 5G network slicing in CM
    partial_evidence = [
        {
            "chunk_id": "cc-allowance-01",
            "document_name": "SAP CC.pdf",
            "section": "Allowance Plans",
            "page_number": 44,
            "rerank_score": 0.95,
            "chunk_text": (
                "Allowance logic in SAP Convergent Charging allows service providers to configure shared "
                "or individual usage allowances. An Allowance Plan defines the validity period, initial quantity, "
                "and deduction rules when chargeable events occur."
            )
        }
    ]

    is_sufficient, reason = grounding_service.evaluate_evidence_sufficiency(query, partial_evidence)
    # The system should recognize relevant evidence is available for the supported portion
    assert is_sufficient is True, f"Supported portion should allow answering, got reason: {reason}"

    # Context and user prompt should instruct LLM to answer supported parts and isolate missing parts
    user_prompt = build_brim_user_prompt(
        query=query,
        structured_context=context_builder.build_context(partial_evidence)
    )
    assert "If evidence is partial, answer what it supports" in user_prompt
    assert "Allowance Plan defines the validity period" in user_prompt


def test_no_relevant_evidence_triggers_refusal():
    """
    5. A question with no relevant evidence.
    Ensures the system does not hallucinate when evidence is absent or completely below threshold.
    """
    query = "How do you configure custom transaction code Z_NONEXISTENT_QUANTUM_BILLING_9999?"
    
    # Empty evidence
    no_evidence = []
    is_sufficient, reason = grounding_service.evaluate_evidence_sufficiency(query, no_evidence)
    assert is_sufficient is False
    score = grounding_service.calculate_evidence_grounding_score(query, no_evidence, is_sufficient)
    assert score == 0.0

    # Irrelevant chunks with low rerank scores
    low_evidence = [
        {
            "chunk_id": "irrel-01",
            "document_name": "SAP CC.pdf",
            "section": "Overview",
            "page_number": 1,
            "rerank_score": 0.25,
            "chunk_text": "This document introduces introductory principles of software architecture."
        }
    ]
    is_low_suff, low_reason = grounding_service.evaluate_evidence_sufficiency(query, low_evidence, threshold=0.60)
    assert is_low_suff is False
    low_score = grounding_service.calculate_evidence_grounding_score(query, low_evidence, is_low_suff, threshold=0.60)
    assert low_score == 0.0


def test_citation_integrity_and_multi_document_context_selection():
    """
    6. Citation integrity and context selection across multiple documents.
    Verifies that:
    - Context builder limits single-document domination when multiple relevant documents exist.
    - Chunks from secondary documents are preserved.
    - Citation headers are clean, consistent, and free of filepath leaks.
    """
    multi_doc_evidence = [
        # Document A: 4 high-scoring chunks
        {"chunk_id": "docA-1", "document_name": "SAP CC.pdf", "page_number": 10, "section": "Rating", "rerank_score": 0.96, "chunk_text": "Doc A Chunk 1 text on rating logic."},
        {"chunk_id": "docA-2", "document_name": "SAP CC.pdf", "page_number": 11, "section": "Rating", "rerank_score": 0.95, "chunk_text": "Doc A Chunk 2 text on rating logic."},
        {"chunk_id": "docA-3", "document_name": "SAP CC.pdf", "page_number": 12, "section": "Rating", "rerank_score": 0.94, "chunk_text": "Doc A Chunk 3 text on rating logic."},
        {"chunk_id": "docA-4", "document_name": "SAP CC.pdf", "page_number": 13, "section": "Rating", "rerank_score": 0.93, "chunk_text": "Doc A Chunk 4 text on rating logic."},
        # Document B: 2 relevant chunks
        {"chunk_id": "docB-1", "document_name": "SAP CI1.pdf", "page_number": 22, "section": "Billing", "rerank_score": 0.92, "chunk_text": "Doc B Chunk 1 text on billing process."},
        {"chunk_id": "docB-2", "document_name": "SAP CI1.pdf", "page_number": 23, "section": "Billing", "rerank_score": 0.91, "chunk_text": "Doc B Chunk 2 text on billing process."}
    ]

    # Limit context to 5 chunks total
    context = context_builder.build_context(multi_doc_evidence, max_chunks=5)

    # Document A should not take all 5 slots; Document B should be included
    assert "Document: SAP CC.pdf" in context
    assert "Document: SAP CI1.pdf" in context
    assert "Doc B Chunk 1 text on billing process." in context

    # Verify absence of local filesystem path leaks
    assert "C:\\" not in context
    assert "/Users/" not in context
    assert "file:///" not in context
