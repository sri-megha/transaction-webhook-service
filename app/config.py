import os


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./transactions.db")

    # Hosted Postgres providers hand out plain postgres:// URLs.
    # SQLAlchemy needs to be told which async driver to use.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _database_url()
PROCESSING_DELAY_SECONDS = float(os.getenv("PROCESSING_DELAY_SECONDS", "30"))
SWEEP_INTERVAL_SECONDS = float(os.getenv("SWEEP_INTERVAL_SECONDS", "60"))
