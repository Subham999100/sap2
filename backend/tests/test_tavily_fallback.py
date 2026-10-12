import pytest
from unittest.mock import MagicMock, patch
from backend.app.database import SessionLocal
from backend.app.services.rag_service import rag_service
from backend.app.services.web_search_service import WebSearchService
from backend.app.services.context_builder import context_builder
from backend.app.services.llm_service import llm_service
from backend.app.services.grounding_service import grounding_service


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def mock_tavily_results():
    return [
        {
            "title": "SAP Help Portal - Sourcing Integration",
            "url": "https://help.sap.com/docs/ariba-integration",
            "domain": "help.sap.com",
            "snippet": "SAP Ariba integrates with S/4HANA using standard Web Services and Cloud Integration.",
            "score": 0.92
        },
        {
            "title": "SAP Community - Supplier Sourcing Setup",
            "url": "https://community.sap.com/blogs/ariba-sourcing",
            "domain": "community.sap.com",
            "snippet": "Configuration steps for setting up supplier sourcing between Ariba and S/4HANA.",
            "score": 0.88
        }
    ]


def test_1_sufficient_internal_evidence_does_not_call_tavily(db_session):
    """1: When internal evidence is sufficient (score >= threshold), Tavily search is NEVER called."""
    query = "What is a Price Plan in SAP Convergent Charging?"

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative") as mock_search:
        prep = rag_service.prepare_context_and_sources(db_session, query)
        assert prep["status"] == "ready"
        assert prep["source_type"] == "knowledge_base"
        assert len(prep["evidence"]) > 0
        assert prep["web_evidence"] is None
        assert mock_search.call_count == 0, "Tavily should not be called when internal evidence is sufficient"

        ctx = context_builder.build_context(
            evidence=prep["evidence"],
            source_type=prep["source_type"],
            web_evidence=prep["web_evidence"]
        )
        assert "=== INTERNAL SAP KNOWLEDGE BASE ===" in ctx
        assert "=== FALLBACK WEB EVIDENCE ===" not in ctx
        assert "WEB FALLBACK EVIDENCE" not in ctx


def test_2_insufficient_internal_evidence_calls_tavily(db_session, mock_tavily_results):
    """2: When internal evidence is insufficient (e.g. topic not in local manuals), Tavily search IS called."""
    query = "How does SAP integrate with Celonis for execution management and process mining?"

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_tavily_results) as mock_search:
        prep = rag_service.prepare_context_and_sources(db_session, query)
        assert prep["status"] == "ready"
        assert prep["source_type"] == "web"
        assert len(prep["web_evidence"]) == 2
        # Internal KB evidence is retained and not discarded
        assert len(prep["internal_evidence"]) == len(prep["evidence"])
        mock_search.assert_called_once_with(query)

        ctx = context_builder.build_context(
            evidence=prep["internal_evidence"],
            source_type=prep["source_type"],
            web_evidence=prep["web_evidence"]
        )
        assert "=== INTERNAL SAP KNOWLEDGE BASE ===" in ctx
        assert "=== FALLBACK WEB EVIDENCE ===" in ctx


def test_3_tavily_results_converted_into_structured_web_evidence():
    """3: Raw Tavily responses are converted into clean, structured dictionaries with official domains."""
    mock_raw_response = {
        "results": [
            {
                "title": "<b>SAP Learning Journey</b>",
                "url": "https://learning.sap.com/courses/brim100",
                "content": "<p>Overview of SAP BRIM solution architecture and charging.</p>",
                "score": 0.95
            },
            {
                "title": "Non SAP Blog",
                "url": "https://unauthorized-third-party.com/sap-tips",
                "content": "Random tips.",
                "score": 0.30
            }
        ]
    }

    service = WebSearchService(api_key="test-key")
    service.client = MagicMock()
    service.client.search.return_value = mock_raw_response

    results = service.search_sap_authoritative("BRIM Architecture")
    assert len(results) == 1
    item = results[0]
    assert item["title"] == "SAP Learning Journey"
    assert item["domain"] == "learning.sap.com"
    assert item["url"] == "https://learning.sap.com/courses/brim100"
    assert "Overview of SAP BRIM" in item["snippet"]
    assert "<p>" not in item["snippet"]
    assert item["score"] == 0.95


def test_4_internal_and_web_evidence_clearly_separated_in_context():
    """4: Internal and web fallback evidence are formatted into clearly separated sections."""
    internal_chunks = [
        {
            "chunk_id": "c1",
            "document_name": "SAP CC.pdf",
            "page_number": 10,
            "section": "Overview",
            "rerank_score": 0.45,
            "chunk_text": "Internal partial evidence."
        }
    ]
    web_evidence = [
        {
            "title": "SAP Help Portal",
            "url": "https://help.sap.com/docs/ref",
            "domain": "help.sap.com",
            "snippet": "Web fallback evidence snippet."
        }
    ]

    ctx = context_builder.build_context(
        evidence=internal_chunks,
        source_type="web",
        web_evidence=web_evidence
    )

    assert "=== INTERNAL SAP KNOWLEDGE BASE ===" in ctx
    assert "INTERNAL KNOWLEDGE BASE EVIDENCE:" in ctx
    assert "END INTERNAL KNOWLEDGE BASE EVIDENCE" in ctx
    assert "=== FALLBACK WEB EVIDENCE ===" in ctx
    assert "WEB FALLBACK EVIDENCE:" in ctx
    assert "END WEB FALLBACK EVIDENCE" in ctx

    # Internal chunk appears inside internal section
    internal_part = ctx.split("END INTERNAL KNOWLEDGE BASE EVIDENCE")[0]
    assert "Internal partial evidence." in internal_part
    assert "Web fallback evidence snippet." not in internal_part

    # Web chunk appears inside web section
    web_part = ctx.split("WEB FALLBACK EVIDENCE:")[1].split("END WEB FALLBACK EVIDENCE")[0]
    assert "Web fallback evidence snippet." in web_part
    assert "Internal partial evidence." not in web_part


def test_5_tavily_results_bounded_by_context_budget():
    """5: Web evidence respects maximum chunk and character limits in ContextBuilder."""
    large_web_evidence = [
        {
            "title": f"SAP Doc {i}",
            "url": f"https://help.sap.com/doc/{i}",
            "domain": "help.sap.com",
            "snippet": f"Content snippet {i} " + ("X" * 1000)
        }
        for i in range(10)
    ]

    ctx = context_builder.build_context(
        evidence=[],
        source_type="web",
        web_evidence=large_web_evidence,
        max_chunks=2,
        max_chunk_chars=100,
        max_total_chars=800
    )

    assert "SOURCE 1" in ctx
    assert "SOURCE 2" in ctx
    assert "SOURCE 3" not in ctx
    assert "[truncated for context budget]" in ctx


def test_6_tavily_failure_does_not_crash_request(db_session):
    """6: Network timeouts or Tavily API exceptions do not crash RAG preparation."""
    query = "How does SAP integrate with Celonis for execution management and process mining?"

    with patch("backend.app.services.web_search_service.web_search_service.client") as mock_client:
        mock_client.search.side_effect = RuntimeError("Tavily API timeout / connection error")

        # Must not raise an exception
        prep = rag_service.prepare_context_and_sources(db_session, query)
        assert prep["status"] == "insufficient"
        assert "not available in the provided knowledge base or fallback web evidence" in prep["answer"]
        assert prep["grounding_score"] == 0.0


def test_7_tavily_empty_results_returns_explicit_unavailable_message(db_session):
    """7: When both internal RAG and Tavily return no useful information, answer is explicitly unavailable."""
    query = "How does SAP integrate with Celonis for execution management and process mining?"

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=[]):
        prep = rag_service.prepare_context_and_sources(db_session, query)
        assert prep["status"] == "insufficient"
        assert prep["answer"] == "The information is not available in the provided knowledge base or fallback web evidence."
        assert prep["grounding_score"] == 0.0
        assert prep["citations"] == []
        assert prep["web_sources"] == []


def test_8_prompt_contains_web_evidence_only_when_fallback_triggered():
    """8: The constructed prompt includes web fallback evidence only when fallback is active."""
    internal_chunks = [{"chunk_id": "c1", "document_name": "SAP CC.pdf", "chunk_text": "CC Text."}]
    web_chunks = [{"title": "Web Title", "url": "https://help.sap.com", "snippet": "Web Snippet."}]

    # Case A: Normal internal RAG
    prompt_normal = llm_service._build_user_prompt(
        query="What is CC?",
        evidence=internal_chunks,
        source_type="knowledge_base",
        business_data=None,
        chat_history=None,
        web_evidence=None
    )
    assert "=== INTERNAL SAP KNOWLEDGE BASE ===" in prompt_normal
    assert "=== FALLBACK WEB EVIDENCE ===" not in prompt_normal
    assert "WEB FALLBACK EVIDENCE" not in prompt_normal
    assert "KNOWLEDGE EVIDENCE:" in prompt_normal

    # Case B: Web fallback active
    prompt_fallback = llm_service._build_user_prompt(
        query="Explain Ariba",
        evidence=internal_chunks,
        source_type="web",
        business_data=None,
        chat_history=None,
        web_evidence=web_chunks
    )
    assert "=== INTERNAL SAP KNOWLEDGE BASE ===" in prompt_fallback
    assert "=== FALLBACK WEB EVIDENCE ===" in prompt_fallback
    assert "INTERNAL KNOWLEDGE BASE EVIDENCE:" in prompt_fallback
    assert "WEB FALLBACK EVIDENCE:" in prompt_fallback
    assert "Web Snippet." in prompt_fallback


def test_9_non_streaming_generation_uses_fallback(db_session, mock_tavily_results):
    """9: Synchronous process_query() executes fallback and aligns web sources when internal evidence is insufficient."""
    query = "How does SAP integrate with Celonis for execution management and process mining?"

    mock_llm_answer = (
        "SAP integrates with Celonis for execution management and process mining "
        "as described in authoritative SAP documentation."
    )

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_tavily_results), \
         patch("backend.app.services.llm_service.llm_service.generate_answer", return_value=mock_llm_answer):

        result = rag_service.process_query(db_session, query)
        assert result["source_type"] == "web"
        assert result["answer"] == mock_llm_answer
        assert len(result["web_sources"]) > 0
        assert any("help.sap.com" in w["domain"] for w in result["web_sources"])
        assert result["grounding_score"] > 0.0


def test_10_streaming_generation_uses_fallback(db_session, mock_tavily_results):
    """10: stream_query() yields proper SSE events including web metadata when fallback is active."""
    query = "How does SAP integrate with Celonis for execution management and process mining?"

    mock_tokens = ["SAP ", "Celonis ", "integrates ", "via ", "process ", "mining."]

    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_tavily_results), \
         patch("backend.app.services.llm_service.llm_service.stream_answer", return_value=iter(mock_tokens)):

        events = list(rag_service.stream_query(db_session, query, chat_id=None))
        assert len(events) >= 3

        # Check metadata events
        metadata_events = [e for e in events if "event: metadata" in e]
        assert len(metadata_events) >= 1
        assert '"source_type": "web"' in metadata_events[0]

        # Check token streaming
        token_events = [e for e in events if "event: token" in e]
        assert len(token_events) == len(mock_tokens)

        # Check done event
        assert "event: done" in events[-1]


def test_11_web_citations_only_attached_when_actually_used():
    """11: align_web_sources attaches only sources with substantive terms appearing in the answer."""
    web_sources = [
        {
            "title": "SAP Ariba Cloud Integration Guide",
            "url": "https://help.sap.com/ariba-cloud",
            "domain": "help.sap.com",
            "snippet": "Covers middleware configuration, cXML endpoints, and authentication tokens."
        },
        {
            "title": "SAP Concur Expense Automation",
            "url": "https://help.sap.com/concur-expense",
            "domain": "help.sap.com",
            "snippet": "Expense receipt scanning and automated audit rules."
        }
    ]

    answer = "The integration uses cXML endpoints and middleware configuration."
    aligned = grounding_service.align_web_sources(answer, web_sources)
    assert len(aligned) == 1
    assert aligned[0]["title"] == "SAP Ariba Cloud Integration Guide"
    assert "concur" not in aligned[0]["url"]


def test_12_both_sources_insufficient_yields_zero_grounding_and_no_hallucination():
    """12: Refusal / unavailable statement yields 0 citations and 0 grounding score."""
    answer = "The information is not available in the provided knowledge base or fallback web evidence."
    score = grounding_service.evaluate_grounding("test", answer, [], source_type="refusal")
    assert score == 0.0

    aligned_cits = grounding_service.align_citations(answer, [{"chunk_text": "Sample"}])
    assert aligned_cits == []

    aligned_web = grounding_service.align_web_sources(answer, [{"snippet": "Sample"}])
    assert aligned_web == []


def test_13_no_accidental_web_contamination_when_grounding_is_sufficient():
    """13: A sufficiently grounded query strictly rejects web evidence even if erroneously passed."""
    internal_chunks = [{"chunk_id": "c1", "document_name": "SAP CC.pdf", "chunk_text": "Sufficient internal text.", "rerank_score": 0.95}]
    web_chunks = [{"title": "Unwanted Web", "url": "https://help.sap.com/unwanted", "snippet": "Unwanted snippet"}]

    prompt = llm_service._build_user_prompt(
        query="What is CC?",
        evidence=internal_chunks,
        source_type="knowledge_base",
        business_data=None,
        chat_history=None,
        web_evidence=web_chunks  # Erroneously passed
    )

    assert "=== INTERNAL SAP KNOWLEDGE BASE ===" in prompt
    assert "Sufficient internal text." in prompt
    assert "=== FALLBACK WEB EVIDENCE ===" not in prompt
    assert "Unwanted snippet" not in prompt
    assert "Unwanted Web" not in prompt
