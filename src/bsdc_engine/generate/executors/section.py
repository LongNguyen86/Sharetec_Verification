import re
import polars as pl
from src.bsdc_engine.generate.resolver import resolve_column_name


def parse_section_filter_expr(
    filter_str: str, default_table: str, available_cols: list
) -> pl.Expr | None:
    """
    Parse section filter expressions into Polars expressions dynamically.
    Supports dynamic inclusion and exclusion rules based purely on parsed tokens.
    """
    if not filter_str:
        return None

    filter_upper = filter_str.upper().strip()

    col_match = re.search(r"\bCOL(?:UMN)?\s+([A-Za-z0-9_]+)\b", filter_upper)
    if not col_match:
        return None

    col_let = col_match.group(1).upper()
    c_name = resolve_column_name("", col_let, default_table, available_cols)
    if not c_name or c_name not in available_cols:
        return None

    # Cast to Utf8 string, clean non-ASCII bytes, strip whitespace, and drop float decimal artifacts (.0)
    col_expr = (
        pl.col(c_name)
        .cast(pl.Utf8)
        .fill_null("")
        .str.replace_all(r"[^\x20-\x7E]", "")
        .str.strip_chars()
        .str.replace_all(r"\.0+$", "")
        .str.to_uppercase()
    )

    syntax_keywords = {
        "ONLY", "CONSIDERED", "CONSIDER", "CREATE", "ASSIGN", "DO", "NOT",
        "IF", "COLUMN", "COL", "IS", "AND", "OR", "WHERE", "FILTER", "TABLE",
        "NULL", "BLANK", "NONE", col_let
    }

    raw_tokens = re.findall(r"\b([0-9A-Za-z_\-\.]+)\b", filter_upper)
    value_tokens = []
    for v in raw_tokens:
        v_clean = re.sub(r"\.0+$", "", v.strip())
        if v_clean and v_clean not in syntax_keywords and v_clean != col_let:
            value_tokens.append(v_clean)

    if not value_tokens:
        return None

    is_exclusion = any(kw in filter_upper for kw in ["<>", "!=", "DO NOT CREATE", "DO NOT ASSIGN", "NOT EQUAL"])

    if is_exclusion:
        return ~col_expr.is_in(value_tokens)
    else:
        return col_expr.is_in(value_tokens)