import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

@pytest.fixture
def auth_headers():
    client.post("/api/auth/register", json={"email": "rag_evaluator@sap.com", "name": "Evaluator", "password": "Password123!"})
    login_resp = client.post("/api/auth/login", json={"email": "rag_evaluator@sap.com", "password": "Password123!"}).json()
    return {"Authorization": f"Bearer {login_resp['access_token']}"}

def test_rag_knowledge_base_retrieval(auth_headers):
    # Query clearly present in SAP Convergent Charging implementation guide
    resp = client.post(
        "/api/rag/query",
        json={"query": "What is a Price Plan in SAP Convergent Charging?"},
        headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["source_type"] == "knowledge_base"
    assert data["grounding_score"] >= 0.60
    assert len(data["citations"]) > 0

    # Verify citation structure: safe metadata only
    first_citation = data["citations"][0]
    assert "document" in first_citation
    assert "page" in first_citation
    assert "section" in first_citation
    assert "snippet" in first_citation
    assert ("SAP CC" in first_citation["document"] or "pdfdownload" in first_citation["document"] or "BRIM Book" in first_citation["document"] or "BRIM Press Book" in first_citation["document"])
    # Verify no raw filepath leaks
    assert "C:\\" not in first_citation["document"]
    assert "/Users/" not in first_citation["document"]

def test_rag_web_fallback_for_external_topics(auth_headers):
    # Query for SAP topic not present in our BRIM manuals (e.g. Celonis process mining)
    resp = client.post(
        "/api/rag/query",
        json={"query": "How does SAP integrate with Celonis for execution management and process mining?"},
        headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["source_type"] == "web"
    assert data["grounding_score"] > 0.0
    assert len(data["web_sources"]) > 0
    assert any("help.sap.com" in w["domain"] or "sap.com" in w["domain"] for w in data["web_sources"])
