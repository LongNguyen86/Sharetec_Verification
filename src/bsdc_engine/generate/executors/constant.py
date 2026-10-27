import re
import polars as pl
from src.bsdc_engine.generate.executors.base import BaseExecutor


class ConstantExecutor(BaseExecutor):
    def __init__(self, conditional_executor=None):
        self.conditional_executor = conditional_executor

    def evaluate(
        self,
        target_field: str,
        src_file: str,
        src_col: str,
        dsl_dict: dict,
        raw_notes: str,
        df: pl.DataFrame,
        default_table: str,
        sec_name: str = "",
    ) -> pl.Expr:
        val_raw = str(dsl_dict.get("value") or raw_notes).strip()
        clean_val = re.split(r";|\bIF\b", val_raw, flags=re.IGNORECASE)[0]
        clean_val = re.sub(r"^ASSIGN\s+(ALL\s+)?", "", clean_val, flags=re.IGNORECASE).strip()

        # Generic pattern to detect cross-table field references across any module (e.g. GL.ACCOUNT, MB.NUMBER, CARD.STATUS)
        is_field_reference = bool(re.match(r"^[A-Z0-9_\-]+\.[A-Z0-9_\-]+", clean_val.upper()))

        if is_field_reference:
            if self.conditional_executor:
                return self.conditional_executor.evaluate(
                    target_field, src_file, src_col, dsl_dict, raw_notes, df, default_table, sec_name
                )
        return pl.lit(clean_val)