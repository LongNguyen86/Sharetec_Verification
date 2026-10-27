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
    ConcatRuleDSL,
    DirectRuleDSL,
    ConstantRuleDSL,
    MatrixLookupRuleDSL,
    NoMappingRuleDSL,
    UnparsedRuleDSL,
)
from src.bsdc_engine.rules.store import RuleStore

ENGLISH_STOP_WORDS = {
    "A", "AN", "THE", "TO", "IN", "ON", "AT", "BY", "FOR", "WITH", "ABOUT",
    "AGAINST", "BETWEEN", "INTO", "THROUGH", "DURING", "BEFORE", "AFTER",
    "ABOVE", "BELOW", "FROM", "UP", "DOWN", "OF", "OFF", "OVER", "UNDER",
    "AGAIN", "FURTHER", "THEN", "ONCE", "HERE", "THERE", "WHEN", "WHERE",
    "WHY", "HOW", "ALL", "ANY", "BOTH", "EACH", "FEW", "MORE", "MOST",
    "OTHER", "SOME", "SUCH", "NO", "NOR", "NOT", "ONLY", "OWN", "SAME",
    "SO", "THAN", "TOO", "VERY", "S", "T", "CAN", "WILL", "JUST", "DON",
    "SHOULD", "NOW", "AND", "OR", "IF", "IS", "IT", "AS", "USE", "SET",
    "COL", "COLUMN", "ASSIGN", "CREATE", "LINK", "JOIN", "MATRIX", "LOOKUP"
}

# Narrative instruction keywords in action/then clauses that indicate complex logic requiring LLM parsing
NARRATIVE_ACTION_KEYWORDS = [
    "ADD", "BEGINNING", "STARTING", "EXAMPLE", "DIGIT", "NON MEMBER",
    "MEMBER TYPE", "SUBSTRING", "EXTRACT", "CALCULATE", "SUM", "COUNT",
    "INCREMENT", "LOOKUP", "WHERE", "AFTER", "BEFORE", "PREFIX", "SUFFIX"
]


def clean_excel_text(text) -> str:
    """Generic Unicode normalization and string cleanup."""
    if text is None or pd.isna(text):
        return ""
    s = str(text).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = s.replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")


def clean_action_val(val_str: str) -> str:
    """Extract clean assigned target value, converting BLANK indicators to empty string."""
    if not val_str:
        return ""
    s = val_str.strip()
    s = re.sub(r"^(ASSIGN|CREATE)\s+(ALL\s+)?", "", s, flags=re.IGNORECASE).strip()
    s = s.rstrip(";,.").strip()
    if s.upper() in ["LEAVE BLANK", "BLANK", "NULL", "NONE", "''", '""']:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    s = s.rstrip(";,.").strip()
    if s.upper() in ["LEAVE BLANK", "BLANK", "NULL", "NONE", "''", '""']:
        return ""
    return s


def is_narrative_action(action_str: str) -> bool:
    """Check if the action/then clause contains natural language instructions that should be parsed by LLM."""
    if not action_str:
        return False
    act_upper = action_str.upper()
    return any(re.search(r"\b" + re.escape(kw) + r"\b", act_upper) for kw in NARRATIVE_ACTION_KEYWORDS)


def is_simple_constant(val_str: str) -> bool:
    """Check if assigned value is a clean, simple constant literal."""
    if not val_str:
        return True
    val_upper = val_str.upper()
    if any(re.search(r"\b" + re.escape(kw) + r"\b", val_upper) for kw in NARRATIVE_ACTION_KEYWORDS):
        return False
    if "(" in val_str or ")" in val_str or len(val_str) > 40 or "=" in val_str:
        return False
    return True


def preprocess_notes(notes: str) -> str:
    """Preprocess notes, fixing common typos and commas before action keywords."""
    s = notes.strip()
    s = re.sub(r"\bT\s+HEN\b", "THEN", s, flags=re.IGNORECASE)
    s = re.sub(r"\bI\s+F\b", "IF", s, flags=re.IGNORECASE)
    s = re.sub(r"\bAS\s+SIGN\b", "ASSIGN", s, flags=re.IGNORECASE)

    s = re.sub(
        r"(\bIF\b.*?)\s*,\s*(?:THEN\s+)?(LEAVE\s+BLANK|ASSIGN|CREATE|SET|NULL)\b",
        r"\1 THEN \2",
        s,
        flags=re.IGNORECASE,
    )

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


def expand_shorthand_conditions(cond_str: str) -> str:
    """Expand shorthand multi-column equalities: COLUMN W AND COLUMN X = BLANK -> COLUMN W = BLANK AND COLUMN X = BLANK."""
    regex = r"((?:(?:COLUMN|COL)\s+[A-Za-z0-9_]+\s+(?:AND|,)\s+)+)(?:COLUMN|COL)\s+([A-Za-z0-9_]+)\s*(=|==|!=|<>|IS)\s*(\"[^\"]*\"|'[^']*'|[A-Za-z0-9_\-\.]+)"

    def _replace_shorthand(match):
        prefix_cols_str = match.group(1)
        last_col = match.group(2)
        op = match.group(3)
        val = match.group(4)
        cols = re.findall(r"\b(?:COLUMN|COL)\s+([A-Za-z0-9_]+)\b", prefix_cols_str, re.IGNORECASE)
        cols.append(last_col)
        return " AND ".join([f"COLUMN {c} {op} {val}" for c in cols])

    return re.sub(regex, _replace_shorthand, cond_str, flags=re.IGNORECASE)


def parse_boolean_tree(raw_cond: str, default_col: str) -> list[list[dict]]:
    """Parse conditional logic into Disjunctive Normal Form (DNF): list of OR-groups of AND-conditions."""
    expanded = expand_shorthand_conditions(raw_cond.strip())

    or_parts = re.split(
        r"\s+\bOR\b\s+(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)(?=(?:[^']*'[^']*')*[^']*$)",
        expanded,
        flags=re.IGNORECASE,
    )

    tree = []
    for or_p in or_parts:
        and_parts = re.split(
            r"\s+\bAND\b\s+(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)(?=(?:[^']*'[^']*')*[^']*$)",
            or_p,
            flags=re.IGNORECASE,
        )
        and_list = []
        for ap in and_parts:
            ap = ap.strip()
            if not ap:
                continue

            # Compare two columns directly: COLUMN A = COLUMN B
            m_col_col = re.search(
                r"\b(?:COLUMN|COL)\s+([A-Za-z0-9_]+)\s*(=|==|!=|<>)\s*(?:COLUMN|COL)\s+([A-Za-z0-9_]+)\b",
                ap,
                re.IGNORECASE,
            )
            if m_col_col:
                and_list.append({
                    "col": m_col_col.group(1).upper(),
                    "op": "!=" if m_col_col.group(2) in ["!=", "<>"] else "==",
                    "val": m_col_col.group(3).upper(),
                    "is_col_ref": True,
                })
                continue

            m_col = re.search(r"\b(?:COLUMN|COL)\s+([A-Za-z0-9_]+)\b", ap, re.IGNORECASE)
            col_name = m_col.group(1).upper() if m_col else (default_col.upper() if default_col else "")

            is_ne = any(kw in ap for kw in ["<>", "!="]) or re.search(r"\bNOT\b", ap, re.IGNORECASE)
            op = "!=" if is_ne else "=="

            rhs = re.sub(
                r"^\s*\b(?:COLUMN|COL)\s+[A-Za-z0-9_]+\s*(=|==|!=|<>|IN|IS)?\s*",
                "",
                ap,
                flags=re.IGNORECASE,
            ).strip()

            if not rhs and not m_col and default_col:
                if "NO " in ap.upper() or "BLANK" in ap.upper() or "EMPTY" in ap.upper():
                    rhs = "BLANK"
                else:
                    rhs = ap

            and_list.append({
                "col": col_name,
                "op": op,
                "val": rhs,
                "is_col_ref": False,
            })

        if and_list:
            tree.append(and_list)

    return tree


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


def parse_chained_ifs(notes: str, default_col: str) -> list[dict] | None:
    """
    Parse simple, structured IF-blocks into executable branches.
    Returns None if any branch contains complex narrative instructions that should be handed to LLM.
    """
    s = preprocess_notes(notes)
    if_blocks = re.split(r"\bIF\s+", s, flags=re.IGNORECASE)
    if_blocks = [b.strip() for b in if_blocks if b.strip()]

    parsed_branches = []

    for block in if_blocks:
        then_split = re.split(r"\bTHEN\b", block, maxsplit=1, flags=re.IGNORECASE)
        if len(then_split) < 2:
            return None

        raw_cond = then_split[0].strip()
        then_else_part = then_split[1].strip()

        else_split = re.split(r";?\s*\bELSE\b", then_else_part, maxsplit=1, flags=re.IGNORECASE)
        raw_then = else_split[0].strip()
        raw_else = else_split[1].strip() if len(else_split) > 1 else None

        # Clean boundary: If the action clause contains narrative instructions, hand over to LLM
        if is_narrative_action(raw_then) or (raw_else and is_narrative_action(raw_else)):
            return None

        condition_tree = parse_boolean_tree(raw_cond, default_col)
        then_val = clean_action_val(raw_then)
        else_val = clean_action_val(raw_else) if raw_else else None

        primary_if_col = default_col
        primary_if_val = ""
        if condition_tree and condition_tree[0]:
            primary_if_col = condition_tree[0][0]["col"]
            primary_if_val = condition_tree[0][0]["val"]

        parsed_branches.append({
            "if_col": primary_if_col,
            "if_val": primary_if_val,
            "then_val": then_val,
            "else_val": else_val,
            "raw_condition": raw_cond,
            "condition_tree": condition_tree,
        })

    return parsed_branches


def parse_notes_to_dsl(data_file: str, col: str, notes: str) -> dict:
    """
    Parse notes to DSL:
    - Pure, structured logic is auto-parsed deterministically.
    - Narrative text, complex multi-step instructions, or unparseable patterns are cleanly routed to LLM.
    """
    data_file = clean_excel_text(data_file)
    col = clean_excel_text(col)
    notes = clean_excel_text(notes)
    notes_upper = notes.upper().strip()
    col_upper = col.upper().strip()

    # 1. NO_MAPPING: Empty column and empty notes
    if not col_upper and not notes_upper:
        return {
            "rule_type": "NO_MAPPING",
            "dsl_obj": NoMappingRuleDSL(),
            "dsl_readable": "NO_MAPPING",
            "status": "AUTO_PARSED",
        }

    # 2. Strict CONCAT: Standard explicit '+' expressions
    target_concat_str = col if "+" in col else (notes if "+" in notes else "")
    if target_concat_str and not any(kw in notes_upper for kw in ["LINK", "JOIN", "MATRIX", "LOOKUP", "DECIMAL", "ELEMENT"]):
        parts = [p.strip() for p in target_concat_str.split("+")]
        valid_cols = []
        is_pure_concat = True
        for p in parts:
            p_clean = re.sub(r"^(COL|COLUMN)\s+", "", p, flags=re.IGNORECASE).strip().upper()
            if re.match(r"^[A-Z]{1,2}$", p_clean) and p_clean not in ENGLISH_STOP_WORDS:
                valid_cols.append(p_clean)
            else:
                is_pure_concat = False
                break
        if is_pure_concat and len(valid_cols) >= 2:
            concat_dsl = ConcatRuleDSL(cols=valid_cols, delimiter=" ")
            return {
                "rule_type": "CONCAT",
                "dsl_obj": concat_dsl,
                "dsl_readable": f"CONCAT({', '.join(valid_cols)})",
                "status": "AUTO_PARSED",
            }

    # 3. CONDITIONAL: Handle clean, structured IF ... THEN ... ELSE logic
    if "IF " in notes_upper or "IF\n" in notes_upper or notes_upper.startswith("IF"):
        # Reserve LLM parsing strictly for narrative cross-table queries or custom aggregations
        if any(kw in notes_upper for kw in ["LINK", "JOIN", "MATRIX", "DECIMAL", "ELEMENT", "LOOKUP", "SUM(", "COUNT("]):
            return {
                "rule_type": "UNPARSED",
                "dsl_obj": UnparsedRuleDSL(raw_notes=notes),
                "dsl_readable": "NEEDS_LLM_PARSING",
                "status": "NEEDS_REVIEW",
            }

        branches = parse_chained_ifs(notes, default_col=col)
        # If branches is None, it means narrative text was detected -> cleanly delegate to LLM
        if branches is not None and len(branches) > 0:
            branch_models = [BranchModel(**b) for b in branches]
            cond_dsl = ConditionalRuleDSL(
                if_col=branch_models[0].if_col if branch_models else col,
                if_val=branch_models[0].if_val if branch_models else "",
                then_val=branch_models[0].then_val if branch_models else "",
                else_val=branch_models[0].else_val if branch_models else None,
                branches=branch_models,
                raw_condition=notes,
            )

            # Build human-readable representation for QA review
            if len(branch_models) == 1:
                b = branch_models[0]
                readable = f"IF {b.raw_condition} THEN '{b.then_val}'"
                if b.else_val:
                    readable += f" ELSE '{b.else_val}'"
            else:
                readable = f"IF ({len(branch_models)} CONDITIONS)"

            return {
                "rule_type": "CONDITIONAL",
                "dsl_obj": cond_dsl,
                "dsl_readable": readable,
                "status": "AUTO_PARSED",
            }
        else:
            return {
                "rule_type": "UNPARSED",
                "dsl_obj": UnparsedRuleDSL(raw_notes=notes),
                "dsl_readable": "NEEDS_LLM_PARSING",
                "status": "NEEDS_REVIEW",
            }

    # 4. MATRIX_LOOKUP: Standard lookup references
    if any(k in notes_upper for k in ["MATRIX", "LOOKUP"]):
        if not any(kw in notes_upper for kw in ["LINK", "JOIN", "DECIMAL", "ELEMENT"]):
            match = re.search(r"ASSIGN\s+([A-Za-z0-9_\-\.]+)", notes, re.IGNORECASE)
            ref = match.group(1) if match else "MATRIX_LOOKUP"
            matrix_dsl = MatrixLookupRuleDSL(target_ref=ref, source_file=data_file, source_column=col, raw_notes=notes)
            return {
                "rule_type": "MATRIX_LOOKUP",
                "dsl_obj": matrix_dsl,
                "dsl_readable": f"LOOKUP('{ref}')",
                "status": "AUTO_PARSED",
            }

    # 5. ASSIGN / CREATE: Simple literals are auto-parsed, narrative instructions go to LLM
    if notes_upper.startswith("ASSIGN") or notes_upper.startswith("CREATE"):
        val = clean_action_val(notes)
        is_field_ref = bool(re.match(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$", val, re.IGNORECASE))

        if is_field_ref or not is_simple_constant(val):
            return {
                "rule_type": "UNPARSED",
                "dsl_obj": UnparsedRuleDSL(raw_notes=notes),
                "dsl_readable": "NEEDS_LLM_PARSING",
                "status": "NEEDS_REVIEW",
            }
        else:
            return {
                "rule_type": "CONSTANT",
                "dsl_obj": ConstantRuleDSL(value=val),
                "dsl_readable": f"CONST('{val}')",
                "status": "AUTO_PARSED",
            }

    # 6. DIRECT: Standard column mapping without notes
    if col_upper and not notes_upper:
        return {
            "rule_type": "DIRECT",
            "dsl_obj": DirectRuleDSL(source_file=data_file, source_column=col),
            "dsl_readable": f"{data_file}.{col}" if data_file else f"COL_{col}",
            "status": "AUTO_PARSED",
        }

    # 7. Fallback: Everything else goes to LLM
    return {
        "rule_type": "UNPARSED",
        "dsl_obj": UnparsedRuleDSL(raw_notes=notes),
        "dsl_readable": "NEEDS_LLM_PARSING",
        "status": "NEEDS_REVIEW",
    }


def process_mapping_sheet(
    raw_df: pd.DataFrame, sheet_name: str, conn: sqlite3.Connection, cu_id: str
):
    print(f"\nReading Mapping Sheet [{sheet_name}] for CU: [{cu_id}]...")
    cursor = conn.cursor()

    stats = {
        "auto_parsed": 0,
        "section_rules": 0,
        "needs_review": 0,
        "no_mapping": 0,
    }

    current_section = f"{sheet_name} - General"
    active_data_file = ""
    section_rule_counter = 0

    field_col_idx = 1
    data_file_col_idx = 5
    col_letter_idx = 6
    notes_col_idx = 7

    for idx, row in raw_df.iterrows():
        row_vals_clean = [
            clean_excel_text(v) for v in row.values if pd.notna(v) and str(v).strip()
        ]
        if not row_vals_clean:
            continue

        row_str = " | ".join(row_vals_clean)

        row_vals_lower = [clean_excel_text(v).lower() for v in row.values]
        if "field" in row_vals_lower and any(
            "notes" in v or "additional" in v for v in row_vals_lower
        ):
            f_found, d_found, c_found, n_found = None, None, None, None
            for c_idx, val in enumerate(row.values):
                val_str = clean_excel_text(val).lower()
                if any(skip_kw in val_str for skip_kw in ["previous", "old", "legacy", "update"]):
                    continue

                if val_str == "field" and f_found is None: f_found = c_idx
                elif any(kw in val_str for kw in ["data file", "source file"]) and d_found is None: d_found = c_idx
                elif any(kw in val_str for kw in ["column", "source col"]) and c_found is None: c_found = c_idx
                elif any(kw in val_str for kw in ["notes", "additional"]) and n_found is None: n_found = c_idx

            if f_found is not None: field_col_idx = f_found
            if d_found is not None: data_file_col_idx = d_found
            if c_found is not None: col_letter_idx = c_found
            if n_found is not None: notes_col_idx = n_found
            continue

        if any(
            kw in row_str.upper()
            for kw in ["ONLY CONSIDERED", "ONLY CREATE", "DO NOT CREATE", "LINK "]
        ):
            parsed_sec = parse_section_rule(row_str)
            sec_dsl_obj: SectionRuleDSL = parsed_sec["dsl_obj"]

            if sec_dsl_obj.filter_condition or sec_dsl_obj.join_rule:
                section_rule_counter += 1
                sec_target_field = f"_SECTION_RULE_{section_rule_counter}"
                cursor.execute(
                    """
                    INSERT INTO rule_store 
                    (cu_id, sheet_name, section_name, target_field, raw_notes, data_file, column_letter, rule_type, dsl_json, dsl_readable, status, parsed_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cu_id, sheet_name, current_section, sec_target_field, row_str,
                        "", "", parsed_sec["rule_type"], sec_dsl_obj.model_dump_json(),
                        parsed_sec["dsl_readable"], parsed_sec["status"], "SYSTEM",
                    ),
                )
                stats["section_rules"] += 1
                continue

        target_field = clean_excel_text(row.iloc[field_col_idx]) if len(row) > field_col_idx else ""

        if not target_field or target_field.lower() in ["nan", "none", "field", "label"]:
            cand_title = clean_excel_text(row.iloc[0]) if len(row) > 0 else ""
            if not cand_title and row_vals_clean:
                cand_title = row_vals_clean[0]

            cand_lower = cand_title.lower()
            if cand_title and not any(kw in cand_lower for kw in ["label", "field", "notes", "data file", "converted"]):
                if len(cand_title) < 120:
                    current_section = cand_title
                    active_data_file = ""
                    section_rule_counter = 0
                    print(f"Scanning Data Section: [{current_section}]")
            continue

        raw_data_file = clean_excel_text(row.iloc[data_file_col_idx]) if len(row) > data_file_col_idx else ""
        col = clean_excel_text(row.iloc[col_letter_idx]) if len(row) > col_letter_idx else ""
        raw_notes = clean_excel_text(row.iloc[notes_col_idx]) if len(row) > notes_col_idx else ""

        if raw_data_file and raw_data_file.lower() not in ["nan", "none"]:
            active_data_file = raw_data_file
        data_file = active_data_file

        if not raw_notes or raw_notes.lower() in ["nan", "none"]:
            for cell_val in row_vals_clean:
                cell_upper = cell_val.upper()
                if any(kw in cell_upper for kw in ["ASSIGN", "MATRIX", "IF COLUMN", "LOOKUP", "MONTH ="]):
                    raw_notes = cell_val
                    break

        parsed_res = parse_notes_to_dsl(data_file, col, raw_notes)
        rule_dsl_obj = parsed_res["dsl_obj"]

        cursor.execute(
            """
            INSERT OR REPLACE INTO rule_store 
            (cu_id, sheet_name, section_name, target_field, raw_notes, data_file, column_letter, rule_type, dsl_json, dsl_readable, status, parsed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cu_id, sheet_name, current_section, target_field, raw_notes,
                data_file, col, parsed_res["rule_type"], rule_dsl_obj.model_dump_json(),
                parsed_res["dsl_readable"], parsed_res["status"], "SYSTEM",
            ),
        )

        if parsed_res["rule_type"] == "NO_MAPPING":
            stats["no_mapping"] += 1
        elif parsed_res["status"] == "AUTO_PARSED":
            stats["auto_parsed"] += 1
        else:
            stats["needs_review"] += 1

    print("=" * 50)
    print(
        f"SUMMARY FOR SHEET [{sheet_name}]: Auto-Parsed: {stats['auto_parsed']} | Section Rules: {stats['section_rules']} | Needs Review: {stats['needs_review']}"
    )
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
    print(f"Opening Excel file: {excel_path.name}")

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
                print(f"Error processing sheet [{sheet}]: {e}")
        conn.commit()