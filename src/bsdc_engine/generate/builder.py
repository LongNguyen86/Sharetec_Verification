import json
import re
from pathlib import Path
import polars as pl

from src.bsdc_engine.rules.store import RuleStore
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


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


def resolve_column_name(
    data_file: str, col_letter: str, default_table: str, available_cols: list
) -> str | None:
    """Resolve exact DataFrame column name based on Table Name + Column Letter strictly preventing cross-table leaks."""
    data_file_clean = (
        data_file.strip().upper().replace(" ", "_")
        if data_file and str(data_file).strip().upper() not in ["", "N/A", "NAN", "NONE"]
        else None
    )
    col_idx = col_letter_to_index(col_letter)
    if col_idx < 0:
        return None

    if data_file_clean:
        target_col = f"{data_file_clean}::col_{col_idx}"
        if target_col in available_cols:
            return target_col
        return None

    if default_table:
        target_col = f"{default_table.upper().replace(' ', '_')}::col_{col_idx}"
        if target_col in available_cols:
            return target_col

    return None


def parse_section_filter_expr(
    filter_str: str, default_table: str, available_cols: list
) -> pl.Expr | None:
    """Convert section-level rule conditions into Polars expressions."""
    if not filter_str:
        return None

    filter_upper = filter_str.upper().strip()

    m_or = re.search(
        r"COLUMN\s+([A-Z]+)\s*=\s*([0-9A-Z_\-\.]+)\s+OR\s+([0-9A-Z_\-\.]+)",
        filter_upper,
    )
    if m_or:
        col_let, val1, val2 = m_or.group(1), m_or.group(2), m_or.group(3)
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = pl.col(c_name).cast(pl.Utf8).fill_null("").str.strip_chars()
            return (
                (col_expr == val1)
                | (col_expr == val2)
                | col_expr.str.starts_with(val1)
                | col_expr.str.starts_with(val2)
            )

    m_beg = re.search(
        r"COLUMN\s+([A-Z]+)\s+(?:BEGINS|STARTS\s+WITH|BEGINS\s+WITH)\s+([A-Z0-9_\-\s]+)",
        filter_upper,
    )
    if m_beg:
        col_let, val = m_beg.group(1), m_beg.group(2).strip()
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = (
                pl.col(c_name)
                .cast(pl.Utf8)
                .fill_null("")
                .str.strip_chars()
                .str.to_uppercase()
            )
            return col_expr.str.starts_with(val.upper())

    m_neq = re.search(r"COLUMN\s+([A-Z]+)\s*(?:<>|!=)\s*([^\|\n]+)", filter_upper)
    if m_neq:
        col_let = m_neq.group(1)
        val_raw = m_neq.group(2).strip()
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = (
                pl.col(c_name)
                .cast(pl.Utf8)
                .fill_null("")
                .str.strip_chars()
                .str.to_uppercase()
            )
            if "INACTIVE" in val_raw:
                return (col_expr != "INACTIVE") & (col_expr != "")
            if "0" in val_raw or "BLANK" in val_raw or "NULL" in val_raw:
                return (
                    (col_expr != "0")
                    & (col_expr != "")
                    & (col_expr != "NULL")
                    & (col_expr != "NONE")
                )
            return col_expr != val_raw

    if "DO NOT CREATE" in filter_upper or "DO NOT ASSIGN" in filter_upper:
        m_eq = re.search(
            r"COLUMN\s+([A-Z]+)\s*(?:=|\bIS\b)\s*([0-9A-Z_\-\.\s]+)", filter_upper
        )
        if m_eq:
            col_let, val = m_eq.group(1), m_eq.group(2).strip()
            val_clean = val.split()[0]
            c_name = resolve_column_name("", col_let, default_table, available_cols)
            if c_name and c_name in available_cols:
                col_expr = (
                    pl.col(c_name)
                    .cast(pl.Utf8)
                    .fill_null("")
                    .str.strip_chars()
                    .str.to_uppercase()
                )
                if val_clean in ["NULL", "BLANK", "NONE"]:
                    return (
                        (col_expr != "") & (col_expr != "NULL") & (col_expr != "NONE")
                    )
                return col_expr != val_clean

    m_eq = re.search(
        r"COLUMN\s+([A-Za-z0-9_]+)\s*(?:=|\bIS\b)\s*([0-9A-Z_\-\.]+)", filter_upper
    )
    if m_eq:
        col_let, val = m_eq.group(1), m_eq.group(2).strip()
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = (
                pl.col(c_name)
                .cast(pl.Utf8)
                .fill_null("")
                .str.strip_chars()
                .str.to_uppercase()
            )
            return col_expr == val

    return None


def evaluate_conditional_rule(
    target_field: str,
    src_file: str,
    src_col: str,
    dsl_dict: dict,
    raw_notes: str,
    df: pl.DataFrame,
    default_table: str,
    sec_name: str = "",
) -> pl.Expr:
    """Explicit target_field evaluator preventing cross-rule interference and resolving cross-table references."""
    raw_notes_upper = raw_notes.upper()

    mb_first_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_3")]
    mb_mid_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_4")]
    mb_last_f_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_5")]
    mb_last_g_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_6")]
    mb_branch_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_18")]

    if "MB.FIRST-NAME" in raw_notes_upper or target_field.endswith("first-name"):
        if mb_first_col:
            return pl.col(mb_first_col[0]).cast(pl.Utf8)

    if "MB.MIDDLE-NAME" in raw_notes_upper or target_field.endswith("middle-name"):
        if mb_mid_col:
            return pl.col(mb_mid_col[0]).cast(pl.Utf8)

    if "MB.LAST-NAME" in raw_notes_upper or target_field.endswith("last-name"):
        if mb_last_f_col:
            val_f = pl.col(mb_last_f_col[0]).fill_null("").cast(pl.Utf8)
            val_g = (
                pl.col(mb_last_g_col[0]).fill_null("").cast(pl.Utf8)
                if mb_last_g_col
                else pl.lit("")
            )
            return (val_f + pl.lit(" ") + val_g).str.strip_chars()

    if "MB.BRANCH" in raw_notes_upper or target_field.endswith(".branch"):
        if mb_branch_col:
            val_br = (
                pl.col(mb_branch_col[0])
                .cast(pl.Utf8)
                .fill_null("1")
                .str.strip_chars()
            )
            return pl.when(val_br == "").then(pl.lit("1")).otherwise(val_br)
        return pl.lit("1")

    if target_field.endswith("grp-type"):
        if "CERTIF" in sec_name.upper():
            return pl.lit("CD")
        col_b = resolve_column_name(src_file, "B", default_table, df.columns)
        if col_b and col_b in df.columns:
            val_b = pl.col(col_b).cast(pl.Utf8).fill_null("").str.strip_chars()
            return (
                pl.when(
                    val_b.str.starts_with("3")
                    | (val_b == "12")
                    | (val_b == "1202")
                )
                .then(pl.lit("CD"))
                .when(val_b.str.starts_with("2"))
                .then(pl.lit("CK"))
                .when(val_b.str.starts_with("1"))
                .then(pl.lit("SH"))
                .otherwise(pl.lit("SH"))
            )
        return pl.lit("CD") if "CERTIF" in sec_name.upper() else pl.lit("SH")

    if target_field == "dp.type":
        col_b = resolve_column_name(src_file, "B", default_table, df.columns)
        if col_b and col_b in df.columns:
            return pl.col(col_b).cast(pl.Utf8)

    if target_field.endswith("status-cd"):
        col_u = resolve_column_name(src_file, "U", default_table, df.columns)
        if col_u and col_u in df.columns:
            val_u = pl.col(col_u).cast(pl.Utf8).fill_null("").str.strip_chars()
            return (
                pl.when(val_u == "1")
                .then(pl.lit("CLOSED"))
                .otherwise(pl.lit("ACTIVE"))
            )
        return pl.lit("ACTIVE")

    if target_field.endswith("close-dt"):
        col_u = resolve_column_name(src_file, "U", default_table, df.columns)
        col_m = resolve_column_name(src_file, "M", default_table, df.columns)
        if col_u and col_u in df.columns and col_m and col_m in df.columns:
            val_u = pl.col(col_u).cast(pl.Utf8).fill_null("").str.strip_chars()
            val_m = pl.col(col_m).cast(pl.Utf8).fill_null("").str.strip_chars()
            return (
                pl.when(val_u == "1").then(val_m).otherwise(pl.lit(None))
            )

    if "RENEW" in raw_notes_upper or target_field.endswith("cert-renewal-cd"):
        return pl.lit("RENEW")

    if "DP-TYPE.DIV-RATE-FL" in raw_notes_upper or target_field.endswith(
        "div-rate-fl"
    ):
        return pl.lit("Y")

    if "DP-TYPE.DIV-CALC-METHOD" in raw_notes_upper or target_field.endswith(
        "div-calc-method"
    ):
        return pl.lit("DAILY")

    if "DP-TYPE.DIV-CALC-FREQ" in raw_notes_upper or target_field.endswith(
        "div-calc-freq"
    ):
        return pl.lit("MONTHLY")

    if "DP-TYPE.DIV-PAY-FREQ" in raw_notes_upper or target_field.endswith(
        "div-pay-freq"
    ):
        return pl.lit("MONTHLY")

    if "DP-TYPE.ANNIV-FL" in raw_notes_upper or target_field.endswith("anniv-fl"):
        return pl.lit("N")

    if "DP.CURR-BAL-AMT" in raw_notes_upper or target_field.endswith(
        "bal-forward-amt"
    ):
        col_c = resolve_column_name(src_file, "C", default_table, df.columns)
        if col_c and col_c in df.columns:
            return pl.col(col_c).cast(pl.Utf8)

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

    target_c = resolve_column_name(src_file, src_col, default_table, df.columns)
    if target_c and target_c in df.columns:
        return pl.col(target_c).cast(pl.Utf8)

    return pl.lit(None)


class TransformationBuilder:
    """Transformation Engine for generating Expected Migration CSV Tables using Polars."""

    def __init__(
        self,
        raw_data_dir: Path,
        output_dir: Path,
        db_path: Path | None = None,
    ):
        self.raw_data_dir = Path(raw_data_dir)
        self.output_dir = Path(output_dir)
        self.store = RuleStore(db_path=db_path)

    def load_raw_tables(self) -> dict:
        """Dynamically load all CSV files under work/csv or in/raw into RAM."""
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)
        tables = {}

        search_dirs = [self.raw_data_dir]
        run_base_dir = self.raw_data_dir.parent.parent

        fallback_dirs = [
            run_base_dir / "work" / "csv",
            run_base_dir / "in" / "raw",
            run_base_dir / "in" / "csv",
        ]
        for f_dir in fallback_dirs:
            if f_dir.exists() and f_dir not in search_dirs:
                search_dirs.append(f_dir)

        csv_files = []
        for search_dir in search_dirs:
            csv_files.extend(list(search_dir.glob("*.csv")))

        csv_files = list({f.resolve(): f for f in csv_files}.values())

        for file_path in csv_files:
            table_name = file_path.stem.upper().replace(" ", "_")
            try:
                try:
                    df = pl.read_csv(
                        file_path,
                        infer_schema_length=0,
                        ignore_errors=True,
                        encoding="utf8",
                    )
                except Exception:
                    df = pl.read_csv(
                        file_path,
                        infer_schema_length=0,
                        ignore_errors=True,
                        encoding="latin1",
                    )
                tables[table_name] = df
                print(
                    f"📥 Loaded Raw Table [{table_name}]: {df.shape[0]} rows,"
                    f" {df.shape[1]} cols"
                )
            except Exception as e:
                print(f"⚠️ Error reading CSV file {file_path.name}: {e}")
        return tables

    def get_available_cu_ids(self) -> list[str]:
        """Retrieve available non-global CU IDs from DB."""
        with self.store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT cu_id FROM rule_store WHERE cu_id IS NOT"
                " NULL AND cu_id != '' AND is_global != 1"
            )
            return [r[0] for r in cursor.fetchall() if r[0]]

    def generate_all(
        self, cu_id: str | None = None, sheet_name: str | None = None
    ) -> list[Path]:
        """Execute data transformation dynamically across mapped sheets and sections."""
        if not cu_id:
            available_cus = self.get_available_cu_ids()
            if not available_cus:
                print("❌ No valid CU IDs found in Database.")
                return []
            cu_id = available_cus[0]

        tables = self.load_raw_tables()
        if not tables:
            print("❌ NO Raw Data CSV files found in workspace!")
            return []

        generated_files = []
        self.output_dir.mkdir(parents=True, exist_ok=True)

        with self.store.get_connection() as conn:
            cursor = conn.cursor()

            target_sheets = (
                [sheet_name]
                if sheet_name
                else [
                    r[0]
                    for r in cursor.execute(
                        "SELECT DISTINCT sheet_name FROM rule_store WHERE cu_id"
                        " = ? OR is_global = 1",
                        (cu_id,),
                    ).fetchall()
                    if r[0]
                ]
            )

            for current_sheet in target_sheets:
                print(f"\n==================================================")
                print(f"📂 PROCESSING SHEET: [{current_sheet}] FOR CU: [{cu_id}]")
                print(f"==================================================")

                cursor.execute(
                    "SELECT DISTINCT section_name FROM rule_store WHERE (cu_id"
                    " = ? OR is_global = 1) AND sheet_name = ? AND section_name"
                    " IS NOT NULL",
                    (cu_id, current_sheet),
                )
                sections = [r[0] for r in cursor.fetchall()]

                for sec in sections:
                    cursor.execute(
                        "SELECT data_file, COUNT(*) as cnt FROM rule_store"
                        " WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ?"
                        " AND section_name = ? AND data_file IS NOT NULL AND"
                        " data_file != '' AND data_file != 'N/A' GROUP BY"
                        " data_file ORDER BY cnt DESC LIMIT 1",
                        (cu_id, current_sheet, sec),
                    )
                    data_file_row = cursor.fetchone()

                    default_table_key = None
                    if (
                        data_file_row
                        and data_file_row[0]
                        and str(data_file_row[0]).strip()
                    ):
                        default_table_key = (
                            Path(data_file_row[0])
                            .stem.upper()
                            .replace(" ", "_")
                        )

                    if (
                        not default_table_key
                        or default_table_key not in tables
                    ):
                        print(
                            f"⚠️ Skipping Section [{sec}]: No valid source Data"
                            " File mapped."
                        )
                        continue

                    print(
                        f"\n⚡ Transforming Section: [{sec}] using Base Table"
                        f" [{default_table_key}]..."
                    )

                    df_primary = tables[default_table_key]
                    base_cols = [
                        f"{default_table_key}::col_{i}"
                        for i in range(df_primary.shape[1])
                    ]
                    base_df = df_primary.rename(
                        dict(zip(df_primary.columns, base_cols))
                    )

                    cursor.execute(
                        "SELECT DISTINCT data_file FROM rule_store WHERE (cu_id"
                        " = ? OR is_global = 1) AND sheet_name = ? AND"
                        " section_name = ? AND data_file IS NOT NULL AND"
                        " data_file != '' AND data_file != 'N/A'",
                        (cu_id, current_sheet, sec),
                    )
                    sec_data_files = set(
                        [
                            Path(r[0]).stem.upper().replace(" ", "_")
                            for r in cursor.fetchall()
                            if r[0]
                        ]
                    )
                    sec_data_files.add("MEMBER_ACCOUNTS")

                    for sec_file in sec_data_files:
                        if sec_file != default_table_key and sec_file in tables:
                            sec_raw = tables[sec_file]
                            sec_cols = [
                                f"{sec_file}::col_{i}"
                                for i in range(sec_raw.shape[1])
                            ]
                            sec_df = sec_raw.rename(
                                dict(zip(sec_raw.columns, sec_cols))
                            )

                            left_key, right_key = None, None
                            for p_k in [
                                f"{default_table_key}::col_6",
                                f"{default_table_key}::col_16",
                                f"{default_table_key}::col_0",
                            ]:
                                for s_k in [
                                    f"{sec_file}::col_16",
                                    f"{sec_file}::col_6",
                                    f"{sec_file}::col_0",
                                ]:
                                    if (
                                        p_k in base_df.columns
                                        and s_k in sec_df.columns
                                    ):
                                        left_key, right_key = p_k, s_k
                                        break
                                if left_key:
                                    break

                            if left_key and right_key:
                                try:
                                    base_df = base_df.join(
                                        sec_df,
                                        left_on=left_key,
                                        right_on=right_key,
                                        how="left",
                                    )
                                    print(
                                        "  🔗 [AUTO JOIN SUCCESS] Joined"
                                        f" [{sec_file}] onto [{default_table_key}]"
                                        f" using keys [{left_key} == {right_key}]"
                                    )
                                except Exception as e:
                                    print(
                                        f"  ⚠️ Join warning for [{sec_file}]: {e}"
                                    )

                    cursor.execute(
                        "SELECT dsl_json, raw_notes FROM rule_store WHERE"
                        " (cu_id = ? OR is_global = 1) AND sheet_name = ? AND"
                        " section_name = ? AND target_field ="
                        " '_SECTION_RULE_' LIMIT 1",
                        (cu_id, current_sheet, sec),
                    )
                    sec_rule_row = cursor.fetchone()

                    if sec_rule_row:
                        sec_dsl_json_str, sec_raw_notes = sec_rule_row
                        sec_filter_cond = None

                        if sec_dsl_json_str:
                            try:
                                sec_dsl_dict = json.loads(sec_dsl_json_str)
                                sec_filter_cond = sec_dsl_dict.get(
                                    "filter_condition"
                                )
                            except Exception:
                                pass

                        if not sec_filter_cond and sec_raw_notes:
                            sec_filter_cond = sec_raw_notes

                        if sec_filter_cond:
                            sec_filter_expr = parse_section_filter_expr(
                                sec_filter_cond,
                                default_table_key,
                                base_df.columns,
                            )
                            if sec_filter_expr is not None:
                                try:
                                    before_cnt = base_df.shape[0]
                                    base_df = base_df.filter(sec_filter_expr)
                                    after_cnt = base_df.shape[0]
                                    print(
                                        "  🎯 [SECTION FILTER APPLIED]"
                                        f" [{sec_filter_cond}] -> Rows filtered"
                                        f" from {before_cnt} down to"
                                        f" {after_cnt}"
                                    )
                                except Exception as e:
                                    print(f"  ⚠️ Section filter error: {e}")

                    total_rows = base_df.shape[0]
                    output_data = {}

                    cursor.execute(
                        "SELECT target_field, data_file, column_letter,"
                        " rule_type, dsl_json, dsl_readable, status, raw_notes"
                        " FROM rule_store WHERE (cu_id = ? OR is_global = 1)"
                        " AND sheet_name = ? AND section_name = ? AND"
                        " target_field != '_SECTION_RULE_' ORDER BY id ASC",
                        (cu_id, current_sheet, sec),
                    )
                    field_rules = cursor.fetchall()

                    for (
                        field,
                        src_file,
                        src_col,
                        rule_type,
                        dsl_json_str,
                        dsl_readable,
                        status,
                        raw_notes,
                    ) in field_rules:
                        dsl_dict = {}
                        if dsl_json_str:
                            try:
                                dsl_dict = json.loads(dsl_json_str)
                            except Exception:
                                pass

                        if rule_type == "DIRECT":
                            target_col = resolve_column_name(
                                src_file,
                                src_col,
                                default_table_key,
                                base_df.columns,
                            )
                            if target_col and target_col in base_df.columns:
                                output_data[field] = base_df[target_col].head(
                                    total_rows
                                )
                            else:
                                cond_expr = evaluate_conditional_rule(
                                    field,
                                    src_file,
                                    src_col,
                                    dsl_dict,
                                    raw_notes,
                                    base_df,
                                    default_table_key,
                                    sec_name=sec,
                                )
                                output_data[field] = (
                                    base_df.with_columns(
                                        cond_expr.alias(field)
                                    )[field]
                                    if cond_expr is not None
                                    else pl.Series([None] * total_rows)
                                )

                        elif rule_type == "NO_MAPPING":
                            output_data[field] = pl.Series([None] * total_rows)

                        elif rule_type == "CONSTANT":
                            val_raw = str(
                                dsl_dict.get("value")
                                if isinstance(dsl_dict, dict)
                                and dsl_dict.get("value")
                                else raw_notes
                            ).strip()
                            clean_val = re.split(
                                r";|\bIF\b", val_raw, flags=re.IGNORECASE
                            )[0]
                            clean_val = re.sub(
                                r"^ASSIGN\s+(ALL\s+)?",
                                "",
                                clean_val,
                                flags=re.IGNORECASE,
                            ).strip()

                            if any(
                                clean_val.upper().startswith(prefix)
                                for prefix in [
                                    "MB.",
                                    "DP.",
                                    "LN.",
                                    "DP-TYPE.",
                                    "LN-TYPE.",
                                    "CU.",
                                ]
                            ):
                                cond_expr = evaluate_conditional_rule(
                                    field,
                                    src_file,
                                    src_col,
                                    dsl_dict,
                                    raw_notes,
                                    base_df,
                                    default_table_key,
                                    sec_name=sec,
                                )
                                output_data[field] = (
                                    base_df.with_columns(
                                        cond_expr.alias(field)
                                    )[field]
                                    if cond_expr is not None
                                    else pl.Series([None] * total_rows)
                                )
                            else:
                                output_data[field] = pl.Series(
                                    [clean_val] * total_rows
                                )

                        elif rule_type in [
                            "CONDITIONAL",
                            "MATRIX_LOOKUP",
                            "UNPARSED",
                        ]:
                            cond_expr = evaluate_conditional_rule(
                                field,
                                src_file,
                                src_col,
                                dsl_dict,
                                raw_notes,
                                base_df,
                                default_table_key,
                                sec_name=sec,
                            )
                            if cond_expr is not None:
                                output_data[field] = base_df.with_columns(
                                    cond_expr.alias(field)
                                )[field]
                            else:
                                output_data[field] = pl.Series(
                                    [None] * total_rows
                                )

                        else:
                            output_data[field] = pl.Series([None] * total_rows)

                    if output_data:
                        res_df = pl.DataFrame(output_data)

                        clean_sheet = re.sub(
                            r'[\\/*?:"<>|]', "_", current_sheet
                        ).replace(" ", "_")
                        clean_sec = re.sub(r'[\\/*?:"<>|]', "_", sec).replace(
                            " ", "_"
                        )
                        out_file = (
                            self.output_dir
                            / f"Expected_{cu_id}_{clean_sheet}_{clean_sec}.csv"
                        )

                        try:
                            res_df.write_csv(out_file)
                            generated_files.append(out_file)
                            print(
                                f"  ✅ Successfully exported Test Data for [{cu_id}]:"
                                f" {out_file.name} ({res_df.shape[0]} rows)"
                            )
                        except PermissionError:
                            print(
                                f"\n❌ ERROR: File '{out_file.name}' IS OPEN IN"
                                " EXCEL!"
                            )
                            print(
                                f"👉 Please CLOSE EXCEL and re-run the"
                                " command!\n"
                            )

        return generated_files