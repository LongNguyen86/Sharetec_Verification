import json
import re
import sqlite3
import sys
from pathlib import Path
import polars as pl

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "workspace" / "rules.db"
RAW_DATA_DIR = BASE_DIR / "workspace" / "raw_data"
OUTPUT_DIR = BASE_DIR / "workspace" / "output"


def col_letter_to_index(letter: str) -> int:
    """Convert Excel column letters (A, B, C...) to 0-based index."""
    if not letter:
        return -1
    words = re.findall(r"[A-Za-z]+", str(letter))
    if not words:
        return -1
    clean_letter = words[-1].upper()
    result = 0
    for char in clean_letter:
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def load_raw_tables() -> dict:
    """Load all CSV files from raw_data folder into RAM as Polars DataFrames."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    tables = {}
    for file_path in RAW_DATA_DIR.glob("*.csv"):
        table_name = file_path.stem.upper().replace(" ", "_")
        try:
            try:
                df = pl.read_csv(file_path, infer_schema_length=0, ignore_errors=True, encoding="utf8")
            except Exception:
                df = pl.read_csv(file_path, infer_schema_length=0, ignore_errors=True, encoding="latin1")
            tables[table_name] = df
            print(f"📥 Loaded Raw Table [{table_name}]: {df.shape[0]} rows, {df.shape[1]} cols")
        except Exception as e:
            print(f"⚠️ Error reading file {file_path.name}: {e}")
    return tables


def get_available_cu_ids(db_path: Path) -> list[str]:
    """Retrieve distinct non-global CU IDs stored in the rule_store database."""
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT cu_id FROM rule_store WHERE cu_id IS NOT NULL AND cu_id != '' AND is_global != 1"
        )
        rows = cursor.fetchall()
        return [r[0] for r in rows if r[0]]


def resolve_column_name(data_file: str, col_letter: str, default_table: str, available_cols: list) -> str | None:
    """Resolve exact DataFrame column name based on Table Name + Column Letter (A, B, C...)."""
    data_file_clean = data_file.strip().upper().replace(" ", "_") if data_file and str(data_file).strip().upper() not in ["", "N/A", "NAN", "NONE"] else None
    col_idx = col_letter_to_index(col_letter)
    if col_idx < 0:
        return None

    if data_file_clean:
        target_col = f"{data_file_clean}::col_{col_idx}"
        if target_col in available_cols:
            return target_col

    if default_table:
        target_col = f"{default_table.upper().replace(' ', '_')}::col_{col_idx}"
        if target_col in available_cols:
            return target_col

    matching_cols = [c for c in available_cols if str(c).endswith(f"::col_{col_idx}")]
    if matching_cols:
        return matching_cols[0]

    return None


def evaluate_conditional_rule(
    target_field: str, src_file: str, src_col: str, dsl_dict: dict, raw_notes: str, df: pl.DataFrame, default_table: str
) -> pl.Expr:
    """Explicit target_field evaluator preventing cross-rule interference."""

    # 1. mb.mb-num
    if target_field == "mb.mb-num":
        col_a = resolve_column_name(src_file, "A", default_table, df.columns)
        col_b = resolve_column_name(src_file, "B", default_table, df.columns)

        if col_a and col_a in df.columns:
            val_a = pl.col(col_a).cast(pl.Utf8).fill_null("").str.strip_chars()
            if col_b and col_b in df.columns:
                val_b = pl.col(col_b).cast(pl.Utf8).fill_null("").str.strip_chars()
                cond = (val_a == val_b) & (val_a != "0") & (val_a != "")
                gen_id = pl.lit("99") + val_a.str.zfill(8)
                return pl.when(cond.fill_null(False)).then(gen_id).otherwise(val_a)
            return val_a

    # 2. mb.mb-type
    if target_field == "mb.mb-type":
        col_al = resolve_column_name(src_file, "AL", default_table, df.columns)
        col_a = resolve_column_name(src_file, "A", default_table, df.columns)
        col_b = resolve_column_name(src_file, "B", default_table, df.columns)

        val_al = pl.col(col_al).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase() if (col_al and col_al in df.columns) else pl.lit("")
        val_a = pl.col(col_a).cast(pl.Utf8).fill_null("").str.strip_chars() if (col_a and col_a in df.columns) else pl.lit("")
        val_b = pl.col(col_b).cast(pl.Utf8).fill_null("").str.strip_chars() if (col_b and col_b in df.columns) else pl.lit("")

        is_non_member = (val_a == val_b) & (val_a != "") & (val_a != "0")

        return (
            pl.when(is_non_member.fill_null(False))
            .then(pl.lit("NON"))
            .when(val_al.str.contains("INSTITUC"))
            .then(pl.lit("BUS"))
            .when(val_al == "DBA")
            .then(pl.lit("DBA"))
            .when(val_al == "MENOR")
            .then(pl.lit("YUTH"))
            .when(val_al == "COOPERATIVA")
            .then(pl.lit("COOP"))
            .when(val_al == "GRUPO")
            .then(pl.lit("GRPO"))
            .when(val_al == "IGLESIA")
            .then(pl.lit("IGLE"))
            .when(val_al == "INCAPACIDAD")
            .then(pl.lit("INCP"))
            .otherwise(pl.lit("M"))
        )

    # 3. Organization Fields Check (AL Column)
    col_al = resolve_column_name(src_file, "AL", default_table, df.columns)
    if col_al and col_al in df.columns:
        is_org = pl.col(col_al).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase().str.contains("INSTITUCION|DBA|COOPERATIVA|GRUPO|IGLESIA|INCAPACIDAD").fill_null(False)

        if target_field == "mb.bus-fl":
            return pl.when(is_org).then(pl.lit("Y")).otherwise(pl.lit("N"))

        if target_field == "mb.first-name":
            col_d = resolve_column_name(src_file, "D", default_table, df.columns)
            val_d = pl.col(col_d).cast(pl.Utf8) if (col_d and col_d in df.columns) else pl.lit(None)
            return pl.when(is_org).then(pl.lit(None)).otherwise(val_d)

        if target_field == "mb.middle-name":
            col_e = resolve_column_name(src_file, "E", default_table, df.columns)
            val_e = pl.col(col_e).cast(pl.Utf8) if (col_e and col_e in df.columns) else pl.lit(None)
            return pl.when(is_org).then(pl.lit(None)).otherwise(val_e)

        if target_field == "mb.last-name":
            col_f = resolve_column_name(src_file, "F", default_table, df.columns)
            col_g = resolve_column_name(src_file, "G", default_table, df.columns)
            col_bb = resolve_column_name(src_file, "BB", default_table, df.columns)
            col_d = resolve_column_name(src_file, "D", default_table, df.columns)
            col_e = resolve_column_name(src_file, "E", default_table, df.columns)

            val_f = pl.col(col_f).fill_null("") if (col_f and col_f in df.columns) else pl.lit("")
            val_g = pl.col(col_g).fill_null("") if (col_g and col_g in df.columns) else pl.lit("")
            fg_combined = (val_f + pl.lit(" ") + val_g).str.strip_chars()

            val_bb = pl.col(col_bb).cast(pl.Utf8) if (col_bb and col_bb in df.columns) else None
            val_d = pl.col(col_d).fill_null("") if (col_d and col_d in df.columns) else pl.lit("")
            val_e = pl.col(col_e).fill_null("") if (col_e and col_e in df.columns) else pl.lit("")
            full_combined = (val_d + pl.lit(" ") + val_e + pl.lit(" ") + val_f + pl.lit(" ") + val_g).str.strip_chars()

            org_name = val_bb if val_bb is not None else full_combined
            return pl.when(is_org).then(org_name).otherwise(fg_combined)

    # 4. Generic Fallback
    target_c = resolve_column_name(src_file, src_col, default_table, df.columns)
    if target_c and target_c in df.columns:
        return pl.col(target_c).cast(pl.Utf8)

    return pl.lit(None)


def execute_transformation(cu_id: str = None, sheet_name: str = None):
    """Execute data transformation dynamically across any Sheet & Section for given CU(s)."""
    if not cu_id:
        available_cus = get_available_cu_ids(DB_PATH)
        if not available_cus:
            print("❌ No valid CU IDs found in rule_store database. Please run rule_engine.py first!")
            return
        for target_cu in available_cus:
            execute_transformation(cu_id=target_cu, sheet_name=sheet_name)
        return

    tables = load_raw_tables()
    if not tables:
        print("❌ NO Raw Data CSV files found in workspace/raw_data/!")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        target_sheets = [sheet_name] if sheet_name else [r[0] for r in cursor.execute("SELECT DISTINCT sheet_name FROM rule_store WHERE cu_id = ? OR is_global = 1", (cu_id,)).fetchall() if r[0]]

        for current_sheet in target_sheets:
            print(f"\n==================================================")
            print(f"📂 PROCESSING SHEET: [{current_sheet}] FOR CU: [{cu_id}]")
            print(f"==================================================")

            cursor.execute(
                "SELECT DISTINCT section_name FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name IS NOT NULL",
                (cu_id, current_sheet),
            )
            sections = [r[0] for r in cursor.fetchall()]

            for sec in sections:
                cursor.execute(
                    "SELECT data_file, COUNT(*) as cnt FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND data_file IS NOT NULL AND data_file != '' AND data_file != 'N/A' GROUP BY data_file ORDER BY cnt DESC LIMIT 1",
                    (cu_id, current_sheet, sec),
                )
                data_file_row = cursor.fetchone()

                default_table_key = None
                if data_file_row and data_file_row[0] and str(data_file_row[0]).strip():
                    default_table_key = Path(data_file_row[0]).stem.upper().replace(" ", "_")

                if not default_table_key or default_table_key not in tables:
                    print(f"⚠️ Skipping Section [{sec}]: No valid source Data File mapped in mapping Excel.")
                    continue

                print(f"\n⚡ Transforming Section: [{sec}] using Source Table [{default_table_key}]...")

                df = tables[default_table_key]
                new_cols = [f"{default_table_key}::col_{i}" for i in range(df.shape[1])]
                base_df = df.rename(dict(zip(df.columns, new_cols)))

                total_rows = base_df.shape[0]
                output_data = {}

                cursor.execute(
                    "SELECT target_field, data_file, column_letter, rule_type, dsl_json, dsl_readable, status, raw_notes FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND target_field != '_SECTION_RULE_' ORDER BY id ASC",
                    (cu_id, current_sheet, sec),
                )
                field_rules = cursor.fetchall()

                for field, src_file, src_col, rule_type, dsl_json_str, dsl_readable, status, raw_notes in field_rules:
                    dsl_dict = {}
                    if dsl_json_str:
                        try:
                            dsl_dict = json.loads(dsl_json_str)
                        except Exception:
                            pass

                    # Process DIRECT mapping
                    if rule_type == "DIRECT":
                        target_col = resolve_column_name(src_file, src_col, default_table_key, base_df.columns)
                        if target_col and target_col in base_df.columns:
                            output_data[field] = base_df[target_col].head(total_rows)
                        else:
                            output_data[field] = pl.Series([None] * total_rows)

                    # Process NO_MAPPING
                    elif rule_type == "NO_MAPPING":
                        output_data[field] = pl.Series([None] * total_rows)

                    # Process CONSTANT
                    elif rule_type == "CONSTANT":
                        val_raw = str(dsl_dict.get("value") if isinstance(dsl_dict, dict) and dsl_dict.get("value") else raw_notes).strip()
                        clean_val = re.split(r";|\bIF\b", val_raw, flags=re.IGNORECASE)[0]
                        clean_val = re.sub(r"^ASSIGN\s+(ALL\s+)?", "", clean_val, flags=re.IGNORECASE).strip()
                        output_data[field] = pl.Series([clean_val] * total_rows)

                    # Process CONDITIONAL or UNPARSED rules directly
                    elif rule_type in ["CONDITIONAL", "UNPARSED"]:
                        cond_expr = evaluate_conditional_rule(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key)
                        if cond_expr is not None:
                            output_data[field] = base_df.with_columns(cond_expr.alias(field))[field]
                        else:
                            output_data[field] = pl.Series([None] * total_rows)

                    else:
                        output_data[field] = pl.Series([f"[LOOKUP_PENDING]"] * total_rows)

                if output_data:
                    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                    res_df = pl.DataFrame(output_data)

                    clean_sheet = re.sub(r'[\\/*?:"<>|]', "_", current_sheet).replace(" ", "_")
                    clean_sec = re.sub(r'[\\/*?:"<>|]', "_", sec).replace(" ", "_")
                    out_file = OUTPUT_DIR / f"Expected_{cu_id}_{clean_sheet}_{clean_sec}.csv"

                    try:
                        res_df.write_csv(out_file)
                        print(f"  ✅ Successfully exported Test Data for [{cu_id}]: {out_file.name} ({res_df.shape[0]} rows)")
                    except PermissionError:
                        print(f"\n❌ ERROR: File '{out_file.name}' IS OPEN IN EXCEL!")
                        print(f"👉 Please CLOSE EXCEL and re-run the command!\n")


if __name__ == "__main__":
    target_cu_id = sys.argv[1].upper() if len(sys.argv) > 1 else None
    execute_transformation(cu_id=target_cu_id, sheet_name=None)