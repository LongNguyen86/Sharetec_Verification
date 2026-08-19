import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "workspace" / "rules.db"


def init_database(reset=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if reset:
        cursor.execute("DROP TABLE IF EXISTS rule_history;")
        cursor.execute("DROP TABLE IF EXISTS rule_store;")
        cursor.execute("DROP TABLE IF EXISTS cu_registry;")
        print("🧹 Cleaned up old data in Database!")

    # 1. Main Rule storage table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rule_store (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cu_id TEXT NOT NULL DEFAULT 'GLOBAL',
        sheet_name TEXT NOT NULL,
        section_name TEXT DEFAULT 'MAIN',
        target_field TEXT NOT NULL,
        raw_notes TEXT,
        data_file TEXT,
        column_letter TEXT,
        rule_type TEXT NOT NULL,
        dsl_json TEXT NOT NULL,
        dsl_readable TEXT,
        is_global INTEGER DEFAULT 0,
        status TEXT DEFAULT 'AUTO_PARSED',
        parsed_by TEXT DEFAULT 'ITEM_8',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(cu_id, sheet_name, section_name, target_field)
    );
    """)

    # 2. Audit History table for QA reviews (Item #14)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rule_history (
        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER NOT NULL,
        cu_id TEXT,
        sheet_name TEXT,
        section_name TEXT,
        target_field TEXT,
        action TEXT NOT NULL,
        previous_dsl TEXT,
        new_dsl TEXT,
        reviewer TEXT,
        review_notes TEXT,
        reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. CU Registry table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cu_registry (
        cu_id TEXT PRIMARY KEY,
        cu_name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()
    print(f"✅ Successfully initialized SQLite Database at: {DB_PATH.resolve()}")


if __name__ == "__main__":
    init_database(reset=True)