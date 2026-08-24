import polars as pl
from src.bsdc_engine.generate.executors.base import BaseExecutor
from src.bsdc_engine.generate.resolver import resolve_column_name


class DirectExecutor(BaseExecutor):
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
    ) -> pl.Expr | None:
        target_col = resolve_column_name(src_file, src_col, default_table, df.columns)
        if target_col and target_col in df.columns:
            return pl.col(target_col).cast(pl.Utf8)
        return None