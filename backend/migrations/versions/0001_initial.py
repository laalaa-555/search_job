"""Create jobs, bookmarks, collection status and worker leases."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("company", sa.String(500), nullable=False),
        sa.Column("location", sa.String(500), nullable=False),
        sa.Column("location_city", sa.String(100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("salary_text", sa.String(500), nullable=False),
        sa.Column("salary_currency", sa.String(10)),
        sa.Column("salary_period", sa.String(20)),
        sa.Column("salary_min", sa.Float()), sa.Column("salary_max", sa.Float()),
        sa.Column("annual_salary_min", sa.Integer()), sa.Column("annual_salary_max", sa.Integer()),
        sa.Column("salary_estimated", sa.Boolean(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_job_source_external_id"))
    op.create_index("ix_jobs_source_active", "jobs", ["source", "is_active"])
    op.create_index("ix_jobs_location_salary", "jobs", ["location_city", "annual_salary_min"])
    op.create_index("ix_jobs_updated", "jobs", ["updated_at", "id"])
    op.create_table("bookmarks", sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("source_states", sa.Column("source", sa.String(20), primary_key=True), sa.Column("status", sa.String(20), nullable=False), sa.Column("last_started_at", sa.DateTime(timezone=True)), sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_finished_at", sa.DateTime(timezone=True)), sa.Column("collected_count", sa.Integer(), nullable=False), sa.Column("keywords_completed", sa.Integer(), nullable=False), sa.Column("keywords_total", sa.Integer(), nullable=False), sa.Column("error", sa.Text()), sa.Column("truncated", sa.Boolean(), nullable=False))
    op.create_table("collection_runs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source", sa.String(20), nullable=False), sa.Column("trigger", sa.String(20), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("status", sa.String(20), nullable=False), sa.Column("collected_count", sa.Integer(), nullable=False), sa.Column("error", sa.Text()))
    op.create_table("worker_locks", sa.Column("name", sa.String(50), primary_key=True), sa.Column("owner", sa.String(100), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ("worker_locks", "collection_runs", "source_states", "bookmarks", "jobs"):
        op.drop_table(table)
