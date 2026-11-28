from alembic import context

with context.config.attributes["engine"].begin() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
