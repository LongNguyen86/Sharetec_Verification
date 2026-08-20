import json
import re
from pathlib import Path
import polars as pl

from src.bsdc_engine.io.raw_reader import load_raw_tables
from src.bsdc_engine.rules.store import RuleStore
from src.bsdc_engine.generate.executors.conditional import ConditionalExecutor, resolve_column_name
from src.bsdc_engine.models.results import GenerateResult
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


class TransformationBuilder:
    def __init__(self, raw_data_dir: Path, output_dir: Path, db_path: Path | None = None):
        self.raw_data_dir = Path(raw_data_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rule_store = RuleStore(db_path=db_path)
        self.executor = ConditionalExecutor()

    def generate_all(self, cu_id: str) -> list[GenerateResult]:
        tables = load_raw_tables(self.raw_data_dir)
        if not tables:
            logger.error(f"No Raw Data CSV files found in {self.raw_data_dir}")
            return []

        results = []
        with self.rule_store.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT sheet_name FROM rule_store WHERE cu_id = ? OR is_global = 1",
                (cu_id,)
            )
            sheets = [r[0] for r in cursor.fetchall() if r[0]]

            for current_sheet in sheets:
                cursor.execute(
                    "SELECT DISTINCT section_name FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ?",
                    (cu_id, current_sheet),
                )
                sections = [r[0] for r in cursor.fetchall() if r[0]]

                for sec in sections:
                    cursor.execute(
                        "SELECT data_file, COUNT(*) as cnt FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND data_file IS NOT NULL AND data_file != '' AND data_file != 'N/A' GROUP BY data_file ORDER BY cnt DESC LIMIT 1",
                        (cu_id, current_sheet, sec),
                    )
                    data_file_row = cursor.fetchone()
                    if not data_file_row or not data_file_row[0]:
                        continue

                    default_table_key = Path(data_file_row[0]).stem.upper().replace(" ", "_")
                    if default_table_key not in tables:
                        continue

                    df_primary = tables[default_table_key]
                    base_cols = [f"{default_table_key}::col_{i}" for i in range(df_primary.shape[1])]
                    base_df = df_primary.rename(dict(zip(df_primary.columns, base_cols)))

                    output_data = {}
                    cursor.execute(
                        "SELECT target_field, data_file, column_letter, rule_type, dsl_json, raw_notes FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND target_field != '_SECTION_RULE_' ORDER BY id ASC",
                        (cu_id, current_sheet, sec),
                    )
                    field_rules = cursor.fetchall()
                    total_rows = base_df.shape[0]

                    for field, src_file, src_col, rule_type, dsl_json_str, raw_notes in field_rules:
                        dsl_dict = json.loads(dsl_json_str) if dsl_json_str else {}
                        
                        if rule_type == "DIRECT":
                            target_col = resolve_column_name(src_file, src_col, default_table_key, base_df.columns)
                            if target_col and target_col in base_df.columns:
                                output_data[field] = base_df[target_col].head(total_rows)
                            else:
                                cond_expr = self.executor.evaluate(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key, sec_name=sec)
                                output_data[field] = base_df.with_columns(cond_expr.alias(field))[field] if cond_expr is not None else pl.Series([None] * total_rows)
                        elif rule_type == "CONSTANT":
                            val_raw = str(dsl_dict.get("value") or raw_notes).strip()
                            clean_val = re.sub(r"^ASSIGN\s+(ALL\s+)?", "", val_raw, flags=re.IGNORECASE).strip()
                            output_data[field] = pl.Series([clean_val] * total_rows)
                        else:
                            cond_expr = self.executor.evaluate(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key, sec_name=sec)
                            output_data[field] = base_df.with_columns(cond_expr.alias(field))[field] if cond_expr is not None else pl.Series([None] * total_rows)

                    if output_data:
                        res_df = pl.DataFrame(output_data)
                        clean_sheet = re.sub(r'[\\/*?:"<>|]', "_", current_sheet).replace(" ", "_")
                        clean_sec = re.sub(r'[\\/*?:"<>|]', "_", sec).replace(" ", "_")
                        out_file = self.output_dir / f"Expected_{cu_id}_{clean_sheet}_{clean_sec}.csv"
                        res_df.write_csv(out_file)
                        
                        results.append(GenerateResult(
                            cu_id=cu_id,
                            sheet_name=current_sheet,
                            section_name=sec,
                            rows_generated=res_df.shape[0],
                            output_file=str(out_file)
                        ))

        return results