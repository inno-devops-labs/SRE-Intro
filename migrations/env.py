"""Migrate the existing QuickTicket schema; DATABASE_URL overrides local defaults."""

import logging
import os

from alembic import context
from sqlalchemy import engine_from_config, pool


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
config = context.config
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET lock_timeout = '3s'")
            connection.exec_driver_sql("SET statement_timeout = '30s'")
            connection.commit()
            context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
