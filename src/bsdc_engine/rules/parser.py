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
    BranchModel,
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

    s = str(text).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = s.replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    return s


def preprocess_notes(notes: str) -> str:
    """Preprocess and fix typos / missing keywords in Excel notes prior to parsing."""
    s = notes.strip()

    s = re.sub(r"\bT\s+HEN\b", "THEN", s, flags=re.IGNORECASE)
    s = re.sub(r"\bI\s+F\b", "IF", s, flags=re.IGNORECASE)
    s = re.sub(r"\bAS\s+SIGN\b", "ASSIGN", s, flags=re.IGNORECASE)

    def insert_then(match):
        if_prefix = match.group(1)
        action = match.group(2)
        if "THEN" not in if_prefix.upper():
            return f"{if_prefix} THEN {action}"
        return match.group(0)

    s = re.sub(
        r"(\bIF\b.*?)\s+\b(LEAVE\s+BLANK|ASSIGN|CREATE|SET|NULL)\b",
        insert_then,
        s,
        flags=re.IGNORECASE,
    )
    return s


def clean_action_val(val_str: str) -> str:
    """Extract clean assigned target value by stripping ASSIGN, CREATE, quotes, and trailing punctuation (; , .)."""
    if not val_str:
        return ""
    s = val_str.strip()
    if s.upper() in ["LEAVE BLANK", "BLANK", "NULL", "NONE"]:
        return ""
    s = re.sub(r"^(ASSIGN|CREATE)\s+(ALL\s+)?", "", s, flags=re.IGNORECASE).strip()
    s = s.rstrip(";,.").strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    s = s.rstrip(";,.").strip()
    return s


def clean_cond_val(cond_str: str, default_col: str) -> tuple[str, str]:
    """Extract column letter and cleaned value list including BLANK for condition evaluation."""
    s = cond_str.strip()
    col_match = re.search(r"\bCOL(?:UMN)?\s+([A-Za-z0-9_]+)\b", s, re.IGNORECASE)
    col = col_match.group(1) if col_match else default_col

    rhs = re.sub(r"^\s*\bCOL(?:UMN)?\s+[A-Za-z0-9_]+\s*(=|<>|!=|IN|IS)?\s*", "", s, flags=re.IGNORECASE).strip()
    has_blank = bool(re.search(r"\b(OR\s+)?(BLANK|NULL|EMPTY)\b", rhs, re.IGNORECASE))

    quoted_vals = re.findall(r'["\']([^"\'\n]+)["\']', rhs)
    if not quoted_vals:
        plain_vals = [
            v.strip() for v in re.split(r"\b(?:OR|AND)\b|,", rhs, flags=re.IGNORECASE)
            if v.strip() and v.strip().upper() not in ["BLANK", "NULL", "EMPTY"]
        ]
        quoted_vals = plain_vals

    vals = [v for v in quoted_vals if v.upper() not in ["BLANK", "NULL", "EMPTY"]]
    if has_blank and "BLANK" not in [v.upper() for v in vals]:
        vals.append("BLANK")

    if vals:
        cond_val_str = ", ".join(vals)
    elif has_blank:
        cond_val_str = "BLANK"
    else:
        cond_val_str = rhs

    return col, cond_val_str


def parse_section_rule(raw_notes: str) -> dict:
    filter_cond = None
    join_rule_model = None

    raw_notes_clean = re.sub(
        r"([0-9A-Za-z])(LINK|JOIN|WHERE|FILTER)\b",
        r"\1 \2",
        raw_notes,
        flags=re.IGNORECASE,
    )

    link_split = re.split(r"\bLINK\b", raw_notes_clean, flags=re.IGNORECASE)
    filter_text = link_split[0]

    filter_match = re.search(
        r"((?:ONLY\s+CONSIDERED\s+.*?|ONLY\s+CREATE\s+.*?|DO\s+NOT\s+CREATE\s+.*?|IF\s+COLUMN\s+[A-Za-z0-9_]+\s*=\s*NULL\s+DO\s+NOT\s+CREATE\s+.*?|IF\s+COLUMN\s+.*?)(?:COLUMN\s+[A-Za-z0-9_]+\s*[^\|\n]+))",
        filter_text,
        re.IGNORECASE,
    )
    if not filter_match:
        filter_match = re.search(
            r"(COLUMN\s+[A-Za-z0-9_]+\s*(?:=|<|>|<>|!=|BEGINS|STARTS|IS)\s*[^\|\n]+)",
            filter_text,
            re.IGNORECASE,
        )

    if filter_match:
        filter_cond = clean_excel_text(filter_match.group(1).split("\n")[0].split("|")[0])

    link_match = re.search(
        r"LINK\s+([A-Za-z0-9_]+)\s+COLUMN\s+([A-Za-z0-9_]+)\s+TO\s+([A-Za-z0-9_]+)\s+COLUMN\s+([A-Za-z0-9_]+)",
        raw_notes_clean,
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


def parse_chained_ifs(notes: str, default_col: str) -> list[dict]:
    s = preprocess_notes(notes)
    if_blocks = re.split(r"\bIF\s+", s, flags=re.IGNORECASE)
    if_blocks = [b.strip() for b in if_blocks if b.strip()]

    parsed_branches = []
    for block in if_blocks:
        then_split = re.split(r"\bTHEN\b", block, maxsplit=1, flags=re.IGNORECASE)
        if len(then_split) < 2:
            continue

        raw_cond = then_split[0].strip()
        then_else_part = then_split[1].strip()

        else_split = re.split(r";?\s*\bELSE\b", then_else_part, maxsplit=1, flags=re.IGNORECASE)
        raw_then = else_split[0].strip()
        raw_else = else_split[1].strip() if len(else_split) > 1 else None

        col_found, cond_val = clean_cond_val(raw_cond, default_col)
        then_val = clean_action_val(raw_then)
        else_val = clean_action_val(raw_else) if raw_else else None

        parsed_branches.append({
            "if_col": col_found,
            "if_val": cond_val,
            "then_val": then_val,
            "else_val": else_val,
            "raw_condition": raw_cond,
        })
    return parsed_branches


def parse_notes_to_dsl(data_file: str, col: str, notes: str) -> dict:
    data_file = clean_excel_text(data_file)
    col = clean_excel_text(col)
    notes = clean_excel_text(notes)
    notes_upper = notes.upper().strip()
    col_upper = col.upper().strip()

    if not col_upper and not notes_upper:
        no_map = NoMappingRuleDSL()
        return {
            "rule_type": "NO_MAPPING",
            "dsl_obj": no_map,
            "dsl_readable": "NO_MAPPING",
            "status": "AUTO_PARSED",
        }

    if "IF " in notes_upper or "IF\n" in notes_upper or notes_upper.startswith("IF"):
        branches = parse_chained_ifs(notes, default_col=col)
        if branches:
            branch_models = [BranchModel(**b) for b in branches]
            cond_dsl = ConditionalRuleDSL(
                if_col=branch_models[0].if_col,
                if_val=branch_models[0].if_val,
                then_val=branch_models[0].then_val,
                else_val=branch_models[0].else_val,
                branches=branch_models,
                raw_condition=notes,
            )

            b_count = len(branch_models)
            if b_count == 1:
                b = branch_models[0]
                readable = f"IF COL_{b.if_col} == '{b.if_val}' THEN '{b.then_val}'"
                if b.else_val:
                    readable += f" ELSE '{b.else_val}'"
            else:
                readable = f"IF ({b_count} CONDITIONS)"

            return {
                "rule_type": "CONDITIONAL",
                "dsl_obj": cond_dsl,
                "dsl_readable": readable,
                "status": "AUTO_PARSED",
            }

    if any(k in notes_upper for k in ["MATRIX", "LOOKUP"]):
        match = re.search(r"ASSIGN\s+([A-Za-z0-9_\\-\\.]+)", notes, re.IGNORECASE)
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

    if notes_upper.startswith("ASSIGN"):
        val = clean_action_val(notes)
        is_field_ref = bool(
            re.match(
                r"^(MB|DP|LN|DP-TYPE|LN-TYPE|CU|CHECK_ACCOUNT_HOLDS|SAVINGS_ACCOUNTS)\.[A-Za-z0-9_\\-]+",
                val,
                re.IGNORECASE,
            )
        )

        if is_field_ref:
            unparsed_dsl = UnparsedRuleDSL(raw_notes=notes)
            return {
                "rule_type": "CROSS_FIELD_REF",
                "dsl_obj": unparsed_dsl,
                "dsl_readable": f"REF('{val}')",
                "status": "AUTO_PARSED",
            }
        else:
            const_dsl = ConstantRuleDSL(value=val)
            return {
                "rule_type": "CONSTANT",
                "dsl_obj": const_dsl,
                "dsl_readable": f"CONST('{val}')",
                "status": "AUTO_PARSED",
            }

    if col_upper and not notes_upper:
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

        if any(kw in row_str.lower() for kw in ["table)", "(mb-", "(dp", "table", "section"]):
            clean_sec_name = row_str.split("ONLY CONSIDERED")[0].split("LINK")[0].split("|")[0].strip()
            if clean_sec_name and len(clean_sec_name) < 100:
                current_section = clean_sec_name
                active_data_file = ""
                print(f"📌 Scanning Data Section: [{current_section}]")

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

        conn.execute("DELETE FROM rule_store WHERE cu_id = ?", (cu_id,))

        for sheet in valid_sheets:
            try:
                raw_df = xl.parse(sheet, header=None)
                process_mapping_sheet(raw_df, sheet_name=sheet, conn=conn, cu_id=cu_id)
            except Exception as e:
                print(f"❌ Error processing sheet [{sheet}]: {e}")
        conn.commit()