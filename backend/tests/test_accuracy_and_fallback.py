import json
import pytest
from unittest.mock import MagicMock, patch
from backend.app.database import SessionLocal
from backend.app.config import settings
from backend.app.services.rag_service import rag_service
from backend.app.services.grounding_service import grounding_service


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def mock_web_results():
    return [
        {
            "title": "SAP Help Portal - Allowance Management in Convergent Charging",
            "url": "https://help.sap.com/docs/cc-allowances",
            "domain": "help.sap.com",
            "snippet": "In SAP Convergent Charging, when a subscription is switched mid-cycle, partially-consumed allowances are either expired or prorated based on the allowance logic component configuration.",
            "score": 0.94
        }
    ]


def test_regression_allowance_product_switch_triggers_tavily(db_session, mock_web_results):
    """
    Priority 1 & 3: Regression test for the partially-consumed allowance/product-switch query.
    ColBERT scores retrieved CC chunks highly on general terms, but operative scenario/facts
    (subscription switch, partially-consumed allowance) are missing from internal evidence.
    Evidence sufficiency check must evaluate to False and trigger Tavily fallback.
    """
    query = "How does SAP Convergent Charging treat a partially-consumed allowance when a subscription is switched to a different product or pricing configuration within the same billing period?"

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_web_results) as mock_search, \
         patch("backend.app.services.llm_service.llm_service.generate_answer", return_value="Based on SAP documentation, partially-consumed allowances can be expired or prorated upon subscription switch.") as mock_llm:

        prep = rag_service.prepare_context_and_sources(db_session, query)
        assert prep["status"] == "ready"
        assert prep["source_type"] == "web"
        assert prep["web_evidence"] is not None
        assert len(prep["web_evidence"]) == 1
        assert mock_search.call_count == 1

        mock_search.reset_mock()
        result = rag_service.process_query(db_session, query)
        assert result["source_type"] == "web"
        assert len(result["web_sources"]) > 0
        assert "help.sap.com" in result["web_sources"][0]["domain"]
        assert mock_search.call_count == 1  # Called at most once within process_query


def test_sufficient_internal_evidence_skips_tavily(db_session):
    """
    Priority 1 & 3: Sufficient internal evidence covering topic and required facts
    skips Tavily search completely.
    """
    query = "What is the T-Code to display FICA documents?"

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative") as mock_search:
        prep = rag_service.prepare_context_and_sources(db_session, query)
        assert prep["status"] == "ready"
        assert prep["source_type"] == "knowledge_base"
        assert prep["web_evidence"] is None
        assert mock_search.call_count == 0


def test_adaptive_fallback_on_explicit_refusal(db_session, mock_web_results):
    """
    Priority 1: If internal evidence was preliminarily deemed sufficient, but internal
    LLM answer generation produces the explicit refusal, allow one adaptive fallback
    attempt to Tavily.
    """
    query = "How does SAP Convergent Invoicing aggregate billable items?"

    mock_prep = {
        "status": "ready",
        "source_type": "knowledge_base",
        "evidence": [{"chunk_text": "CI BIT aggregation info", "document_name": "CI Guide.pdf", "page_number": 1, "section": "BIT Aggregation", "rerank_score": 0.85}],
        "internal_evidence": [{"chunk_text": "CI BIT aggregation info", "document_name": "CI Guide.pdf", "page_number": 1, "section": "BIT Aggregation", "rerank_score": 0.85}],
        "web_evidence": None,
        "candidate_citations": [],
        "candidate_web_sources": [],
        "chat_history": [],
        "grounding_score": 0.85,
        "web_attempted": False
    }

    # First LLM call produces explicit refusal, second (fallback with web) answers
    llm_side_effects = [
        "The information is not available in the provided knowledge base.",
        "According to SAP Help Portal, billable items are aggregated using billing cycles and grouping variants."
    ]

    with patch.object(rag_service, "prepare_context_and_sources", return_value=mock_prep), \
         patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_web_results) as mock_search, \
         patch("backend.app.services.llm_service.llm_service.generate_answer", side_effect=llm_side_effects) as mock_llm:

        result = rag_service.process_query(db_session, query)
        assert result["source_type"] == "web"
        assert "billing cycles" in result["answer"]
        assert mock_search.call_count == 1
        assert mock_llm.call_count == 2


def test_tavily_attempted_at_most_once(db_session):
    """
    Priority 1 & 3: Tavily search must be attempted at most once per query, preventing
    retry loops even when adaptive fallback fails.
    """
    query = "What is an unknown SAP custom module XYZ999?"

    mock_prep = {
        "status": "ready",
        "source_type": "knowledge_base",
        "evidence": [{"chunk_text": "Some text", "document_name": "SAP.pdf", "page_number": 1, "section": "General", "rerank_score": 0.70}],
        "internal_evidence": [{"chunk_text": "Some text", "document_name": "SAP.pdf", "page_number": 1, "section": "General", "rerank_score": 0.70}],
        "web_evidence": None,
        "candidate_citations": [],
        "candidate_web_sources": [],
        "chat_history": [],
        "grounding_score": 0.70,
        "web_attempted": False
    }

    refusal = "The information is not available in the provided knowledge base."

    with patch.object(rag_service, "prepare_context_and_sources", return_value=mock_prep), \
         patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=[]) as mock_search, \
         patch("backend.app.services.llm_service.llm_service.generate_answer", return_value=refusal) as mock_llm:

        result = rag_service.process_query(db_session, query)
        # Search attempted exactly once, no retry loops
        assert mock_search.call_count == 1
        assert grounding_service.is_refusal(result["answer"])
        assert result["citations"] == []
        assert result["web_sources"] == []
        assert result["grounding_score"] == 0.0


def test_failure_or_insufficient_web_evidence_results_in_strict_refusal(db_session):
    """
    Priority 1 & 3: When neither internal KB nor web evidence supports an answer,
    strict refusal is preserved without hallucinated citations or content.
    """
    query = "How to configure unsupported quantum entangled billing in SAP?"

    refusal_answer = "The information is not available in the provided knowledge base."
    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=[]) as mock_search, \
         patch("backend.app.services.llm_service.llm_service.generate_answer", return_value=refusal_answer):
        result = rag_service.process_query(db_session, query)
        assert grounding_service.is_refusal(result["answer"])
        assert result["grounding_score"] == 0.0
        assert result["citations"] == []
        assert result["web_sources"] == []


def test_unsupported_answers_do_not_receive_default_citations():
    """
    Priority 2 & 3: Verify that unconditional fallback attaching chunks[0] is removed.
    Answers without claim support or with explicit refusal must receive 0 citations.
    """
    chunks = [
        {
            "chunk_text": "SAP Convergent Charging manages allowances, subscriptions, and charge plans.",
            "document_name": "CC_Overview.pdf",
            "page_number": 5,
            "section": "Core Architecture",
            "rerank_score": 0.90
        }
    ]

    # Case A: Explicit refusal answer
    refusal_answer = "The information is not available in the provided knowledge base."
    aligned_refusal = grounding_service.align_citations(refusal_answer, chunks)
    assert aligned_refusal == [], "Refusal answers must never receive default or fallback citations"

    # Case B: Unsupported answer that does not match chunk text, document name, or section
    unsupported_answer = "The user should run transaction /N/XYZ123 on the mainframe terminal to reboot."
    aligned_unsupported = grounding_service.align_citations(unsupported_answer, chunks)
    assert aligned_unsupported == [], "Answers without claim support must never receive default chunks[0]"


def test_streaming_and_non_streaming_behavior_consistency(db_session, mock_web_results):
    """
    Priority 1 & 3: Verify streaming (stream_query) and non-streaming (process_query)
    produce consistent responses and handle adaptive fallback without leaking refusal tokens.
    """
    query = "How does SAP handle partially-consumed allowances during subscription changes?"

    mock_prep = {
        "status": "ready",
        "source_type": "knowledge_base",
        "evidence": [{"chunk_text": "CC general text", "document_name": "CC.pdf", "page_number": 1, "section": "General", "rerank_score": 0.75}],
        "internal_evidence": [{"chunk_text": "CC general text", "document_name": "CC.pdf", "page_number": 1, "section": "General", "rerank_score": 0.75}],
        "web_evidence": None,
        "candidate_citations": [],
        "candidate_web_sources": [],
        "chat_history": [],
        "grounding_score": 0.75,
        "web_attempted": False
    }

    # Simulate stream_answer: first call yields refusal tokens, second yields web answer tokens
    def fake_stream_answer(query, evidence, source_type, chat_history, web_evidence=None):
        if source_type == "knowledge_base":
            for tok in ["The ", "information ", "is ", "not ", "available ", "in ", "the ", "provided ", "knowledge ", "base."]:
                yield tok
        else:
            for tok in ["According ", "to ", "SAP ", "Help, ", "partially-consumed ", "allowances ", "expire."]:
                yield tok

    with patch.object(rag_service, "prepare_context_and_sources", return_value=mock_prep), \
         patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_web_results) as mock_search, \
         patch("backend.app.services.llm_service.llm_service.stream_answer", side_effect=fake_stream_answer):

        events = list(rag_service.stream_query(db_session, query, chat_id="test-chat-stream"))

        # Verify SSE event types and extracted tokens
        event_types = []
        token_contents = []
        for block in events:
            for line in block.split("\n"):
                if line.startswith("event: "):
                    event_types.append(line.replace("event: ", "").strip())
                elif line.startswith("data: "):
                    data_str = line.replace("data: ", "").strip()
                    try:
                        data_json = json.loads(data_str)
                        if "token" in data_json:
                            token_contents.append(data_json["token"])
                    except Exception:
                        pass

        assert "metadata" in event_types
        assert "token" in event_types
        assert "done" in event_types

        # Verify that refusal tokens were NOT leaked to client before web fallback
        combined_streamed_text = "".join(token_contents)
        assert "The information is not available" not in combined_streamed_text
        assert "According to SAP Help" in combined_streamed_text
        assert mock_search.call_count == 1
