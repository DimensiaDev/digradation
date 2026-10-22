"""add the pgvector source index

Revision ID: b7c1d2e3f4a5
Revises: f3a9c1d7b2e4
Create Date: 2026-10-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1d7b2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "rag_chunks",
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=16), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", HALFVEC(2048), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("doc_id", "ordinal", "kind"),
    )
    op.create_index("ix_rag_chunks_source", "rag_chunks", ["source_id", "source_kind"], unique=False)
    op.create_index(
        "ix_rag_chunks_embedding_hnsw",
        "rag_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_rag_chunks_embedding_hnsw", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_source", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    # The vector extension is shared by every database that may use it, so it is
    # deliberately not dropped here. `CREATE EXTENSION IF NOT EXISTS` above makes
    # a downgrade-then-upgrade cycle safe either way.
