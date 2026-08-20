import sqlite3
from pathlib import Path
from src.bsdc_engine.config import settings
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


class RuleStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init_schema(self):
        """Execute migration script to set up tables if missing."""
        migration_file = settings.BASE_DIR / "migrations" / "001_init_rule_store.sql"
        if migration_file.exists():
            sql = migration_file.read_text(encoding="utf-8")
            with self.get_connection() as conn:
                conn.executescript(sql)
                conn.commit()
            logger.info(f"Initialized Database schema at: {self.db_path}")

    def get_available_cu_ids(self) -> list[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT cu_id FROM rule_store WHERE cu_id IS NOT NULL AND cu_id != '' AND is_global != 1"
            )
            return [r[0] for r in cursor.fetchall() if r[0]]