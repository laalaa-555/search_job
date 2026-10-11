"""Persist collection cycles and atomic keyword/page checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    op.create_table("collection_cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("next_keyword_index", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False))
    op.create_index("ix_collection_cycles_source", "collection_cycles", ["source"])
    op.create_table("keyword_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("collection_cycles.id"), nullable=False),
        sa.Column("keyword_index", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(500), nullable=False),
        sa.Column("next_page", sa.Integer(), nullable=False),
        sa.Column("next_offset", sa.Integer(), nullable=False),
        sa.Column("seen_ids", sa.JSON(), nullable=False),
        sa.Column("page_signatures", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("cycle_id", "keyword_index", name="uq_cycle_keyword"))
    op.create_index("ix_keyword_progress_cycle_id", "keyword_progress", ["cycle_id"])
    # SQLite requires batch mode for a new foreign key.
    with op.batch_alter_table("source_states") as batch:
        batch.add_column(sa.Column("current_cycle_id", sa.Integer()))
        batch.add_column(sa.Column("last_cycle_completed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_completed_scope_hash", sa.String(64)))
        batch.create_foreign_key("fk_source_current_cycle", "collection_cycles", ["current_cycle_id"], ["id"])
    # Legacy successes do not prove completion for today's configured scope.


def downgrade():
    with op.batch_alter_table("source_states") as batch:
        batch.drop_constraint("fk_source_current_cycle", type_="foreignkey")
        for name in ("last_completed_scope_hash", "last_cycle_completed_at", "current_cycle_id"):
            batch.drop_column(name)
    op.drop_table("keyword_progress")
    op.drop_table("collection_cycles")
