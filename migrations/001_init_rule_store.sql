-- 1. Main Rule storage table
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

-- 2. Audit History table for QA reviews
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

-- 3. CU Registry table
CREATE TABLE IF NOT EXISTS cu_registry (
    cu_id TEXT PRIMARY KEY,
    cu_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);