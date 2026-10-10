from logging.config import fileConfig
import os

from sqlalchemy import pool
from alembic import context

from app.config import Settings
from app.database import Base, make_engine
from app.models import Certificate, Job  # noqa: F401 - ensure models are imported

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        ini_url = config.get_main_option("sqlalchemy.url")
        if ini_url and "driver://user:pass" not in ini_url:
            url = ini_url
        else:
            url = Settings.from_env().database_url
    return url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection", None)

    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    else:
        url = get_url()
        connectable = make_engine(url)

        with connectable.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata
            )
            with context.begin_transaction():
                context.run_migrations()
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
