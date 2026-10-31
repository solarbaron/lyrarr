"""
Alembic migration environment for Lyrarr.
Uses the same database URL and models as the main application.
"""

import logging
from logging.config import fileConfig

from alembic import context

# Alembic Config object
config = context.config

# Interpret the config file for Python logging — only for the standalone
# `alembic` CLI. When lyrarr runs Alembic in-process (init_db stamps the
# database) the app's logging is already configured; fileConfig would replace
# its handlers, drop the root level to WARNING and disable every existing
# logger for the rest of the process.
if (
    config.config_file_name is not None
    and config.attributes.get('configure_logger', True)
    and not logging.getLogger().handlers
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Import the app's models metadata for autogenerate
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from lyrarr.app.database import Base, engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL output only)."""
    from lyrarr.app.get_args import args
    url = f'sqlite:///{os.path.join(args.config_dir, "db", "lyrarr.db")}'
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using the existing engine."""
    connectable = engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # Required for SQLite ALTER TABLE support
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
