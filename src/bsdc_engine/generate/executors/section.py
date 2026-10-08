import re
import polars as pl
from src.bsdc_engine.generate.resolver import resolve_column_name


def parse_section_filter_expr(
    filter_str: str, default_table: str, available_cols: list
) -> pl.Expr | None:
    """
    Parse section filter expressions into Polars expressions dynamically.
    Supports OR conditions, prefix matching, inequality checks, and exclusion rules.
    """
    if not filter_str:
        return None

    filter_upper = filter_str.upper().strip()

    # Match dynamic OR conditions for column value filters
    m_or = re.search(
        r"COLUMN\s+([A-Z]+)\s*=\s*([0-9A-Z_\-\.]+)\s+OR\s+(?:COLUMN\s+[A-Z]+\s*=\s*)?([0-9A-Z_\-\.]+)",
        filter_upper,
        re.IGNORECASE,
    )
    if m_or:
        col_let, val1, val2 = m_or.group(1), m_or.group(2), m_or.group(3).strip(".")
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = pl.col(c_name).cast(pl.Utf8).fill_null("").str.strip_chars()
            return (
                (col_expr == val1)
                | (col_expr == val2)
                | col_expr.str.starts_with(val1)
                | col_expr.str.starts_with(val2)
            )

    # Match prefix filter conditions (e.g. STARTS WITH / BEGINS WITH)
    m_beg = re.search(
        r"COLUMN\s+([A-Z]+)\s+(?:BEGINS|STARTS\s+WITH|BEGINS\s+WITH)\s+([A-Z0-9_\-\s]+)",
        filter_upper,
    )
    if m_beg:
        col_let, val = m_beg.group(1), m_beg.group(2).strip()
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = pl.col(c_name).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
            return col_expr.str.starts_with(val.upper())

    # Match inequality filter conditions
    m_neq = re.search(r"COLUMN\s+([A-Z]+)\s*(?:<>|!=)\s*([^\|\n]+)", filter_upper)
    if m_neq:
        col_let = m_neq.group(1)
        val_raw = m_neq.group(2).strip()
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = pl.col(c_name).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
            if "INACTIVE" in val_raw:
                return (col_expr != "INACTIVE") & (col_expr != "")
            if "0" in val_raw or "BLANK" in val_raw or "NULL" in val_raw:
                return (col_expr != "0") & (col_expr != "") & (col_expr != "NULL") & (col_expr != "NONE")
            return col_expr != val_raw

    # Match exclusion instructions (e.g. DO NOT CREATE / DO NOT ASSIGN)
    if "DO NOT CREATE" in filter_upper or "DO NOT ASSIGN" in filter_upper:
        m_eq = re.search(r"COLUMN\s+([A-Z]+)\s*(?:=|\bIS\b)\s*([0-9A-Z_\-\.\s]+)", filter_upper)
        if m_eq:
            col_let, val = m_eq.group(1), m_eq.group(2).strip()
            val_clean = val.split()[0]
            c_name = resolve_column_name("", col_let, default_table, available_cols)
            if c_name and c_name in available_cols:
                col_expr = pl.col(c_name).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
                if val_clean in ["NULL", "BLANK", "NONE"]:
                    return (col_expr != "") & (col_expr != "NULL") & (col_expr != "NONE")
                return col_expr != val_clean

    # Match basic column equality filter
    m_eq = re.search(r"COLUMN\s+([A-Za-z0-9_]+)\s*(?:=|\bIS\b)\s*([0-9A-Z_\-\.]+)", filter_upper)
    if m_eq:
        col_let, val = m_eq.group(1), m_eq.group(2).strip()
        c_name = resolve_column_name("", col_let, default_table, available_cols)
        if c_name and c_name in available_cols:
            col_expr = pl.col(c_name).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
            return col_expr == val

    return None