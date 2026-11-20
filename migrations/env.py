"""Synchronous Alembic execution, invoked off the application event loop."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def run() -> None:
    engine = create_engine(context.config.get_main_option("sqlalchemy.url"), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
