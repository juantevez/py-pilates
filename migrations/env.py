from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importa los modelos ORM de todos los contextos para poblar la metadata.
import pilates.booking.adapters.orm  # noqa: F401
import pilates.identity.adapters.orm  # noqa: F401
import pilates.scheduling.adapters.orm  # noqa: F401
import pilates.shared.infrastructure.orm  # noqa: F401
from pilates.config import get_settings
from pilates.shared.infrastructure.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
