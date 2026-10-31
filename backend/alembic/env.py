from sqlalchemy import create_engine, pool

from alembic import context
from app import models  # noqa: F401
from app import contractor_models  # noqa: F401
from app.config import settings
from app.db import Base

config = context.config
if context.is_offline_mode():
    context.configure(url=settings().database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable = create_engine(settings().database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            include_object=lambda obj, name, type_, reflected, compare_to: name != "spatial_ref_sys",
        )
        with context.begin_transaction():
            context.run_migrations()
