from abc import ABC, abstractmethod
import polars as pl


class BaseExecutor(ABC):
    """Abstract Base Class for all Rule Executors."""

    @abstractmethod
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
        pass