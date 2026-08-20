import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import models as db_models
from core.database.connection import SessionLocal, engine

logger = logging.getLogger(__name__)


def run_migrations():
    """Ensure database schema is up-to-date.

    create_all() (called right after this) only creates tables that don't
    exist yet -- it never adds columns to a table that's already there. Any
    new column on an existing model (like oracle_connect_type below) needs
    an explicit, idempotent ALTER TABLE here, or every user who already has
    a database_connections table from a previous version hits
    "no such column" the next time that table is touched.
    """
    try:
        with engine.connect() as conn:
            existing_cols = {
                row[1]
                for row in conn.execute(
                    text("PRAGMA table_info(database_connections)")
                ).fetchall()
            }
            if not existing_cols:
                # Table doesn't exist yet -- create_all() will make it with
                # the column already included, nothing to migrate.
                return
            if "oracle_connect_type" not in existing_cols:
                conn.execute(
                    text(
                        "ALTER TABLE database_connections "
                        "ADD COLUMN oracle_connect_type VARCHAR DEFAULT 'service_name'"
                    )
                )
                conn.commit()
    except Exception:
        logger.exception("Migration check for database_connections failed")


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
