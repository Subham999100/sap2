import pytest
from sqlalchemy.orm import Session
from backend.app.database import SessionLocal
from backend.app.services.embedding_service import embedding_service
from backend.app.services.retrieval_service import retrieval_service
from backend.app.models.chunk import DocumentChunk
from backend.app.config import settings

@pytest.fixture(scope="module")
def db_session():
    db = SessionLocal()
    yield db
    db.close()

def test_query_embedding_dimension_1536():
    """Verify that the OpenAI embedding service generates 1536-dimensional vectors."""
    vec = embedding_service.embed_text("SAP BRIM Subscription Order Management")
    assert isinstance(vec, list)
    assert len(vec) == 1536, f"Expected 1536 dimensions, got {len(vec)}"
    # Verify vector is L2 normalized
    import numpy as np
    norm = np.linalg.norm(vec)
    assert abs(norm - 1.0) < 1e-4

def test_vector_search_1536(db_session: Session):
    """Verify vector search runs against embedding_new with 1536-dim queries and returns valid similarity scores."""
    query = "Revenue Accounting and Reporting RAR contract reversal"
    results = retrieval_service.vector_search(db_session, query, top_k=5)
    assert len(results) > 0, "Vector search should return candidates"
    assert len(results) <= 5
    for r in results:
        assert "chunk_id" in r
        assert "chunk_text" in r
        assert "document_name" in r
        assert r["retrieval_method"] == "vector"
        assert 0.0 <= r["score"] <= 1.0, f"Score out of bounds: {r['score']}"
        assert r["vector_score"] is not None

def test_lexical_search(db_session: Session):
    """Verify PostgreSQL GIN full-text lexical search operates correctly."""
    query = "Subscription Order Management SOM"
    results = retrieval_service.lexical_search(db_session, query, top_k=5)
    assert len(results) > 0, "Lexical search should return results"
    for r in results:
        assert r["retrieval_method"] == "lexical"
        assert 0.0 <= r["score"] <= 1.0
        assert r["keyword_score"] is not None

def test_hybrid_search_rrf(db_session: Session):
    """Verify hybrid search with Reciprocal Rank Fusion combines vector and lexical results."""
    query = "Convergent Invoicing billing document"
    results = retrieval_service.hybrid_search(db_session, query, top_k=10)
    assert len(results) > 0, "Hybrid search should return candidates"
    assert len(results) <= 10
    for r in results:
        assert r["retrieval_method"] == "hybrid_rrf"
        assert "rrf_score" in r
        assert r["rrf_score"] > 0
        assert 0.0 <= r["score"] <= 1.0

def test_retrieve_and_rerank_pipeline(db_session: Session):
    """Verify the full 2-stage retrieval and reranker pipeline."""
    query = "What is the process for RAR contract reversal in SAP?"
    chunks, has_sufficient = retrieval_service.retrieve_and_rerank(
        db_session,
        query=query,
        candidate_k=15,
        final_k=3,
        enable_rerank=True,
    )
    assert len(chunks) > 0, "Pipeline should return top reranked chunks"
    assert len(chunks) <= 3
    assert isinstance(has_sufficient, bool)
    first_chunk = chunks[0]
    assert "chunk_text" in first_chunk
    assert "document_name" in first_chunk
    assert "score" in first_chunk
    assert first_chunk["score"] > 0.0

def test_empty_query_handling(db_session: Session):
    """Verify that empty queries return empty results safely without errors."""
    assert retrieval_service.hybrid_search(db_session, "") == []
    assert retrieval_service.hybrid_search(db_session, "   ") == []
    assert retrieval_service.vector_search(db_session, "") == []
    assert retrieval_service.lexical_search(db_session, "") == []
    chunks, sufficient = retrieval_service.retrieve_and_rerank(db_session, "")
    assert chunks == []
    assert sufficient is False

def test_document_filter(db_session: Session):
    """Verify filtering by specific document name works across searches."""
    target_doc = "Getting_Started_Guide.pdf"
    results = retrieval_service.hybrid_search(
        db_session,
        query="SAP installation guide",
        top_k=5,
        document_filter=target_doc
    )
    for r in results:
        assert r["document_name"] == target_doc, f"Expected {target_doc}, got {r['document_name']}"

def test_recoverable_384_local_path(db_session: Session, monkeypatch):
    """Verify the original 384-dimensional retrieval path remains recoverable."""
    # Mock embed_text to return a 384-dimensional vector (simulating local provider)
    local_384_vec = [0.01] * 384
    monkeypatch.setattr(embedding_service, "embed_text", lambda text: local_384_vec)

    # Hybrid search should detect 384-dim and query the original 'embedding' column
    results = retrieval_service.hybrid_search(db_session, "SOM subscription", top_k=5)
    assert len(results) > 0, "384-dim query should succeed on original column"

def test_invalid_dimension_rejection(db_session: Session, monkeypatch):
    """Verify that unsupported vector dimensions raise a clear ValueError."""
    invalid_vec = [0.1] * 512
    monkeypatch.setattr(embedding_service, "embed_text", lambda text: invalid_vec)

    with pytest.raises(ValueError, match="does not match any configured database column"):
        retrieval_service.hybrid_search(db_session, "test query")
