import json
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.user import User
from backend.app.models.chat import Chat
from backend.app.models.message import Message
from backend.app.models.citation import Citation
from backend.app.services.sap_classifier import sap_classifier
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.rag_service import rag_service
from backend.app.services.context_builder import context_builder

client = TestClient(app)


@pytest.fixture
def test_user_and_chat():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "prod_test@sap.com").first()
        if not user:
            from backend.app.security.auth import hash_password
            user = User(
                email="prod_test@sap.com",
                password_hash=hash_password("Password123!"),
                name="Prod Tester",
                is_admin=False
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        chat = Chat(user_id=user.id, title="BRIM Test Chat")
        db.add(chat)
        db.commit()
        db.refresh(chat)

        yield user, chat, db
    finally:
        db.close()


def test_brim_classifier_without_sap_prefix():
    """Validates that professional BRIM terminology is accepted without explicitly prefixing 'SAP'."""
    brim_queries = [
        "How does provider contract determine billing?",
        "What is a Price Plan and Price Table?",
        "Explain chargeable item vs consumption item",
        "Explain BIT and CIT classes in billing",
        "How does allowance logic calculate balances?",
        "Explain partner settlement and revenue sharing"
    ]
    for q in brim_queries:
        is_sap, msg = sap_classifier.classify(q)
        assert is_sap is True, f"BRIM query '{q}' should be classified as SAP domain"

    unrelated_queries = [
        "What is Python?",
        "Give me a chocolate cake recipe.",
        "Who is Elon Musk?",
        "How is the weather today?"
    ]
    for q in unrelated_queries:
        is_sap, msg = sap_classifier.classify(q)
        assert is_sap is False, f"Query '{q}' should be rejected as non-SAP"


def test_lexical_fallback_on_long_scenario():
    """Validates that long natural-language scenario queries do not produce zero lexical hits."""
    db = SessionLocal()
    try:
        scenario_query = (
            "Design a 499 telecom pricing plan with 100 GB included data, "
            "10 per additional GB, weekday weekend pricing differences, "
            "and a 20 discount for the first 3 months."
        )
        candidates = retrieval_service.hybrid_search(db, scenario_query, top_k=20)
        assert isinstance(candidates, list)
        assert len(candidates) > 0, "Scenario query must retrieve candidates from hybrid search"
        # Confirm that chunks from SAP CC or SOM are present
        doc_names = [c["document_name"] for c in candidates]
        assert any("SAP CC" in d or "pdfdownload" in d for d in doc_names)
    finally:
        db.close()


def test_multi_turn_history_in_rag_service(test_user_and_chat):
    """Validates that prior conversation turns are retrieved and incorporated into context."""
    user, chat, db = test_user_and_chat

    # Add prior turn with explicit timestamps
    from datetime import datetime, timedelta, timezone
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=2)
    m1 = Message(chat_id=chat.id, role="user", content="What is a Price Plan in SAP Convergent Charging?", created_at=t1)
    m2 = Message(chat_id=chat.id, role="assistant", content="A Price Plan in SAP CC defines rating and charging rules.", created_at=t2)
    db.add(m1)
    db.add(m2)
    db.commit()

    prep = rag_service.prepare_context_and_sources(db, "How is it different from a Price Table?", chat_id=chat.id)
    assert prep["status"] == "ready"
    assert len(prep["chat_history"]) >= 2
    assert prep["chat_history"][-2]["role"] == "user"
    assert "Price Plan" in prep["chat_history"][-2]["content"]

    # Verify context formatting includes history
    formatted_context = context_builder.build_context(
        evidence=prep["evidence"],
        source_type="knowledge_base",
        chat_history=prep["chat_history"]
    )
    assert "CONVERSATION HISTORY (RECENT TURNS):" in formatted_context
    assert "Price Plan in SAP Convergent Charging" in formatted_context


def test_stream_query_sse_events(test_user_and_chat):
    """Validates that stream_query yields valid SSE metadata, token, and done events."""
    user, chat, db = test_user_and_chat
    query = "What is a Price Plan in SAP Convergent Charging?"

    events = list(rag_service.stream_query(db, query, chat_id=chat.id, user_id=user.id))
    assert len(events) >= 3

    # Check first event is metadata
    first_event = events[0]
    assert "event: metadata" in first_event
    assert "data: " in first_event

    # Check token events
    token_events = [e for e in events if "event: token" in e]
    assert len(token_events) > 0
    token_payload = json.loads(token_events[0].split("data: ")[1].strip())
    assert "token" in token_payload

    # Check final event is done
    last_event = events[-1]
    assert "event: done" in last_event
    assert '"status": "complete"' in last_event


def test_consistent_persistence_across_endpoints(test_user_and_chat):
    """Validates that /api/rag/query persists citations into the Citation table consistently."""
    user, chat, db = test_user_and_chat
    from backend.app.security.auth import create_access_token

    token = create_access_token(data={"sub": user.id, "email": user.email})
    headers = {"Authorization": f"Bearer {token}"}

    query = "Explain Price Plan vs Price Table in SAP Convergent Charging"
    resp = client.post(
        "/api/rag/query",
        json={"chat_id": chat.id, "query": query},
        headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message_id"] is not None

    # Verify in DB that citations were persisted
    db.commit()
    citations_in_db = db.query(Citation).filter(Citation.message_id == data["message_id"]).all()
    assert len(citations_in_db) > 0
    assert citations_in_db[0].document_name is not None
