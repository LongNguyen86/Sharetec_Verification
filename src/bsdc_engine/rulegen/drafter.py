"""Rule Drafter Orchestration for Batch LLM Parsing."""

import json
import time
from pathlib import Path

from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.rulegen.client import LLMParserClient
from src.bsdc_engine.rules.store import RuleStore

logger = get_logger(__name__)


class RuleDrafter:
    """Orchestrates batch AI parsing for unparsed/complex mapping rules."""

    def __init__(
        self,
        db_path: Path | None = None,
        batch_size: int = 25,
        delay_between_batches: int = 14,
    ):
        self.store = RuleStore(db_path=db_path)
        self.client = LLMParserClient()
        self.batch_size = batch_size
        self.delay_between_batches = delay_between_batches

    def draft_pending_rules(self, cu_id: str | None = None) -> int:
        """Retrieve unparsed rules from SQLite and invoke LLM in batches."""
        with self.store.get_connection() as conn:
            cursor = conn.cursor()

            query = """
                SELECT id, target_field, data_file, column_letter, raw_notes 
                FROM rule_store 
                WHERE (status IN ('NEEDS_REVIEW', 'UNPARSED') OR dsl_readable = 'NEEDS_LLM_PARSING')
            """
            params = []
            if cu_id:
                query += " AND (cu_id = ? OR is_global = 1)"
                params.append(cu_id)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            if not rows:
                logger.info("✅ All rules parsed successfully. No rules left requiring LLM processing!")
                return 0

            logger.info(f"🤖 Found {len(rows)} rules needing AI/LLM decoding...")

            items = [
                {
                    "id": r_id,
                    "field": field,
                    "file": file_name,
                    "col": col,
                    "raw_notes": notes,
                }
                for r_id, field, file_name, col, notes in rows
            ]

            total_batches = (len(items) + self.batch_size - 1) // self.batch_size
            total_updated = 0

            for batch_idx in range(total_batches):
                batch_items = items[
                    batch_idx * self.batch_size : (batch_idx + 1) * self.batch_size
                ]
                logger.info(
                    f"🧠 [Batch {batch_idx + 1}/{total_batches}] Sending {len(batch_items)} rules to Gemini AI..."
                )

                parsed_list = self.client.parse_rules_with_llm_batch(batch_items)

                if parsed_list:
                    for item in parsed_list:
                        rule_id = item.get("id")
                        r_type = item.get("rule_type", "CONDITIONAL")
                        d_json = item.get("dsl_json", {})
                        d_readable = item.get("dsl_readable", "")

                        cursor.execute(
                            """
                            UPDATE rule_store 
                            SET rule_type = ?, dsl_json = ?, dsl_readable = ?, status = 'PROVISIONAL_NEEDS_REVIEW', parsed_by = 'LLM_GEMINI'
                            WHERE id = ?
                        """,
                            (
                                r_type,
                                json.dumps(d_json, ensure_ascii=False),
                                d_readable,
                                rule_id,
                            ),
                        )
                        total_updated += 1

                    conn.commit()
                    logger.info(f"   ✅ Successfully updated Batch {batch_idx + 1} into Database!")
                else:
                    logger.warning(f"   ⚠️ Batch {batch_idx + 1} returned empty response.")

                if batch_idx < total_batches - 1:
                    logger.info(
                        f"   💤 Sleeping for {self.delay_between_batches}s to respect Rate Limit (5 RPM)..."
                    )
                    time.sleep(self.delay_between_batches)

            logger.info("🎉 COMPLETED! All rules processed by AI and updated in Database.")
            return total_updated