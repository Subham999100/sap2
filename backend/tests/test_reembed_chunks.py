import pytest
from unittest.mock import MagicMock, patch

# Import the function to test
from backend.app.commands.reembed_chunks import reembed_chunks, _is_valid_vector

class DummyChunk:
    def __init__(self, id_, text, embedding_new=None):
        self.id = id_
        self.chunk_text = text
        self.embedding_new = embedding_new

class DummySession:
    def __init__(self, chunks):
        # Store chunks sorted by numeric id for deterministic order
        self._chunks = sorted(chunks, key=lambda c: int(c.id))
        self.committed = False
        self.closed = False
        self._offset = 0
        self._limit = None
        self._min_id = None  # For keyset filter (id > last_id)

    def query(self, model):
        # Mock query builder returning self
        return self

    def filter(self, condition=None, *args, **kwargs):
        # Detect filter on id > value, whether condition is a string or a SQLAlchemy BinaryExpression
        if condition is not None:
            # If it's a string like "DocumentChunk.id > '5'"
            if isinstance(condition, str) and 'id >' in condition:
                try:
                    val = condition.split('>')[1].strip().strip("'\"")
                    self._min_id = int(val)
                except Exception:
                    self._min_id = None
            else:
                # Try to handle SQLAlchemy BinaryExpression with .right attribute
                right = getattr(condition, 'right', None)
                if right is not None:
                    try:
                        val = getattr(right, 'value', None)
                        if val is None:
                            val = str(right).strip("'\"")
                        self._min_id = int(val)
                    except Exception:
                        self._min_id = None
        return self

    def order_by(self, *args, **kwargs):
        # No operation needed; list is already ordered
        return self

    def offset(self, offset):
        self._offset = offset
        return self

    def limit(self, limit):
        self._limit = limit
        return self

    def all(self):
        # Apply keyset filter if set
        filtered = self._chunks
        if self._min_id is not None:
            filtered = [c for c in filtered if int(c.id) > self._min_id]
        # Apply offset/limit for pagination fallback
        start = getattr(self, '_offset', 0)
        end = start + (self._limit if self._limit is not None else len(filtered))
        return filtered[start:end]

    def count(self):
        return len(self._chunks)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

# Helper to create a fresh dummy session each test
def make_session(chunks):
    return DummySession(chunks)

def test_dry_run_no_write(monkeypatch):
    chunks = [DummyChunk("1", "text one"), DummyChunk("2", "text two")]
    dummy_session = make_session(chunks)
    monkeypatch.setattr('backend.app.commands.reembed_chunks.SessionLocal', lambda: dummy_session)
    # embed_batch should not be called in dry-run
    mock_service = MagicMock()
    mock_service.embed_batch.return_value = []
    monkeypatch.setattr('backend.app.commands.reembed_chunks.embedding_service', mock_service)

    summary = reembed_chunks(batch_size=2, dry_run=True)
    assert summary["processed"] == 2
    assert summary["skipped"] == 0
    assert summary["failed"] == 0
    # Ensure no commit happened and embedding_new unchanged
    assert not dummy_session.committed
    for c in chunks:
        assert c.embedding_new is None

def test_successful_reembed(monkeypatch):
    chunks = [DummyChunk("1", "text1"), DummyChunk("2", "text2")]
    dummy_session = make_session(chunks)
    monkeypatch.setattr('backend.app.commands.reembed_chunks.SessionLocal', lambda: dummy_session)
    # Return correct dimension vectors
    vectors = [[0.0] * 1536, [1.0] * 1536]
    mock_service = MagicMock()
    mock_service.embed_batch.return_value = vectors
    monkeypatch.setattr('backend.app.commands.reembed_chunks.embedding_service', mock_service)

    summary = reembed_chunks(batch_size=2, dry_run=False)
    assert summary["processed"] == 2
    assert summary["failed"] == 0
    assert dummy_session.committed
    for c, vec in zip(chunks, vectors):
        assert c.embedding_new == vec

def test_api_failure_counts_failed(monkeypatch):
    chunks = [DummyChunk("1", "text1"), DummyChunk("2", "text2")]
    dummy_session = make_session(chunks)
    monkeypatch.setattr('backend.app.commands.reembed_chunks.SessionLocal', lambda: dummy_session)
    mock_service = MagicMock()
    mock_service.embed_batch.side_effect = RuntimeError("API down")
    monkeypatch.setattr('backend.app.commands.reembed_chunks.embedding_service', mock_service)

    summary = reembed_chunks(batch_size=2, dry_run=False)
    assert summary["processed"] == 0
    assert summary["failed"] == 2
    assert not dummy_session.committed

def test_invalid_dimension_counts_failed(monkeypatch):
    chunks = [DummyChunk("1", "text1")]
    dummy_session = make_session(chunks)
    monkeypatch.setattr('backend.app.commands.reembed_chunks.SessionLocal', lambda: dummy_session)
    mock_service = MagicMock()
    # Return vector of wrong length
    mock_service.embed_batch.return_value = [[0.0] * 100]
    monkeypatch.setattr('backend.app.commands.reembed_chunks.embedding_service', mock_service)

    summary = reembed_chunks(batch_size=1, dry_run=False)
    assert summary["processed"] == 0
    assert summary["failed"] == 1
    assert not dummy_session.committed

def test_resumable_skips_already_embedded(monkeypatch):
    # First chunk already has valid embedding_new, second does not
    valid_vec = [0.0] * 1536
    chunks = [DummyChunk("1", "text1", embedding_new=valid_vec), DummyChunk("2", "text2")]
    dummy_session = make_session(chunks)
    monkeypatch.setattr('backend.app.commands.reembed_chunks.SessionLocal', lambda: dummy_session)
    mock_service = MagicMock()
    mock_service.embed_batch.return_value = [[1.0] * 1536]
    monkeypatch.setattr('backend.app.commands.reembed_chunks.embedding_service', mock_service)

    summary = reembed_chunks(batch_size=2, dry_run=False)
    # One skipped, one processed
    assert summary["skipped"] == 1
    assert summary["processed"] == 1
    assert chunks[1].embedding_new == [1.0] * 1536

# Validate helper function correctly recognises dimension
def test_is_valid_vector():
    assert _is_valid_vector([0.0] * 1536)
    assert not _is_valid_vector([0.0] * 100)
    assert not _is_valid_vector("not a list")
