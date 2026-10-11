import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import MetaData, Table, create_engine, select

from app.ingestion.normalize import NormalizedJob
from app.models import utcnow


def test_sqlite_upgrade_preserves_existing_jobs_and_bookmarks(tmp_path):
    url = f"sqlite:///{tmp_path / 'test_migration.db'}"
    env = dict(os.environ, DATABASE_URL=url, PYTHONUTF8="1")
    root = Path(__file__).resolve().parents[1]
    def migrate(target):
        subprocess.run([sys.executable,"-m","alembic","upgrade",target],cwd=root,env=env,check=True,capture_output=True)
    migrate("0001")
    engine = create_engine(url)
    metadata = MetaData()
    jobs = Table("jobs",metadata,autoload_with=engine)
    bookmarks = Table("bookmarks",metadata,autoload_with=engine)
    values = NormalizedJob(source="linkedin",external_id="li-1",title="測試工程師",company="測試公司",url="https://www.linkedin.com/jobs/view/1",description="Python",salary_text="月薪 50,000",salary_currency="TWD",salary_period="month",salary_min=50000).finalize().storage_values()
    values = {name:value for name,value in values.items() if name in jobs.c}
    with engine.begin() as db:
        db.execute(jobs.insert().values(id=1,first_seen_at=utcnow(),last_seen_at=utcnow(),updated_at=utcnow(),**values))
        db.execute(bookmarks.insert().values(job_id=1,created_at=utcnow()))
    engine.dispose()
    migrate("head")
    metadata = MetaData()
    jobs = Table("jobs",metadata,autoload_with=engine)
    bookmarks = Table("bookmarks",metadata,autoload_with=engine)
    with engine.connect() as db:
        row = db.execute(select(jobs)).mappings().one()
        assert row["description"] == "Python" and row["annual_salary_min"] == 600000
        assert row["detail_status"] == "complete" and row["detail_collected_at"]
        assert db.scalar(select(bookmarks.c.job_id)) == 1
    engine.dispose()
