import json
import re
from pathlib import Path
import pandas as pd

from src.bsdc_engine.rules.store import RuleStore


def parse_qa_dsl_text_to_dict(dsl_text: str) -> dict:
    """
    Convert human-edited DSL text from QA into structured dsl_json format.
    Ensures downstream TransformationBuilder actually reflects QA edits.
    """
    clean_text = dsl_text.strip()
    if not clean_text:
        return {}

    # Check if QA provided direct JSON string
    if clean_text.startswith("{") and clean_text.endswith("}"):
        try:
            return json.loads(clean_text)
        except Exception:
            pass

    # Handle CONSTANT overrides (e.g. CONST('ACTIVE') or 'ACTIVE')
    const_match = re.search(r"CONST\(['\"]?([^'\"]+)['\"]?\)", clean_text, re.IGNORECASE)
    if const_match:
        return {"type": "constant", "value": const_match.group(1)}

    # Handle DIRECT column mapping overrides (e.g. COL_A or FILE.COL_A)
    col_match = re.search(r"\bCOL_([A-Za-z0-9_]+)\b", clean_text, re.IGNORECASE)
    if col_match:
        return {"type": "direct", "source_col": col_match.group(1)}

    # Fallback generic override container
    return {"type": "qa_override", "raw_override": clean_text, "status": "APPROVED"}


def apply_qa_decisions(
    reviewed_report_path: Path, db_path: Path | None = None
) -> dict:
    """Read QA reviewed Excel file -> Update both dsl_readable AND dsl_json in SQLite."""
    if not reviewed_report_path.exists():
        return {"approved": 0, "edited": 0, "rejected": 0, "skipped": 0}

    df = pd.read_excel(reviewed_report_path, sheet_name="Rule_Verification")
    stats = {"approved": 0, "edited": 0, "rejected": 0, "skipped": 0}
    store = RuleStore(db_path=db_path)

    with store.get_connection() as conn:
        cursor = conn.cursor()

        for idx, row in df.iterrows():
            rule_id = int(row["Rule_ID"])
            decision = str(row.get("Decision (QA)", "")).strip().upper()
            qa_edited_dsl = str(row.get("QA Edited DSL", "")).strip()
            reviewer = str(row.get("QA Reviewer", "")).strip() or "QA_USER"
            qa_notes = str(row.get("QA Notes", "")).strip()

            if pd.isna(decision) or decision not in ["APPROVE", "EDIT", "REJECT"]:
                stats["skipped"] += 1
                continue

            cursor.execute(
                "SELECT dsl_readable, dsl_json, cu_id, sheet_name, section_name, target_field FROM rule_store WHERE id = ?",
                (rule_id,),
            )
            curr = cursor.fetchone()
            if not curr:
                continue

            prev_dsl, prev_json_str, cu_id, sheet_name, sec_name, target_field = curr
            new_dsl = prev_dsl
            new_json_str = prev_json_str
            new_status = "APPROVED"

            if decision == "APPROVE":
                new_status = "VERIFIED_APPROVED"
                stats["approved"] += 1

            elif decision == "EDIT":
                new_status = "VERIFIED_EDITED"
                if qa_edited_dsl:
                    new_dsl = qa_edited_dsl
                    parsed_dict = parse_qa_dsl_text_to_dict(qa_edited_dsl)
                    new_json_str = json.dumps(parsed_dict)
                stats["edited"] += 1

            elif decision == "REJECT":
                new_status = "REJECTED"
                stats["rejected"] += 1

            # Update both readable string and JSON payload so engine consumes updated rules
            cursor.execute(
                """
                UPDATE rule_store 
                SET dsl_readable = ?, dsl_json = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_dsl, new_json_str, new_status, rule_id),
            )

            cursor.execute(
                """
                INSERT INTO rule_history 
                (rule_id, cu_id, sheet_name, section_name, target_field, action, previous_dsl, new_dsl, reviewer, review_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rule_id, cu_id, sheet_name, sec_name, target_field, decision, prev_dsl, new_dsl, reviewer, qa_notes),
            )

        conn.commit()

    return stats