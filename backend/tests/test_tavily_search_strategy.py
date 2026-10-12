import pytest
from unittest.mock import MagicMock, patch
from backend.app.database import SessionLocal
from backend.app.services.web_search_service import WebSearchService, web_search_service
from backend.app.services.rag_service import rag_service
from backend.app.services.grounding_service import grounding_service
from backend.app.services.llm_service import llm_service


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_1_query_generation_allowance_scenario():
    """
    Requirement 1 & 2: For the allowance product-switch scenario, generate focused queries covering:
    - SAP Convergent Charging, subscription/product changes, pricing configuration
    - Allowance validity, remaining balances, expiration, transfer, and proration behavior.
    """
    query = (
        "How does SAP Convergent Charging treat a partially-consumed allowance "
        "when a subscription is switched to a different product or pricing configuration "
        "within the same billing period?"
    )

    queries = WebSearchService.generate_focused_queries(query)

    assert len(queries) >= 2
    assert len(queries) <= 3

    # Concept 1: Subscription/product changes & pricing configuration in SAP CC
    has_sub_change = any(
        "subscription" in q.lower() and "product" in q.lower() and "charging" in q.lower()
        for q in queries
    )
    assert has_sub_change, "Must generate a query covering subscription/product changes in CC"

    # Concept 2: Allowance validity, remaining balance, expiration/transfer/proration
    has_validity_proration = any(
        "allowance" in q.lower() and ("validity" in q.lower() or "balance" in q.lower()) and ("proration" in q.lower() or "expiration" in q.lower() or "transfer" in q.lower())
        for q in queries
    )
    assert has_validity_proration, "Must generate a query covering allowance validity and proration/expiration/transfer"

    # All queries must be unique and non-empty
    assert len(queries) == len(set(q.lower() for q in queries))
    for q in queries:
        assert len(q.strip()) > 10


def test_2_query_generation_general_scenario():
    """
    Requirement 1: General queries generate a concise, focused keyword query without question filler.
    """
    query = "How does SAP S/4HANA integrate with Ariba for central procurement?"
    queries = WebSearchService.generate_focused_queries(query)

    assert len(queries) >= 1
    assert len(queries) <= 3
    # First query contains full context or SAP prefix
    assert "sap" in queries[0].lower()
    # If second query is generated, it focuses on operative terms
    if len(queries) > 1:
        assert "how" not in queries[1].lower()
        assert "does" not in queries[1].lower()
        assert "ariba" in queries[1].lower()
        assert "procurement" in queries[1].lower()


def test_3_canonical_url_deduplication():
    """
    Requirement 4: Canonicalize URLs and deduplicate results, retaining superior evidence snippets.
    """
    # Test individual canonicalization
    url1 = "https://HELP.SAP.COM/docs/cc/allowance/?utm_source=google&ref=chat#section1"
    url2 = "https://help.sap.com/docs/cc/allowance#section2"
    url3 = "https://help.sap.com:443/docs/cc/allowance"

    canon1 = WebSearchService._canonicalize_url(url1)
    canon2 = WebSearchService._canonicalize_url(url2)
    canon3 = WebSearchService._canonicalize_url(url3)

    assert canon1 == "https://help.sap.com/docs/cc/allowance"
    assert canon2 == "https://help.sap.com/docs/cc/allowance"
    assert canon3 == "https://help.sap.com/docs/cc/allowance"

    # Test deduplication across multiple search results
    service = WebSearchService(api_key="test-key")
    service.client = MagicMock()

    # Query 1 returns a short snippet; Query 2 returns a deeper, more detailed snippet for the same page
    service.client.search.side_effect = [
        {
            "results": [
                {
                    "title": "SAP CC Allowances",
                    "url": "https://help.sap.com/docs/cc/allowance#intro",
                    "content": "Short intro to allowances in SAP CC.",
                    "score": 0.82
                }
            ]
        },
        {
            "results": [
                {
                    "title": "SAP CC Allowances Detailed",
                    "url": "https://help.sap.com/docs/cc/allowance/?locale=en-US",
                    "content": "Comprehensive details on allowance lifecycle, validity periods, proration, and expiration when subscriptions change.",
                    "score": 0.94
                }
            ]
        },
        {
            "results": []
        }
    ]

    results = service.search_sap_authoritative("SAP Convergent Charging allowance switch", max_results=5)
    # Both URLs canonicalize to the same page, so exactly 1 result should remain
    assert len(results) == 1
    # The longer, more informative snippet should be retained
    assert "Comprehensive details on allowance lifecycle" in results[0]["snippet"]
    assert results[0]["score"] == 0.94


def test_4_authoritative_domain_filtering():
    """
    Requirement 3: Restrict results strictly to the authoritative SAP domain allowlist.
    """
    service = WebSearchService(api_key="test-key")
    service.client = MagicMock()
    service.client.search.return_value = {
        "results": [
            {
                "title": "SAP Help Documentation",
                "url": "https://help.sap.com/docs/valid-doc",
                "content": "Authoritative SAP documentation on charging.",
                "score": 0.91
            },
            {
                "title": "Untrusted Blog",
                "url": "https://random-sap-blog.com/tips",
                "content": "Third-party unofficial tips.",
                "score": 0.88
            },
            {
                "title": "SAP Learning Hub",
                "url": "https://learning.sap.com/courses/brim200",
                "content": "Official course on BRIM architecture.",
                "score": 0.95
            },
            {
                "title": "Phishing Attempt",
                "url": "https://evil-sap-portal.com/login",
                "content": "Fake portal.",
                "score": 0.99
            }
        ]
    }

    results = service.search_sap_authoritative("SAP BRIM", max_results=5)
    assert len(results) == 2
    domains = [r["domain"] for r in results]
    assert "help.sap.com" in domains
    assert "learning.sap.com" in domains
    for r in results:
        assert any(allowed in r["url"] for allowed in service.ALLOWED_DOMAINS)


def test_5_low_value_and_boilerplate_snippet_filtering():
    """
    Requirement 4 & 5: Boilerplate forum text (e.g. login/comment warnings) is filtered out.
    """
    service = WebSearchService(api_key="test-key")
    service.client = MagicMock()
    service.client.search.return_value = {
        "results": [
            {
                "title": "SAP Community Thread",
                "url": "https://community.sap.com/thread/123",
                "content": "You must be a registered user to add a comment on this discussion.",
                "score": 0.80
            },
            {
                "title": "SAP Community Solution",
                "url": "https://community.sap.com/thread/456",
                "content": "In SAP Convergent Charging, allowance balances are defined with validity dates and can be configured with expiration counters.",
                "score": 0.89
            }
        ]
    }

    results = service.search_sap_authoritative("SAP CC allowance", max_results=5)
    assert len(results) == 1
    assert "You must be a registered user" not in results[0]["snippet"]
    assert "In SAP Convergent Charging, allowance balances" in results[0]["snippet"]


def test_6_high_relevance_score_not_proof_of_grounding():
    """
    Requirement 5 & 6: High Tavily relevance score is NOT treated as proof that a source answers the question.
    If the LLM refuses because evidence is insufficient, grounding evaluation strictly returns 0.0 and no citations.
    """
    refusal_answer = "The information is not available in the provided knowledge base or fallback web evidence."
    mock_web = [
        {
            "title": "SAP Convergent Charging Overview",
            "url": "https://help.sap.com/cc-overview",
            "domain": "help.sap.com",
            "snippet": "General concepts of charging logic, rate plans, and subscribers.",
            "score": 0.99  # Very high Tavily score
        }
    ]

    score = grounding_service.evaluate_grounding("test question", refusal_answer, mock_web, source_type="refusal")
    assert score == 0.0

    aligned = grounding_service.align_web_sources(refusal_answer, mock_web)
    assert aligned == [], "Refusal answer must never attach web citations even if source score was 0.99"


def test_7_insufficient_evidence_flow(db_session):
    """
    Requirement 6: When both internal KB and web fallback return no usable evidence,
    the system strictly returns refusal with 0 grounding and no citations.
    """
    query = "How does SAP Convergent Charging treat a partially-consumed allowance when a subscription is switched?"

    # Mock Tavily returning empty results (or all filtered)
    with patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=[]):
        prep = rag_service.prepare_context_and_sources(db_session, query)

        assert prep["status"] == "insufficient"
        assert prep["answer"] == "The information is not available in the provided knowledge base or fallback web evidence."
        assert prep["grounding_score"] == 0.0
        assert prep["citations"] == []
        assert prep["web_sources"] == []


def test_8_no_repeated_queries_or_unbounded_search_loops():
    """
    Requirement 7: Do not repeatedly search the same query or introduce unbounded search loops.
    """
    service = WebSearchService(api_key="test-key")
    service.client = MagicMock()
    service.client.search.return_value = {"results": []}

    # Even if query generation had identical queries, search_sap_authoritative must not search duplicates
    with patch.object(service, "generate_focused_queries", return_value=["SAP CC Allowance", "SAP CC Allowance", "sap cc allowance"]):
        results = service.search_sap_authoritative("SAP CC Allowance")
        # client.search should only have been called ONCE due to normalization and deduplication
        assert service.client.search.call_count == 1


def test_9_grounded_answer_and_streaming_compatibility(db_session):
    """
    Requirement 8: Preserve both non-streaming (process_query) and streaming (stream_query) behavior.
    """
    query = "How does SAP Convergent Charging handle allowance validity?"
    mock_web_results = [
        {
            "title": "SAP CC Allowance Lifecycle",
            "url": "https://help.sap.com/docs/cc-allowance-lifecycle",
            "domain": "help.sap.com",
            "snippet": "Allowances have start and end validity periods and maintain consumption counters until expiration.",
            "score": 0.91
        }
    ]
    mock_llm_answer = (
        "According to SAP Convergent Charging documentation, allowances maintain consumption "
        "counters and validity periods until expiration."
    )

    # Test 1: Synchronous process_query
    with patch("backend.app.services.retrieval_service.retrieval_service.retrieve_and_rerank", return_value=([], False)), \
         patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_web_results), \
         patch("backend.app.services.llm_service.llm_service.generate_answer", return_value=mock_llm_answer):

        res = rag_service.process_query(db_session, query)
        assert res["source_type"] == "web"
        assert res["answer"] == mock_llm_answer
        assert len(res["web_sources"]) > 0
        assert res["grounding_score"] > 0.0

    # Test 2: Streaming stream_query
    mock_tokens = ["Allowances ", "maintain ", "validity ", "periods ", "and ", "counters."]
    with patch("backend.app.services.retrieval_service.retrieval_service.retrieve_and_rerank", return_value=([], False)), \
         patch("backend.app.services.web_search_service.web_search_service.search_sap_authoritative", return_value=mock_web_results), \
         patch("backend.app.services.llm_service.llm_service.stream_answer", return_value=iter(mock_tokens)):

        events = list(rag_service.stream_query(db_session, query, chat_id=None))
        assert any("event: metadata" in e for e in events)
        assert any("event: token" in e for e in events)
        assert any("event: done" in e for e in events)
