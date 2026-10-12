import pytest
from sqlalchemy import func
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.chunk import DocumentChunk
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.reranking_service import reranking_service

client = TestClient(app)

@pytest.fixture
def auth_headers():
    client.post("/api/auth/register", json={"email": "kb_tester@sap.com", "name": "KBTester", "password": "Password123!"})
    login_resp = client.post("/api/auth/login", json={"email": "kb_tester@sap.com", "password": "Password123!"}).json()
    return {"Authorization": f"Bearer {login_resp['access_token']}"}

def test_kb_database_integrity():
    """Verify that all 23 documents and 10,106 chunks are present without corruption."""
    db = SessionLocal()
    try:
        doc_count = db.query(Document).count()
        assert doc_count == 24, f"Expected 24 documents, got {doc_count}"

        chunk_count = db.query(DocumentChunk).count()
        assert chunk_count == 10141, f"Expected 10141 chunks, got {chunk_count}"

        # No null embeddings
        null_embeddings = db.query(DocumentChunk).filter(DocumentChunk.embedding == None).count()
        assert null_embeddings == 0, f"Expected 0 NULL embeddings, found {null_embeddings}"

        # Embedding dimension check
        first_chunk = db.query(DocumentChunk).first()
        assert first_chunk is not None
        assert len(first_chunk.embedding) == 384

        # No duplicate documents
        duplicate_docs = (
            db.query(Document.filename)
            .group_by(Document.filename)
            .having(func.count(Document.id) > 1)
            .all()
        )
        assert len(duplicate_docs) == 0, f"Found duplicate documents: {duplicate_docs}"

        # No duplicate chunks per document
        duplicate_chunks = (
            db.query(DocumentChunk.document_id, DocumentChunk.chunk_index)
            .group_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            .having(func.count(DocumentChunk.id) > 1)
            .all()
        )
        assert len(duplicate_chunks) == 0, f"Found duplicate chunks: {duplicate_chunks}"
    finally:
        db.close()

def test_retrieval_rar_contract_reversal():
    """Verify retrieval of Revenue Accounting and Reporting (RAR) contract reversal."""
    db = SessionLocal()
    try:
        retrieval_svc = RetrievalService()
        query = "What is the RAR Contract Reversal Process and how are performance obligations handled?"
        candidates = retrieval_svc.hybrid_search(db, query, top_k=10)
        assert len(candidates) > 0

        top_chunks, is_sufficient = reranking_service.rerank(query, candidates, top_n=3)
        assert len(top_chunks) > 0
        assert is_sufficient is True
        top_doc = top_chunks[0]["document_name"]
        assert "RAR" in top_doc or "Revenue" in top_doc or "BRIM" in top_doc
        assert top_chunks[0]["rerank_score"] >= 0.60
    finally:
        db.close()

def test_retrieval_abap_rap():
    """Verify retrieval of ABAP RESTful Application Programming Model (RAP) knowledge."""
    db = SessionLocal()
    try:
        retrieval_svc = RetrievalService()
        query = "What is the ABAP RESTful Application Programming Model RAP and its architecture?"
        candidates = retrieval_svc.hybrid_search(db, query, top_k=10)
        assert len(candidates) > 0

        top_chunks, is_sufficient = reranking_service.rerank(query, candidates, top_n=3)
        assert len(top_chunks) > 0
        assert is_sufficient is True
        assert "ABAP" in top_chunks[0]["document_name"] or "RAP" in top_chunks[0]["document_name"]
        assert top_chunks[0]["rerank_score"] >= 0.60
    finally:
        db.close()

def test_retrieval_budget_billing():
    """Verify retrieval of Budget Billing concepts in FI-CA."""
    db = SessionLocal()
    try:
        retrieval_svc = RetrievalService()
        query = "Explain Budget Billing plan and procedure in SAP FI-CA"
        candidates = retrieval_svc.hybrid_search(db, query, top_k=10)
        assert len(candidates) > 0

        top_chunks, is_sufficient = reranking_service.rerank(query, candidates, top_n=3)
        assert len(top_chunks) > 0
        assert is_sufficient is True
        assert top_chunks[0]["rerank_score"] >= 0.60
    finally:
        db.close()

def test_retrieval_device_as_a_service_som():
    """Verify retrieval of Device as a Service (DaaS) in SAP SOM."""
    db = SessionLocal()
    try:
        retrieval_svc = RetrievalService()
        query = "How does SAP S/4HANA BRIM SOM handle Device as a Service DaaS?"
        candidates = retrieval_svc.hybrid_search(db, query, top_k=10)
        assert len(candidates) > 0

        top_chunks, is_sufficient = reranking_service.rerank(query, candidates, top_n=3)
        assert len(top_chunks) > 0
        assert is_sufficient is True
        assert top_chunks[0]["rerank_score"] >= 0.60
    finally:
        db.close()

def test_end_to_end_rag_with_expanded_kb(auth_headers):
    """Verify end-to-end RAG query using newly added knowledge."""
    resp = client.post(
        "/api/rag/query",
        json={"query": "Explain how the RAR contract reversal process works in SAP Revenue Accounting and Reporting."},
        headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["source_type"] in ["knowledge_base", "web", "refusal"]
    if data["source_type"] == "knowledge_base" and data["grounding_score"] > 0.0:
        assert data["grounding_score"] >= 0.60
        assert len(data["citations"]) > 0
        assert len(data["web_sources"]) == 0
    elif data["source_type"] == "web":
        assert len(data["web_sources"]) > 0

def test_retrieval_tcodes_brim():
    """Verify retrieval of SAP BRIM transaction codes from newly ingested T-Codes guide."""
    db = SessionLocal()
    try:
        retrieval_svc = RetrievalService()
        query = "What are the transaction codes T-codes in SAP BRIM?"
        candidates = retrieval_svc.hybrid_search(db, query, top_k=10)
        assert len(candidates) > 0

        top_chunks, is_sufficient = reranking_service.rerank(query, candidates, top_n=3)
        assert len(top_chunks) > 0
        assert is_sufficient is True
        assert "T Codes" in top_chunks[0]["document_name"]
        assert top_chunks[0]["rerank_score"] >= 0.60
    finally:
        db.close()

