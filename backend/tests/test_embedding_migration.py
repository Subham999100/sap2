import importlib
import pytest
from unittest import mock
import sys, os
import sqlalchemy as sa
# Ensure the project root is on sys.path so 'backend' package can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Dynamically import the staged migration script that adds a new 1536‑dimensional column
migration = importlib.import_module('backend.alembic.versions.20241010_01_add_embedding_dim_1536')

def test_revision_ids():
    assert getattr(migration, 'revision') == '20241010_01'
    assert getattr(migration, 'down_revision') is None

def test_upgrade_operations(monkeypatch):
    executed_sql = []
    def fake_execute(sql):
        executed_sql.append(sql)
    fake_op = mock.MagicMock()
    fake_op.execute.side_effect = fake_execute
    fake_op.add_column = mock.MagicMock()
    monkeypatch.setattr(migration, 'op', fake_op)
    # Patch Vector to a dummy sqlalchemy type for the test
    class DummyVector(sa.types.TypeEngine):
        def __init__(self, dim):
            self.dim = dim
        def __repr__(self):
            return f'Vector({self.dim})'
    monkeypatch.setattr('backend.alembic.versions.20241010_01_add_embedding_dim_1536.Vector', DummyVector)
    migration.upgrade()
    # Verify that a new column was added for the 1536‑dim embeddings
    fake_op.add_column.assert_called_once()
    args, _ = fake_op.add_column.call_args
    assert args[0] == 'document_chunks'
    # Verify that the HNSW index for the new column was created
    assert any('CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_new_hnsw' in stmt for stmt in executed_sql)

def test_downgrade_operations(monkeypatch):
    executed_sql = []
    def fake_execute(sql):
        executed_sql.append(sql)
    fake_op = mock.MagicMock()
    fake_op.execute.side_effect = fake_execute
    fake_op.drop_column = mock.MagicMock()
    monkeypatch.setattr(migration, 'op', fake_op)
    migration.downgrade()
    # Ensure the index for the new column is dropped
    assert any('DROP INDEX IF EXISTS ix_document_chunks_embedding_new_hnsw' in stmt for stmt in executed_sql)
    # Ensure the new column is removed
    fake_op.drop_column.assert_called_once_with('document_chunks', 'embedding_new')
