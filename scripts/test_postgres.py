"""Run only inside the API container: python /app/../... or via the mounted script."""
import subprocess
from sqlalchemy import create_engine, text
from app.db import engine

if __name__ == '__main__':
    if engine.dialect.name != 'postgresql':
        raise SystemExit('This script requires the Compose PostgreSQL configuration')
    admin = create_engine(engine.url, isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        if not connection.scalar(text("SELECT 1 FROM pg_database WHERE datname='test_jobs'")):
            connection.execute(text('CREATE DATABASE test_jobs'))
    import os
    task_env = dict(os.environ)
    task_env['TEST_DATABASE_URL'] = engine.url.set(database='test_jobs').render_as_string(hide_password=False)
    # Any accidental use of the production session factory must fail on empty memory DB.
    task_env['DATABASE_URL'] = 'sqlite:///:memory:'
    raise SystemExit(subprocess.call(['python','-m','pytest','-q','--tb=short'], env=task_env))
