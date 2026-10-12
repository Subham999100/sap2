'''Placeholder migration after adding `embedding_new` column.

The `embedding_new` column and its HNSW index are introduced in the previous
migration (20241010_01). This migration is intentionally empty; it exists only to
maintain a proper revision chain for future schema changes.
'''

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# Revision identifiers, used by Alembic.
revision = "20241010_02"
down_revision = "20241010_01"
branch_labels = None
depends_on = None

def upgrade():
    # No operation – embedding_new column already added in migration 01.
    pass
    # Existing GIN full‑text index and original HNSW index on `embedding` remain untouched.

def downgrade():
    # No operation – downgrade handled in migration 01.
    pass
