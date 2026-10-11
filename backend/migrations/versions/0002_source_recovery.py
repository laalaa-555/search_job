"""Persist source cooldowns and separate LinkedIn listing/detail freshness."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("detail_status", sa.String(20), nullable=False, server_default="complete"))
    op.add_column("jobs", sa.Column("detail_collected_at", sa.DateTime(timezone=True)))
    for name, kind in (("reason_code", sa.String(40)), ("next_retry_at", sa.DateTime(timezone=True)), ("last_data_at", sa.DateTime(timezone=True))):
        op.add_column("source_states", sa.Column(name, kind))
    op.execute("UPDATE jobs SET detail_status = 'pending' WHERE source = 'linkedin' AND description = ''")
    op.execute("UPDATE jobs SET detail_collected_at = last_seen_at WHERE source = 'linkedin' AND description <> ''")
    op.execute("UPDATE source_states SET last_data_at = (SELECT MAX(last_seen_at) FROM jobs WHERE jobs.source = source_states.source)")


def downgrade():
    with op.batch_alter_table("source_states") as batch:
        for name in ("last_data_at", "next_retry_at", "reason_code"):
            batch.drop_column(name)
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("detail_collected_at")
        batch.drop_column("detail_status")
