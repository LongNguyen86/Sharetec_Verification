import json
import re
import sqlite3
import unicodedata
from pathlib import Path
import pandas as pd

from src.bsdc_engine.rules.dsl import (
    SectionRuleDSL,
    JoinRuleModel,
    ConditionalRuleDSL,
    DirectRuleDSL,
    ConstantRuleDSL,
    MatrixLookupRuleDSL,
    NoMappingRuleDSL,
    UnparsedRuleDSL,
)
from src.bsdc_engine.rules.store import RuleStore


def clean_excel_text(text) -> str:
    """Generic Unicode normalization and string cleanup without any CU-specific business rules."""
    if text is None or pd.isna(text):
        return ""

    s = str(text).replace("\xa0", " ").strip()
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    return s


def parse_section_rule(raw_notes: str) -> dict:
    """Parse section-level filter conditions and table joins using Pydantic v2."""
    filter_cond = None
    join_rule_model = None

    filter_match = re.search(
        r"((?:ONLY\s+CONSIDERED\s+.*?|ONLY\s+CREATE\s+.*?|DO\s+NOT\s+CREATE\s+.*?|IF\s+COLUMN\s+[A-Za-z0-9_]+\s*=\s*NULL\s+DO\s+NOT\s+CREATE\s+.*?|IF\s+COLUMN\s+.*?)(?:COLUMN\s+[A-Za-z0-9_]+\s*[^\|\n]+))",
        raw_notes,
        re.IGNORECASE,
    )
    if not filter_match:
        filter_match = re.search(
            r"(COLUMN\s+[A-Za-z0-9_]+\s*(?:=|<|>|<>|!=|BEGINS|STARTS|IS)\s*[^\|\n]+)",
            raw_notes,
            re.IGNORECASE,
        )

    if filter_match:
        filter_cond = clean_excel_text(filter_match.group(1).split("\n")[0].split("|")[0])

    link_match = re.search(
        r"LINK\s+([A-Za-z0-9_]+)\s+COLUMN\s+([A-Za-z0-9_]+)\s+TO\s+([A-Za-z0-9_]+)\s+COLUMN\s+([A-Za-z0-9_]+)",
        raw_notes,
        re.IGNORECASE,
    )
    if link_match:
        join_rule_model = JoinRuleModel(
            source_file=clean_excel_text(link_match.group(1)),
            source_col=clean_excel_text(link_match.group(2)),
            target_file=clean_excel_text(link_match.group(3)),
            target_col=clean_excel_text(link_match.group(4)),
        )

    sec_dsl = SectionRuleDSL(
        filter_condition=filter_cond,
        join_rule=join_rule_model,
        raw_notes=raw_notes,
    )

    readable_parts = []
    if filter_cond:
        readable_parts.append(f"FILTER({filter_cond})")
    if join_rule_model:
        readable_parts.append(
            f"JOIN({join_rule_model.source_file}.{join_rule_model.source_col} = {join_rule_model.target_file}.{join_rule_model.target_col})"
        )

    readable_str = " | ".join(readable_parts)

    return {
        "rule_type": "SECTION_RULE",
        "dsl_obj": sec_dsl,
        "dsl_readable": readable_str if readable_str else "SECTION_HEADER_RULE",
        "status": "AUTO_PARSED",
    }


def parse_notes_to_dsl(data_file: str, col: str, notes: str) -> dict:
    """Parse field mapping notes into typed Pydantic v2 models."""
    data_file = clean_excel_text(data_file)
    col = clean_excel_text(col)
    notes = clean_excel_text(notes)
    notes_upper = notes.upper()

    # Case 1: Empty mapping
    if not col and not notes:
        no_map = NoMappingRuleDSL()
        return {
            "rule_type": "NO_MAPPING",
            "dsl_obj": no_map,
            "dsl_readable": "NO_MAPPING",
            "status": "AUTO_PARSED",
        }

    # Case 2: Direct Mapping
    has_conditional_keywords = any(
        kw in notes_upper for kw in ["IF COLUMN", "IF ", "ASSIGN", "MATRIX", "LOOKUP", "WHEN", "ACCUMULATE"]
    )
    if col and not has_conditional_keywords:
        direct_dsl = DirectRuleDSL(
            source_file=data_file,
            source_column=col,
        )
        return {
            "rule_type": "DIRECT",
            "dsl_obj": direct_dsl,
            "dsl_readable": f"{data_file}.{col}" if data_file else f"COL_{col}",
            "status": "AUTO_PARSED",
        }

    # Case 3: Simple Conditional Rule
    if "IF COLUMN" in notes_upper and "ACCUMULATE" not in notes_upper:
        cond_match = re.search(
            r"IF\s+COLUMN\s+([A-Za-z0-9]+)\s*=\s*([A-Za-z0-9_\-\.\/]+)\s+THEN\s+(.*?)\s*(?:;|\b)\s*ELSE\s+(.*)",
            notes,
            re.IGNORECASE,
        )

        if cond_match:
            cond_dsl = ConditionalRuleDSL(
                if_col=clean_excel_text(cond_match.group(1)),
                if_val=clean_excel_text(cond_match.group(2)),
                then_val=clean_excel_text(cond_match.group(3)),
                else_val=clean_excel_text(cond_match.group(4)),
                raw_condition=notes,
            )
            return {
                "rule_type": "CONDITIONAL",
                "dsl_obj": cond_dsl,
                "dsl_readable": f"IF COL_{cond_dsl.if_col}=='{cond_dsl.if_val}' THEN '{cond_dsl.then_val}' ELSE '{cond_dsl.else_val}'",
                "status": "AUTO_PARSED",
            }

    # Case 4: Matrix Lookup
    if any(k in notes_upper for k in ["MATRIX", "LOOKUP"]):
        match = re.search(r"ASSIGN\s+([A-Za-z0-9_\-\.]+)", notes, re.IGNORECASE)
        ref = match.group(1) if match else "MATRIX_LOOKUP"

        matrix_dsl = MatrixLookupRuleDSL(
            target_ref=ref,
            source_file=data_file,
            source_column=col,
            raw_notes=notes,
        )
        return {
            "rule_type": "MATRIX_LOOKUP",
            "dsl_obj": matrix_dsl,
            "dsl_readable": f"LOOKUP('{ref}')",
            "status": "AUTO_PARSED",
        }

    # Case 5: Constant Assignment OR Cross-Table Field Reference
    if notes_upper.startswith("ASSIGN") and "IF COLUMN" not in notes_upper:
        val = re.sub(r"^ASSIGN\s+(ALL\s+)?", "", notes, flags=re.IGNORECASE).strip()
        is_field_ref = bool(
            re.match(
                r"^(MB|DP|LN|DP-TYPE|LN-TYPE|CU|CHECK_ACCOUNT_HOLDS|SAVINGS_ACCOUNTS)\.[A-Za-z0-9_\-]+",
                val,
                re.IGNORECASE,
            )
        )

        if is_field_ref:
            unparsed_dsl = UnparsedRuleDSL(raw_notes=notes)
            return {
                "rule_type": "CONDITIONAL" if col else "UNPARSED",
                "dsl_obj": unparsed_dsl,
                "dsl_readable": f"REF('{val}')",
                "status": "AUTO_PARSED" if col else "NEEDS_REVIEW",
            }
        else:
            const_dsl = ConstantRuleDSL(value=val)
            return {
                "rule_type": "CONSTANT",
                "dsl_obj": const_dsl,
                "dsl_readable": f"CONST('{val}')",
                "status": "AUTO_PARSED",
            }

    # Case 6: Complex or Multi-IF free-text -> Pass to LLM / AI Engine
    unparsed_dsl = UnparsedRuleDSL(raw_notes=notes)
    return {
        "rule_type": "UNPARSED",
        "dsl_obj": unparsed_dsl,
        "dsl_readable": "NEEDS_LLM_PARSING",
        "status": "NEEDS_REVIEW",
    }


def process_mapping_sheet(
    raw_df: pd.DataFrame, sheet_name: str, conn: sqlite3.Connection, cu_id: str
):
    """Process a single excel mapping DataFrame and persist Pydantic validated rules to SQLite."""
    print(f"\n🔄 Reading Mapping Sheet [{sheet_name}] for CU: [{cu_id}]...")

    cursor = conn.cursor()

    stats = {
        "reused": 0,
        "auto_parsed": 0,
        "no_mapping": 0,
        "needs_review": 0,
        "section_rules": 0,
    }
    current_section = f"{sheet_name} - General"
    active_data_file = ""

    field_col_idx = None
    data_file_col_idx = None
    col_letter_idx = None
    notes_col_idx = None

    for idx, row in raw_df.iterrows():
        row_vals_clean = [clean_excel_text(v) for v in row.values if pd.notna(v) and str(v).strip()]
        row_str = " | ".join(row_vals_clean)

        # Detect active header column positions (Strictly IGNORE "Previous" history columns)
        row_vals_lower = [v.lower() for v in row_vals_clean]
        if "field" in row_vals_lower and any("notes" in v or "additional" in v for v in row_vals_lower):
            field_col_idx = None
            data_file_col_idx = None
            col_letter_idx = None
            notes_col_idx = None

            for c_idx, val in enumerate(row.values):
                val_str = clean_excel_text(val).lower()

                if "previous" in val_str or "old" in val_str or "legacy" in val_str:
                    continue

                if val_str == "field" and field_col_idx is None:
                    field_col_idx = c_idx
                elif ("data file" in val_str or "source file" in val_str) and data_file_col_idx is None:
                    data_file_col_idx = c_idx
                elif ("column" in val_str or "source col" in val_str) and col_letter_idx is None:
                    col_letter_idx = c_idx
                elif ("notes" in val_str or "additional" in val_str) and notes_col_idx is None:
                    notes_col_idx = c_idx

        f_idx = field_col_idx if field_col_idx is not None else 1
        d_idx = data_file_col_idx if data_file_col_idx is not None else 5
        c_idx = col_letter_idx if col_letter_idx is not None else 6
        n_idx = notes_col_idx if notes_col_idx is not None else 7

        # Detect Data Section Headers
        if any(kw in row_str.lower() for kw in ["table)", "(mb-", "(dp", "table", "section"]):
            clean_sec_name = row_str.split("ONLY CONSIDERED")[0].split("LINK")[0].split("|")[0].strip()
            if clean_sec_name and len(clean_sec_name) < 100:
                current_section = clean_sec_name
                active_data_file = ""
                print(f"📌 Scanning Data Section: [{current_section}]")

        # Save _SECTION_RULE_ if Filter or Join is present
        if any(kw in row_str.upper() for kw in ["ONLY CONSIDERED", "ONLY CREATE", "DO NOT CREATE", "LINK "]):
            parsed_sec = parse_section_rule(row_str)
            sec_dsl_obj: SectionRuleDSL = parsed_sec["dsl_obj"]

            if sec_dsl_obj.filter_condition or sec_dsl_obj.join_rule:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO rule_store 
                    (cu_id, sheet_name, section_name, target_field, raw_notes, data_file, column_letter, rule_type, dsl_json, dsl_readable, status, parsed_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        cu_id,
                        sheet_name,
                        current_section,
                        "_SECTION_RULE_",
                        row_str,
                        "",
                        "",
                        parsed_sec["rule_type"],
                        sec_dsl_obj.model_dump_json(),
                        parsed_sec["dsl_readable"],
                        parsed_sec["status"],
                        "SYSTEM",
                    ),
                )
                stats["section_rules"] += 1
                print(f"   🚩 [SECTION_RULE] Locking filter for [{current_section}] -> {parsed_sec['dsl_readable']}")
                continue

        target_field = clean_excel_text(row.iloc[f_idx]) if len(row) > f_idx else ""

        is_valid_field = (
            bool(target_field)
            and target_field.lower() not in ["nan", "none", "field", "label"]
        )

        if not is_valid_field:
            continue

        raw_data_file = clean_excel_text(row.iloc[d_idx]) if len(row) > d_idx else ""
        col = clean_excel_text(row.iloc[c_idx]) if len(row) > c_idx else ""
        raw_notes = clean_excel_text(row.iloc[n_idx]) if len(row) > n_idx else ""

        if raw_data_file and raw_data_file.lower() not in ["nan", "none"]:
            active_data_file = raw_data_file
        data_file = active_data_file

        if not raw_notes or raw_notes.lower() in ["nan", "none"]:
            for cell_val in row_vals_clean:
                cell_upper = cell_val.upper()
                if any(kw in cell_upper for kw in ["ASSIGN", "MATRIX", "IF COLUMN", "LOOKUP", "MONTH ="]):
                    raw_notes = cell_val
                    break

        if data_file.lower() in ["nan", "none"]: data_file = ""
        if col.lower() in ["nan", "none"]: col = ""
        if raw_notes.lower() in ["nan", "none"]: raw_notes = ""

        # Use INSERT OR REPLACE INTO to strictly prevent UNIQUE constraint failure errors
        parsed_res = parse_notes_to_dsl(data_file, col, raw_notes)
        rule_dsl_obj = parsed_res["dsl_obj"]

        cursor.execute(
            """
            INSERT OR REPLACE INTO rule_store 
            (cu_id, sheet_name, section_name, target_field, raw_notes, data_file, column_letter, rule_type, dsl_json, dsl_readable, status, parsed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                cu_id,
                sheet_name,
                current_section,
                target_field,
                raw_notes,
                data_file,
                col,
                parsed_res["rule_type"],
                rule_dsl_obj.model_dump_json(),
                parsed_res["dsl_readable"],
                parsed_res["status"],
                "SYSTEM",
            ),
        )

        if parsed_res["rule_type"] == "NO_MAPPING":
            stats["no_mapping"] += 1
        elif parsed_res["status"] == "AUTO_PARSED":
            stats["auto_parsed"] += 1
        else:
            stats["needs_review"] += 1

    print("=" * 50)
    print(f"📊 SUMMARY FOR SHEET [{sheet_name}]: Auto-Parsed: {stats['auto_parsed']} | Section Rules: {stats['section_rules']} | Needs Review: {stats['needs_review']}")
    print("=" * 50)


def parse_all_mapping_sheets(
    raw_dir: Path, cu_id: str | None = None, db_path: Path | None = None
):
    mapping_files = [
        f for f in Path(raw_dir).glob("*.xlsx")
        if "mapping" in f.name.lower() and not f.name.startswith("~$")
    ]
    if not mapping_files:
        raise FileNotFoundError(f"No mapping file found in {raw_dir}")

    excel_path = mapping_files[0]
    print(f"📖 Opening Excel file: {excel_path.name}")

    if not cu_id:
        cu_id = excel_path.stem.split()[0].replace("-", "").replace("_", "").upper()

    xl = pd.ExcelFile(excel_path)
    ignore_sheets = ["cover", "index", "readme", "instruction", "instructions", "summary"]
    valid_sheets = [s for s in xl.sheet_names if s.strip().lower() not in ignore_sheets]

    store = RuleStore(db_path=db_path)
    with store.get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        for sheet in valid_sheets:
            try:
                raw_df = xl.parse(sheet, header=None)
                process_mapping_sheet(raw_df, sheet_name=sheet, conn=conn, cu_id=cu_id)
            except Exception as e:
                print(f"❌ Error processing sheet [{sheet}]: {e}")
        conn.commit()