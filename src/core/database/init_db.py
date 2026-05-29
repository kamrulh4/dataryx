import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import models as db_models
from core.database.connection import SessionLocal, engine

logger = logging.getLogger(__name__)


def run_migrations():
    """Ensure database schema is up-to-date."""
    # We let create_all handle the table creation with the new schema.
    pass


# Run migrations BEFORE create_all
run_migrations()
# Then create any new tables
db_models.Base.metadata.create_all(bind=engine)


def create_default_catalog_namespace(db: Session):
    """Create the default 'General' catalog with a 'user_flows' schema if they don't exist."""
    general = (
        db.query(db_models.CatalogNamespace)
        .filter_by(name="General", parent_id=None)
        .first()
    )
    if not general:
        general = db_models.CatalogNamespace(
            name="General",
            parent_id=None,
            level=0,
            description="Default catalog",
            # We use owner_id=0 as a system-owned default since local users are synced on login
            owner_id=0,
        )
        db.add(general)
        db.commit()
        db.refresh(general)

    user_flows = (
        db.query(db_models.CatalogNamespace)
        .filter_by(name="user_flows", parent_id=general.id)
        .first()
    )
    if not user_flows:
        user_flows = db_models.CatalogNamespace(
            name="user_flows",
            parent_id=general.id,
            level=1,
            description="Default schema for user flows",
            owner_id=0,
        )
        db.add(user_flows)
        db.commit()


def init_db():
    db = SessionLocal()
    try:
        create_default_catalog_namespace(db)
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
