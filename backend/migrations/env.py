from alembic import context

from app.db import Base, engine
from app import models  # noqa: F401

if context.is_offline_mode():
    context.configure(url=engine.url.render_as_string(hide_password=False), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=engine.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()
