import json
import re
from pathlib import Path
from typing import Union, List
import polars as pl

from src.bsdc_engine.io.raw_reader import load_raw_tables
from src.bsdc_engine.rules.store import RuleStore
from src.bsdc_engine.generate.executors.direct import DirectExecutor
from src.bsdc_engine.generate.executors.constant import ConstantExecutor
from src.bsdc_engine.generate.executors.conditional import ConditionalExecutor
from src.bsdc_engine.generate.executors.section import parse_section_filter_expr
from src.bsdc_engine.models.results import GenerateResult
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


class TransformationBuilder:
    def __init__(
        self,
        raw_data_dir: Union[Path, str, List[Union[Path, str]]],
        output_dir: Path,
        db_path: Path | None = None,
    ):
        # Support both a single directory path and a list of directory paths
        if isinstance(raw_data_dir, (list, tuple)):
            self.raw_data_dirs = [Path(d) for d in raw_data_dir]
        else:
            self.raw_data_dirs = [Path(raw_data_dir)]

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rule_store = RuleStore(db_path=db_path)

        self.conditional_executor = ConditionalExecutor()
        self.direct_executor = DirectExecutor()
        self.constant_executor = ConstantExecutor(conditional_executor=self.conditional_executor)

    def generate_all(self, cu_id: str) -> list[GenerateResult]:
        # Load raw CSV tables across all configured input directories with priority to earlier sources
        tables = {}
        for d in self.raw_data_dirs:
            if d.exists():
                dir_tables = load_raw_tables(d)
                if dir_tables:
                    for table_key, df in dir_tables.items():
                        # Preserve existing table if already loaded from higher priority directory
                        if table_key not in tables:
                            tables[table_key] = df

        if not tables:
            logger.error(
                f"No Raw Data CSV files found in directories: {[str(d) for d in self.raw_data_dirs]}"
            )
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

                    # STEP A: APPLY SECTION FILTER PRIOR TO JOINING TO PREVENT CARTESIAN PRODUCT
                    cursor.execute(
                        "SELECT dsl_json, raw_notes FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND target_field = '_SECTION_RULE_' LIMIT 1",
                        (cu_id, current_sheet, sec),
                    )
                    sec_rule_row = cursor.fetchone()
                    sec_join_rule = None

                    if sec_rule_row:
                        sec_dsl_json_str, sec_raw_notes = sec_rule_row
                        sec_filter_cond = None
                        if sec_dsl_json_str:
                            try:
                                dsl_data = json.loads(sec_dsl_json_str)
                                sec_filter_cond = dsl_data.get("filter_condition")
                                sec_join_rule = dsl_data.get("join_rule")
                            except Exception:
                                pass
                        if not sec_filter_cond and sec_raw_notes:
                            sec_filter_cond = sec_raw_notes

                        if sec_filter_cond:
                            sec_filter_expr = parse_section_filter_expr(sec_filter_cond, default_table_key, base_df.columns)
                            if sec_filter_expr is not None:
                                try:
                                    base_df = base_df.filter(sec_filter_expr)
                                except Exception as e:
                                    logger.error(f"Section filter error: {e}")

                    # STEP B: JOIN SECONDARY TABLES ACCORDING TO PARSED JOIN_RULE SPECIFICATIONS
                    cursor.execute(
                        "SELECT DISTINCT data_file FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND data_file IS NOT NULL AND data_file != '' AND data_file != 'N/A'",
                        (cu_id, current_sheet, sec),
                    )
                    sec_data_files = set([Path(r[0]).stem.upper().replace(" ", "_") for r in cursor.fetchall() if r[0]])

                    for sec_file in sec_data_files:
                        if sec_file != default_table_key and sec_file in tables:
                            sec_raw = tables[sec_file]
                            sec_cols = [f"{sec_file}::col_{i}" for i in range(sec_raw.shape[1])]
                            sec_df = sec_raw.rename(dict(zip(sec_raw.columns, sec_cols)))

                            left_key, right_key = None, None

                            # Resolve join keys from dsl_json explicit join rule
                            if sec_join_rule:
                                src_file = str(sec_join_rule.get("source_file", "")).upper().replace(" ", "_")
                                src_col_let = str(sec_join_rule.get("source_col", ""))
                                tgt_file = str(sec_join_rule.get("target_file", "")).upper().replace(" ", "_")
                                tgt_col_let = str(sec_join_rule.get("target_col", ""))

                                from src.bsdc_engine.text import col_letter_to_index
                                src_idx = col_letter_to_index(src_col_let)
                                tgt_idx = col_letter_to_index(tgt_col_let)

                                if default_table_key == src_file and sec_file == tgt_file and src_idx >= 0 and tgt_idx >= 0:
                                    left_key = f"{default_table_key}::col_{src_idx}"
                                    right_key = f"{sec_file}::col_{tgt_idx}"
                                elif default_table_key == tgt_file and sec_file == src_file and src_idx >= 0 and tgt_idx >= 0:
                                    left_key = f"{default_table_key}::col_{tgt_idx}"
                                    right_key = f"{sec_file}::col_{src_idx}"

                            # Fallback if no explicit join rule is specified
                            if not left_key or not right_key:
                                primary_col_indices = [c.split("::col_")[-1] for c in base_df.columns if "::col_" in c]
                                sec_col_indices = [c.split("::col_")[-1] for c in sec_df.columns if "::col_" in c]
                                common_indices = [idx for idx in ["6", "16", "0", "1"] if idx in primary_col_indices and idx in sec_col_indices]
                                for idx in common_indices:
                                    left_key = f"{default_table_key}::col_{idx}"
                                    right_key = f"{sec_file}::col_{idx}"
                                    break

                            if left_key and right_key and left_key in base_df.columns and right_key in sec_df.columns:
                                try:
                                    # Filter out null, zero, or blank key values prior to joining
                                    valid_left = base_df.filter(
                                        pl.col(left_key).is_not_null() & 
                                        (pl.col(left_key).cast(pl.Utf8).str.strip_chars() != "") &
                                        (pl.col(left_key).cast(pl.Utf8) != "0")
                                    )
                                    valid_right = sec_df.filter(
                                        pl.col(right_key).is_not_null() & 
                                        (pl.col(right_key).cast(pl.Utf8).str.strip_chars() != "") &
                                        (pl.col(right_key).cast(pl.Utf8) != "0")
                                    )
                                    base_df = valid_left.join(valid_right, left_on=left_key, right_on=right_key, how="left")
                                except Exception as e:
                                    logger.warning(f"Join warning for [{sec_file}]: {e}")

                    # STEP C: PROCESS FIELD MAPPINGS AND TRANSFORMATIONS
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
                            expr = self.direct_executor.evaluate(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key, sec_name=sec)
                            if expr is not None:
                                output_data[field] = base_df.with_columns(expr.alias(field))[field]
                            else:
                                cond_expr = self.conditional_executor.evaluate(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key, sec_name=sec)
                                output_data[field] = base_df.with_columns(cond_expr.alias(field))[field] if cond_expr is not None else pl.Series([None] * total_rows)

                        elif rule_type == "CONSTANT":
                            expr = self.constant_executor.evaluate(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key, sec_name=sec)
                            output_data[field] = base_df.with_columns(expr.alias(field))[field] if expr is not None else pl.Series([None] * total_rows)

                        elif rule_type == "NO_MAPPING":
                            output_data[field] = pl.Series([None] * total_rows)

                        else:  # CONDITIONAL, MATRIX_LOOKUP, UNPARSED
                            cond_expr = self.conditional_executor.evaluate(field, src_file, src_col, dsl_dict, raw_notes, base_df, default_table_key, sec_name=sec)
                            output_data[field] = base_df.with_columns(cond_expr.alias(field))[field] if cond_expr is not None else pl.Series([None] * total_rows)

                    if output_data:
                        res_df = pl.DataFrame(output_data)
                        sec_clean = re.sub(r"^(?:Shares|Member|Members|Savings)\s+", "", sec, flags=re.IGNORECASE).replace(" ", "_")
                        filename = f"{cu_id}{sec_clean}.csv" if sec_clean.startswith("(") else f"{cu_id}_{sec_clean}.csv"
                        out_file = self.output_dir / filename
                        res_df.write_csv(out_file)

                        results.append(GenerateResult(
                            cu_id=cu_id,
                            sheet_name=current_sheet,
                            section_name=sec,
                            rows_generated=res_df.shape[0],
                            output_file=str(out_file)
                        ))

        return results