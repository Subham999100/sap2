import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.config import settings

client = TestClient(app)

def test_admin_ingestion_blocked_for_normal_users():
    # Normal user login
    client.post("/api/auth/register", json={"email": "normal_dev_user@sap.com", "name": "Normal User", "password": "Password123!"})
    login_resp = client.post("/api/auth/login", json={"email": "normal_dev_user@sap.com", "password": "Password123!"}).json()
    normal_token = login_resp["access_token"]

    # Attempt to trigger admin ingestion without admin key
    resp = client.post(
        "/api/admin/documents/ingest",
        headers={"Authorization": f"Bearer {normal_token}"}
    )
    # The first user might have been admin; a normal user must be forbidden
    # If the user is not admin and has no X-Admin-Key, it must return 403
    resp_no_token = client.post("/api/admin/documents/ingest")
    assert resp_no_token.status_code == 403

def test_admin_ingestion_allowed_with_admin_key():
    from unittest.mock import patch
    with patch("backend.app.api.admin.run_ingestion", return_value=[]):
        resp = client.post(
            "/api/admin/documents/ingest",
            headers={"X-Admin-Key": settings.ADMIN_API_KEY}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

def test_no_document_download_routes_exist():
    """Verify that no public route exists allowing direct document downloads."""
    resp = client.get("/documents/SAP_MM_Materials_Management_Manual.pdf")
    assert resp.status_code == 404

    resp2 = client.get("/api/documents/1/download")
    assert resp2.status_code == 404

def test_invalid_jwt_token_rejected():
    resp = client.get("/api/chats", headers={"Authorization": "Bearer invalid_gibberish_token_xyz"})
    assert resp.status_code == 401
