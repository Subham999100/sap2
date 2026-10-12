import pytest
from backend.app.database import SessionLocal
from backend.app.config import settings
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.reranking_service import reranking_service, RerankingService


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_hybrid_retrieval_produces_rrf_candidates(db_session):
    """1 & 2: Test that hybrid retrieval works and RRF produces the expected candidate pool."""
    query = "What is SAP Convergent Charging?"
    candidates = retrieval_service.hybrid_search(db_session, query, top_k=settings.RRF_CANDIDATE_K)

    assert isinstance(candidates, list)
    assert len(candidates) > 0
    assert len(candidates) <= settings.RRF_CANDIDATE_K

    # Verify RRF score and normalized score exist
    for c in candidates:
        assert "chunk_id" in c
        assert "document_name" in c
        assert "chunk_text" in c
        assert "rrf_score" in c
        assert c["rrf_score"] > 0.0
        assert "score" in c
        assert 0.0 <= c["score"] <= 1.0


def test_reranker_processes_rrf_candidates_and_returns_final_k(db_session):
    """3 & 4: Test that reranker receives RRF candidates and returns top_n (Top 5)."""
    query = "What is SAP Convergent Charging?"
    candidates = retrieval_service.hybrid_search(db_session, query, top_k=settings.RRF_CANDIDATE_K)
    assert len(candidates) >= 5

    top_chunks, has_sufficient = reranking_service.rerank(
        query=query,
        candidates=candidates,
        top_n=settings.FINAL_TOP_K
    )

    assert isinstance(top_chunks, list)
    assert len(top_chunks) == settings.FINAL_TOP_K
    assert isinstance(has_sufficient, bool)


def test_chunk_fields_and_metadata_preserved(db_session):
    """5: Test that all existing chunk fields and metadata are preserved after reranking."""
    query = "SAP Convergent Charging pricing and rating"
    candidates = retrieval_service.hybrid_search(db_session, query, top_k=10)
    top_chunks, _ = reranking_service.rerank(query, candidates, top_n=5)

    required_fields = [
        "chunk_id",
        "document_id",
        "document_name",
        "page_number",
        "section",
        "chunk_text",
        "vector_score",
        "keyword_score",
        "rrf_score",
        "score",
        "rerank_score",
        "retrieval_method"
    ]

    for chunk in top_chunks:
        for field in required_fields:
            assert field in chunk, f"Missing required field '{field}' in reranked chunk"
        assert isinstance(chunk["rerank_score"], float)
        assert 0.0 <= chunk["rerank_score"] <= 1.0
        assert chunk["retrieval_method"] in ("hybrid_rrf_rerank", "hybrid_rrf_fallback")


def test_empty_candidates_handled_safely():
    """6: Test that empty retrieval results are handled gracefully without exceptions."""
    empty_result, has_sufficient = reranking_service.rerank(
        query="nonexistent topic",
        candidates=[],
        top_n=5
    )
    assert empty_result == []
    assert has_sufficient is False


def test_reranking_failure_safe_fallback(db_session):
    """7: Test that if the neural reranker model fails or is unavailable, fallback still succeeds."""
    query = "What is SAP Convergent Charging?"
    candidates = retrieval_service.hybrid_search(db_session, query, top_k=10)
    assert len(candidates) > 0

    # Instantiate a mock/isolated reranking service that simulates model failure
    service = RerankingService()
    service._score_candidates_with_model = lambda q, c: None  # force neural failure
    service._score_with_model = lambda q, c: None

    fallback_chunks, sufficient = service.rerank(query, candidates, top_n=5)

    assert len(fallback_chunks) == min(5, len(candidates))
    for chunk in fallback_chunks:
        assert chunk["retrieval_method"] == "hybrid_rrf_fallback"
        assert "rerank_score" in chunk
        assert "rrf_score" in chunk


def test_retrieve_and_rerank_unified_pipeline(db_session):
    """Test the complete 2-stage retrieve_and_rerank pipeline and baseline comparison."""
    query = "SAP Convergent Charging"

    # 1. Proposed 2-stage pipeline (enable_rerank=True)
    reranked_chunks, reranked_sufficient = retrieval_service.retrieve_and_rerank(
        db=db_session,
        query=query,
        candidate_k=settings.RRF_CANDIDATE_K,
        final_k=settings.FINAL_TOP_K,
        enable_rerank=True
    )

    assert len(reranked_chunks) == settings.FINAL_TOP_K
    assert "rerank_score" in reranked_chunks[0]

    # 2. Baseline comparison (enable_rerank=False)
    baseline_chunks, baseline_sufficient = retrieval_service.retrieve_and_rerank(
        db=db_session,
        query=query,
        candidate_k=settings.RRF_CANDIDATE_K,
        final_k=settings.FINAL_TOP_K,
        enable_rerank=False
    )

    assert len(baseline_chunks) == settings.FINAL_TOP_K
    assert baseline_chunks[0]["retrieval_method"] == "hybrid_rrf"


def test_reranker_preserves_technical_identifiers_and_deduplicates():
    """Test that technical identifiers receive boost, query coverage is included, and duplicates are suppressed."""
    service = RerankingService()
    # Mock neural model scoring with uniform neutral score to isolate heuristic and deduplication effects
    service._score_candidates_with_model = lambda q, c: [0.5] * len(c)

    query = "How to monitor billable items in SAP CI using transaction FKKBIX_BIT_MON?"

    candidates = [
        {
            "chunk_id": "c1_dup1",
            "document_name": "BRIM Book -2.pdf",
            "chunk_text": "General overview of Convergent Invoicing billing processes and invoicing documents.",
            "section": "Overview",
            "rrf_score": 0.05,
        },
        {
            "chunk_id": "c1_dup2",
            "document_name": "SAP BRIM Press Book.pdf",
            "chunk_text": "General overview of Convergent Invoicing billing processes and invoicing documents.",
            "section": "Overview",
            "rrf_score": 0.05,
        },
        {
            "chunk_id": "c2_tech",
            "document_name": "SAP BRIM Press Book.pdf",
            "chunk_text": "Using transaction FKKBIXBIT_MON to monitor billable item statuses (raw, billable, billed) and analyze errors.",
            "section": "Monitoring",
            "rrf_score": 0.04,
        },
    ]

    top_chunks, sufficient = service.rerank(query, candidates, top_n=2)

    # 1. Deduplication: c1_dup2 must be suppressed, allowing distinct technical chunk c2_tech into top 2
    top_ids = [c["chunk_id"] for c in top_chunks]
    assert "c2_tech" in top_ids, "Technical chunk must be retained in top chunks and not crowded out by duplicates"
    assert not ("c1_dup1" in top_ids and "c1_dup2" in top_ids), "Duplicate chunks should not both consume top slots"

    # 2. Technical identifier match and query coverage
    tech_chunk = next(c for c in top_chunks if c["chunk_id"] == "c2_tech")
    assert tech_chunk["identifier_matches"] >= 1, "FKKBIX_BIT_MON must match FKKBIXBIT_MON via normalized matching"
    assert tech_chunk["query_coverage"] > 0.0, "Query coverage must be positive"
    assert 0.0 <= tech_chunk["rerank_score"] <= 1.0, "Rerank score must stay within [0.0, 1.0]"


def test_reranker_chunk_caching_and_bounded_input():
    """Test that document embeddings are cached and text bounding works without altering chunk metadata."""
    from unittest.mock import MagicMock
    import numpy as np

    service = RerankingService()
    mock_model = MagicMock()
    # Mock model embedding outputs
    mock_model.embed.side_effect = lambda texts: [np.ones((len(t.split()) + 2, 96)) for t in texts]
    service._get_model = lambda: mock_model

    candidates = [
        {
            "chunk_id": "chunk_abc_1",
            "document_name": "TestDoc.pdf",
            "chunk_text": "This is a long test document chunk text that explains SAP SOM order distribution. " * 20,
            "section": "SOM Order Distribution",
            "rrf_score": 0.04
        }
    ]

    # Cold run
    scores1 = service._score_candidates_with_model("What is SOM?", candidates)
    assert scores1 is not None
    assert "chunk_abc_1_SOM Order Distribution" in service._doc_embedding_cache
    assert mock_model.embed.call_count == 2  # 1 for query, 1 for uncached candidates

    # Warm run with same candidate
    mock_model.embed.reset_mock()
    scores2 = service._score_candidates_with_model("What is SOM?", candidates)
    assert scores2 is not None
    assert mock_model.embed.call_count == 1  # 1 for query ONLY, 0 for candidates (cached!)
    assert candidates[0]["chunk_text"].startswith("This is a long test document")  # Original text preserved


def test_embedding_service_caching():
    """Test that EmbeddingService caches query embeddings across calls."""
    from backend.app.services.embedding_service import embedding_service

    text = "What is SAP Convergent Charging charge plan?"
    vec1 = embedding_service.embed_text(text)
    assert isinstance(vec1, list)
    assert len(vec1) > 0

    cache_keys = [k for k in embedding_service._cache if text in k]
    assert len(cache_keys) >= 1

    vec2 = embedding_service.embed_text(text)
    assert vec1 == vec2


