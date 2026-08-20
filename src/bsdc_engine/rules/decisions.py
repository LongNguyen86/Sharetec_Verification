from pathlib import Path
import pandas as pd

from src.bsdc_engine.rules.store import RuleStore


def apply_qa_decisions(
    reviewed_report_path: Path, db_path: Path | None = None
) -> dict:
    """Read QA reviewed Excel file -> Update SQLite & Save Audit History to rule_history table."""
    if not reviewed_report_path.exists():
        print(f"❌ Reviewed report file not found: {reviewed_report_path}")
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

            if (
                pd.isna(decision)
                or not decision
                or decision not in ["APPROVE", "EDIT", "REJECT"]
            ):
                stats["skipped"] += 1
                continue

            cursor.execute(
                "SELECT dsl_readable, cu_id, sheet_name, section_name, target_field FROM rule_store WHERE id = ?",
                (rule_id,),
            )
            curr_rule = cursor.fetchone()
            if not curr_rule:
                continue

            prev_dsl, cu_id, sheet_name, sec_name, target_field = curr_rule

            new_dsl = prev_dsl
            new_status = "APPROVED"

            if decision == "APPROVE":
                new_status = "VERIFIED_APPROVED"
                stats["approved"] += 1

            elif decision == "EDIT":
                new_status = "VERIFIED_EDITED"
                new_dsl = qa_edited_dsl if qa_edited_dsl else prev_dsl
                stats["edited"] += 1

            elif decision == "REJECT":
                new_status = "REJECTED"
                stats["rejected"] += 1

            cursor.execute(
                """
                UPDATE rule_store 
                SET dsl_readable = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (new_dsl, new_status, rule_id),
            )

            cursor.execute(
                """
                INSERT INTO rule_history 
                (rule_id, cu_id, sheet_name, section_name, target_field, action, previous_dsl, new_dsl, reviewer, review_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    rule_id,
                    cu_id,
                    sheet_name,
                    sec_name,
                    target_field,
                    decision,
                    prev_dsl,
                    new_dsl,
                    reviewer,
                    qa_notes,
                ),
            )

        conn.commit()

    print("\n" + "=" * 60)
    print("✅ APPLIED QA DECISIONS TO DATABASE:")
    print(f"   - Approved: {stats['approved']} rules")
    print(f"   - Edited:   {stats['edited']} rules")
    print(f"   - Rejected: {stats['rejected']} rules")
    print(f"   - Skipped:  {stats['skipped']} rules (No Decision input)")
    print("=" * 60 + "\n")

    return stats