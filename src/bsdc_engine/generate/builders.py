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

# Preferred Credit Union key column indices for auto-join fallback (0=Col A Member Num, 6=Col G, 16=Col Q)
PREFERRED_JOIN_INDICES = ["0", "6", "16", "1"]


class TransformationBuilder:
    def __init__(
        self,
        raw_data_dir: Union[Path, str, List[Union[Path, str]]],
        output_dir: Path,
        db_path: Path | None = None,
    ):
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
        tables = {}
        for d in self.raw_data_dirs:
            if d.exists():
                dir_tables = load_raw_tables(d)
                if dir_tables:
                    for table_key, df in dir_tables.items():
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

                sheet_dfs = []

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

                    # Clean non-ASCII bytes, Unicode BOMs, whitespace, and decimal artifacts from primary DataFrame
                    base_df = base_df.with_columns([
                        pl.col(c)
                        .cast(pl.Utf8)
                        .fill_null("")
                        .str.replace_all(r"[^\x20-\x7E]", "")
                        .str.strip_chars()
                        .alias(c)
                        for c in base_df.columns
                    ])

                    # Track absolute row order index from primary source table for generic output sorting
                    base_df = base_df.with_columns(
                        pl.Series("_orig_row_idx", list(range(base_df.shape[0])))
                    )

                    # STEP A: APPLY SECTION FILTER PRIOR TO JOINING
                    cursor.execute(
                        "SELECT dsl_json, raw_notes FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND target_field LIKE '_SECTION_RULE_%' LIMIT 1",
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

                    if sec_join_rule:
                        s_file = Path(str(sec_join_rule.get("source_file", ""))).stem.upper().replace(" ", "_") if sec_join_rule.get("source_file") else ""
                        t_file = Path(str(sec_join_rule.get("target_file", ""))).stem.upper().replace(" ", "_") if sec_join_rule.get("target_file") else ""
                        if s_file: sec_data_files.add(s_file)
                        if t_file: sec_data_files.add(t_file)

                    for sec_file in sec_data_files:
                        if sec_file != default_table_key and sec_file in tables:
                            sec_raw = tables[sec_file]
                            sec_cols = [f"{sec_file}::col_{i}" for i in range(sec_raw.shape[1])]
                            sec_df = sec_raw.rename(dict(zip(sec_raw.columns, sec_cols)))

                            left_key, right_key = None, None

                            if sec_join_rule:
                                src_file = Path(str(sec_join_rule.get("source_file", ""))).stem.upper().replace(" ", "_")
                                src_col_let = str(sec_join_rule.get("source_col", ""))
                                tgt_file = Path(str(sec_join_rule.get("target_file", ""))).stem.upper().replace(" ", "_")
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

                            # Prioritized Fallback: Match on Member Number (Col A = "0") if no explicit LINK rule exists
                            if not left_key or not right_key:
                                primary_col_indices = [c.split("::col_")[-1] for c in base_df.columns if "::col_" in c]
                                sec_col_indices = [c.split("::col_")[-1] for c in sec_df.columns if "::col_" in c]
                                
                                common_indices = [
                                    idx for idx in PREFERRED_JOIN_INDICES 
                                    if idx in primary_col_indices and idx in sec_col_indices
                                ]
                                if not common_indices:
                                    common_indices = [idx for idx in primary_col_indices if idx in sec_col_indices]

                                if common_indices:
                                    selected_idx = common_indices[0]
                                    left_key = f"{default_table_key}::col_{selected_idx}"
                                    right_key = f"{sec_file}::col_{selected_idx}"
                                    logger.info(f"Auto-matched join key col_{selected_idx} between [{default_table_key}] and [{sec_file}]")

                            if left_key and right_key and left_key in base_df.columns and right_key in sec_df.columns:
                                try:
                                    base_df = base_df.with_columns(
                                        pl.col(left_key)
                                        .cast(pl.Utf8)
                                        .str.replace_all(r"[^\x20-\x7E]", "")
                                        .str.strip_chars()
                                        .str.replace_all(r"\.0+$", "")
                                        .alias(left_key)
                                    )
                                    sec_df = sec_df.with_columns(
                                        pl.col(right_key)
                                        .cast(pl.Utf8)
                                        .str.replace_all(r"[^\x20-\x7E]", "")
                                        .str.strip_chars()
                                        .str.replace_all(r"\.0+$", "")
                                        .alias(right_key)
                                    )
                                    valid_sec_df = sec_df.filter(
                                        pl.col(right_key).is_not_null() &
                                        (pl.col(right_key) != "") &
                                        (pl.col(right_key) != "0") &
                                        (pl.col(right_key) != "NULL") &
                                        (pl.col(right_key) != "NONE")
                                    )
                                    base_df = base_df.join(valid_sec_df, left_on=left_key, right_on=right_key, how="left")
                                except Exception as e:
                                    logger.warning(f"Join warning for [{sec_file}]: {e}")

                    # STEP C: PROCESS FIELD MAPPINGS AND TRANSFORMATIONS
                    output_data = {}
                    cursor.execute(
                        "SELECT target_field, data_file, column_letter, rule_type, dsl_json, raw_notes FROM rule_store WHERE (cu_id = ? OR is_global = 1) AND sheet_name = ? AND section_name = ? AND target_field NOT LIKE '_SECTION_RULE_%' ORDER BY id ASC",
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
                        if "_orig_row_idx" in base_df.columns:
                            res_df = res_df.with_columns(base_df["_orig_row_idx"])

                        if res_df.shape[0] > 0:
                            sheet_dfs.append(res_df)

                # Combine all sections for current sheet, deduplicate & sort based on original input file order
                if sheet_dfs:
                    final_sheet_df = pl.concat(sheet_dfs, how="diagonal_relaxed")

                    if "_orig_row_idx" in final_sheet_df.columns:
                        final_sheet_df = (
                            final_sheet_df
                            .unique(subset=["_orig_row_idx"], keep="last")
                            .sort("_orig_row_idx")
                            .drop("_orig_row_idx")
                        )

                    sheet_clean = re.sub(r'[\\/*?:"<>|]', "_", current_sheet).strip().replace(" ", "_")
                    sheet_clean = re.sub(r"^_+", "", sheet_clean)
                    filename = f"{cu_id}_{sheet_clean}.csv" if not sheet_clean.startswith("(") else f"{cu_id}{sheet_clean}.csv"
                    out_file = self.output_dir / filename

                    final_sheet_df.write_csv(out_file)

                    results.append(GenerateResult(
                        cu_id=cu_id,
                        sheet_name=current_sheet,
                        section_name="ALL_SECTIONS_MERGED",
                        rows_generated=final_sheet_df.shape[0],
                        output_file=str(out_file)
                    ))

        return results