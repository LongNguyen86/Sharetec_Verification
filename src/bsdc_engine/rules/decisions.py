import json
import re
from pathlib import Path
import pandas as pd

from src.bsdc_engine.rules.store import RuleStore
from src.bsdc_engine.rules.parser import parse_notes_to_dsl
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


def extract_constant_value(text: str) -> str | None:
    """
    Extract literal constant value from QA input formats:
    - CONSTANT('TT') or CONSTANT("TT") or CONSTANT(TT)
    - CONST('TT') or CONST("TT") or CONST(TT)
    - 'TT' or "TT"
    - ASSIGN 'TT' or ASSIGN TT
    """
    cleaned = text.strip()
    
    # 1. Matches CONSTANT(...) or CONST(...)
    m_func = re.match(r"^(?:CONSTANT|CONST)\s*\(\s*['\"]?(.*?)['\"]?\s*\)$", cleaned, re.IGNORECASE)
    if m_func:
        return m_func.group(1).strip()
        
    # 2. Matches quoted literals: 'value' or "value"
    m_quoted = re.match(r"^['\"](.*)['\"]$", cleaned)
    if m_quoted:
        return m_quoted.group(1).strip()
        
    # 3. Matches explicit assignment: ASSIGN 'value' or CREATE 'value'
    m_assign = re.match(r"^(?:ASSIGN|CREATE)\s+['\"]?(.*?)['\"]?$", cleaned, re.IGNORECASE)
    if m_assign:
        return m_assign.group(1).strip()
        
    return None


def apply_qa_decisions(
    reviewed_report_path: Path, db_path: Path | None = None
) -> dict:
    """
    Read QA reviewed Excel report and update rule_store deterministically.
    Supports DIRECT, CONSTANT, and general expressions with proper rule_type assignment.
    """
    reviewed_report_path = Path(reviewed_report_path)
    if not reviewed_report_path.exists():
        logger.warning(f"Reviewed report file not found: {reviewed_report_path}")
        return {"approved": 0, "edited": 0, "rejected": 0, "skipped": 0}

    df = pd.read_excel(reviewed_report_path, sheet_name="Rule_Verification")
    
    # Strip whitespace from column headers to prevent key lookup failures
    df.columns = [str(c).strip() for c in df.columns]

    stats = {"approved": 0, "edited": 0, "rejected": 0, "skipped": 0}
    store = RuleStore(db_path=db_path)

    with store.get_connection() as conn:
        cursor = conn.cursor()

        for idx, row in df.iterrows():
            raw_id = row.get("Rule_ID")
            if pd.isna(raw_id) or str(raw_id).strip() == "":
                continue

            try:
                rule_id = int(float(str(raw_id).strip()))
            except ValueError:
                continue

            raw_decision = row.get("Decision (QA)", "")
            decision = str(raw_decision).strip().upper() if pd.notna(raw_decision) else ""

            raw_edited_dsl = row.get("QA Edited DSL", "")
            qa_edited_dsl = str(raw_edited_dsl).strip() if pd.notna(raw_edited_dsl) else ""
            if qa_edited_dsl.lower() in ["nan", "none", "null"]:
                qa_edited_dsl = ""

            reviewer = str(row.get("QA Reviewer", "")).strip() if pd.notna(row.get("QA Reviewer")) else "QA_USER"
            if reviewer.lower() in ["nan", "none"]:
                reviewer = "QA_USER"

            qa_notes = str(row.get("QA Notes", "")).strip() if pd.notna(row.get("QA Notes")) else ""
            if qa_notes.lower() in ["nan", "none"]:
                qa_notes = ""

            if decision not in ["APPROVE", "EDIT", "REJECT"]:
                stats["skipped"] += 1
                continue

            cursor.execute(
                "SELECT dsl_readable, dsl_json, cu_id, sheet_name, section_name, target_field, data_file, column_letter FROM rule_store WHERE id = ?",
                (rule_id,),
            )
            curr = cursor.fetchone()
            if not curr:
                logger.warning(f"Rule ID [{rule_id}] not found in database. Skipping.")
                continue

            prev_dsl, prev_json_str, cu_id, sheet_name, sec_name, target_field, curr_file, curr_col = curr

            new_dsl = prev_dsl
            new_json_str = prev_json_str
            new_status = "APPROVED"
            new_rule_type = None
            new_file = curr_file
            new_col = curr_col

            if decision == "APPROVE":
                new_status = "VERIFIED_APPROVED"
                stats["approved"] += 1

            elif decision == "REJECT":
                new_status = "REJECTED"
                stats["rejected"] += 1

            elif decision == "EDIT":
                new_status = "VERIFIED_EDITED"
                if qa_edited_dsl:
                    new_dsl = qa_edited_dsl

                    # 1. Check for CONSTANT override: CONSTANT('...'), CONST('...'), '...', "...", ASSIGN '...'
                    const_val = extract_constant_value(qa_edited_dsl)
                    if const_val is not None:
                        new_rule_type = "CONSTANT"
                        new_json_str = json.dumps({"value": const_val})

                    # 2. Check for DIRECT column mapping: DIRECT(TABLE.COL), DIRECT(COL), TABLE.COL, or COL
                    elif re.match(
                        r"^(?:DIRECT\()?\s*(?:([A-Za-z0-9_]+)\.)?([A-Za-z]{1,3})\s*\)?$",
                        qa_edited_dsl,
                        re.IGNORECASE,
                    ):
                        m_direct = re.match(
                            r"^(?:DIRECT\()?\s*(?:([A-Za-z0-9_]+)\.)?([A-Za-z]{1,3})\s*\)?$",
                            qa_edited_dsl,
                            re.IGNORECASE,
                        )
                        new_file = m_direct.group(1) or curr_file
                        new_col = m_direct.group(2).upper()
                        new_rule_type = "DIRECT"
                        new_json_str = json.dumps({"source_file": new_file, "source_column": new_col})

                    # 3. Fallback to general DSL parsing (CONDITIONAL, CONCAT, etc.)
                    else:
                        parsed_res = parse_notes_to_dsl(curr_file, curr_col, qa_edited_dsl)
                        new_rule_type = parsed_res["rule_type"]
                        dsl_obj = parsed_res["dsl_obj"]
                        new_json_str = dsl_obj.model_dump_json() if hasattr(dsl_obj, "model_dump_json") else json.dumps(dsl_obj)

                stats["edited"] += 1

            update_query = """
                UPDATE rule_store 
                SET dsl_readable = ?, dsl_json = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            """
            update_params = [new_dsl, new_json_str, new_status]

            if new_rule_type:
                update_query += ", rule_type = ?, data_file = ?, column_letter = ?"
                update_params.extend([new_rule_type, new_file, new_col])

            update_query += " WHERE id = ?"
            update_params.append(rule_id)

            cursor.execute(update_query, tuple(update_params))

            cursor.execute(
                """
                INSERT INTO rule_history 
                (rule_id, cu_id, sheet_name, section_name, target_field, action, previous_dsl, new_dsl, reviewer, review_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rule_id, cu_id, sheet_name, sec_name, target_field, decision, prev_dsl, new_dsl, reviewer, qa_notes),
            )

        conn.commit()

    logger.info(f"QA Decisions Applied: {stats}")
    return stats