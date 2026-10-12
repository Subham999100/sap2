'''Staged migration to add a 1536‑dimensional embedding column.

We keep the existing `embedding` (384‑dim) column untouched. A new nullable column
`embedding_new` of type Vector(1536) is added, together with an HNSW index.
After a separate re‑embedding job populates `embedding_new`, a cut‑over migration
can drop the old column and rename the new one.

The downgrade drops the added column and its index.
'''

try:
    from alembic import op
except ImportError:
    class _OpPlaceholder:
        def __getattr__(self, name):
            raise NotImplementedError(f"alembic.op.{name} is not available")
    op = _OpPlaceholder()
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# Revision identifiers, used by Alembic.
revision = "20241010_01"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # 1. Add new column for 1536‑dim embeddings (nullable until re‑embedding completes)
    op.add_column(
        "document_chunks",
        sa.Column("embedding_new", Vector(1536), nullable=True),
    )

    # 2. Create HNSW index for the new column (cosine similarity)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_new_hnsw ON document_chunks USING hnsw (embedding_new vector_cosine_ops)"
    )
    # Existing GIN full‑text index and original HNSW index on `embedding` remain untouched.

def downgrade():
    # Drop the HNSW index on the new column if present
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_new_hnsw")
    # Remove the new column
    op.drop_column("document_chunks", "embedding_new")
