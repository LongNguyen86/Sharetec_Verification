import json
import re
from pathlib import Path
import pandas as pd
import polars as pl

from src.bsdc_engine.text import col_letter_to_index
from src.bsdc_engine.generate.executors.base import BaseExecutor


def resolve_column_name(data_file: str, col_letter: str, default_table: str, available_cols: list) -> str | None:
    """Resolve DataFrame column name matching file prefix and column index."""
    data_file_clean = (
        data_file.strip().upper().replace(" ", "_")
        if data_file and str(data_file).strip().upper() not in ["", "N/A", "NAN", "NONE"]
        else None
    )
    col_idx = col_letter_to_index(col_letter)
    if col_idx < 0:
        return None

    if data_file_clean:
        target_col = f"{data_file_clean}::col_{col_idx}"
        if target_col in available_cols:
            return target_col
        return None

    if default_table:
        target_col = f"{default_table.upper().replace(' ', '_')}::col_{col_idx}"
        if target_col in available_cols:
            return target_col

    return None


def clean_action_val(val_str: str) -> str:
    """Clean action string value, returning empty string for BLANK/NULL."""
    if not val_str:
        return ""
    s = val_str.strip()
    s = re.sub(r"^(ASSIGN|CREATE)\s+(ALL\s+)?", "", s, flags=re.IGNORECASE).strip()
    s = s.rstrip(";,.").strip()
    if s.upper() in ["LEAVE BLANK", "BLANK", "NULL", "NONE", "''", '""']:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    s = s.rstrip(";,.").strip()
    if s.upper() in ["LEAVE BLANK", "BLANK", "NULL", "NONE", "''", '""']:
        return ""
    return s


def is_column_reference(val_str: str) -> str | None:
    """Detect explicit source column reference (e.g. 'COLUMN B', 'COL D')."""
    if not val_str:
        return None
    s = val_str.strip()
    m = re.search(r"^\s*(?:COLUMN|COL)\s+([A-Za-z]{1,3})\s*$", s, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def parse_concat_column_expr(
    val_str: str, src_file: str, default_table: str, available_cols: list
) -> pl.Expr | None:
    """Parse column addition expressions dynamically into Polars string concatenation."""
    cleaned = clean_action_val(val_str)
    s = re.sub(r"^\s*(?:COLUMN|COL)?\s*", "", cleaned, flags=re.IGNORECASE).strip()

    if "+" in s or " AND " in s.upper():
        parts = [p.strip() for p in re.split(r"\+|\bAND\b", s, flags=re.IGNORECASE)]
        col_exprs = []
        for p in parts:
            p_clean = re.sub(r"^\s*(?:COLUMN|COL)?\s*", "", p, flags=re.IGNORECASE).strip().upper()
            if re.match(r"^[A-Z]{1,3}$", p_clean):
                ref_col = resolve_column_name(src_file, p_clean, default_table, available_cols)
                if ref_col and ref_col in available_cols:
                    col_exprs.append(pl.col(ref_col).cast(pl.Utf8).fill_null(""))
                else:
                    return None
            else:
                return None

        if col_exprs:
            concat_expr = col_exprs[0]
            for expr in col_exprs[1:]:
                concat_expr = concat_expr + pl.lit(" ") + expr
            return concat_expr.str.strip_chars().str.replace_all(r"\s+", " ")
    return None


def resolve_target_value_expr(
    val_str: str, src_file: str, default_table: str, available_cols: list, primary_col_name: str | None = None
) -> pl.Expr:
    """Resolve target action value into exact Polars literal, column reference, or concatenation expressions."""
    concat_expr = parse_concat_column_expr(val_str, src_file, default_table, available_cols)
    if concat_expr is not None:
        return concat_expr

    cleaned = clean_action_val(val_str)
    if not cleaned:
        return pl.lit("")

    ref_let = is_column_reference(cleaned)
    if ref_let:
        ref_col = resolve_column_name(src_file, ref_let, default_table, available_cols)
        if ref_col and ref_col in available_cols:
            return pl.col(ref_col).cast(pl.Utf8).fill_null("")

    return pl.lit(cleaned)


def get_active_run_matrix_file(target_field: str, run_id: str | None = None) -> Path | None:
    """Locate Matrix Excel workbook dynamically based on target field prefix."""
    search_dirs = []
    if run_id:
        search_dirs.extend([
            Path(f"workspace/runs/{run_id}/input/matrix"),
            Path(f"workspace/runs/{run_id}/in/matrix"),
        ])
    search_dirs.extend([Path("workspace/input/matrix"), Path("workspace/input")])

    for s_dir in search_dirs:
        if s_dir.exists():
            xlsx_files = [f for f in s_dir.glob("*.xlsx") if not f.name.startswith("~$")]
            if xlsx_files:
                tf_prefix = target_field.split(".")[0].lower() if "." in target_field else target_field.lower()
                for f in xlsx_files:
                    if tf_prefix in f.name.lower():
                        return f
                return xlsx_files[0]
    return None


def load_and_parse_matrix(target_field: str, run_id: str | None = None) -> pd.DataFrame:
    """Load Matrix Excel dynamically starting from standard header row."""
    target_file = get_active_run_matrix_file(target_field, run_id)
    if not target_file:
        return pd.DataFrame()

    try:
        df_matrix = pd.read_excel(target_file, sheet_name=0, header=3, dtype=str)
        df_matrix.columns = [str(c).strip() for c in df_matrix.columns]
        return df_matrix
    except Exception:
        return pd.DataFrame()


def evaluate_matrix_rule(
    target_field: str,
    df: pl.DataFrame,
    matrix_df: pd.DataFrame,
    src_file: str,
    default_table: str,
) -> pl.Expr:
    """Generic Decision Matrix Evaluator."""
    if matrix_df.empty:
        return pl.lit("")

    tf_lower = target_field.lower()
    is_group_type = "grp" in tf_lower or "group" in tf_lower

    target_col_name = None
    if is_group_type:
        for c in ["Group Type", "dp-type.grp-type", "ln-type.grp-type", "Group"]:
            if c in matrix_df.columns:
                target_col_name = c
                break
        if not target_col_name and len(matrix_df.columns) >= 9:
            target_col_name = matrix_df.columns[8]
    else:
        for c in ["NEW Share Type", "NEW Loan Type", "NEW Type", "dp-type.type", "ln-type.type", "Type"]:
            if c in matrix_df.columns:
                target_col_name = c
                break
        if not target_col_name and len(matrix_df.columns) >= 7:
            target_col_name = matrix_df.columns[6]

    expr = None

    for _, row in matrix_df.iterrows():
        logic_str = str(row.get("Programming Logic", "") if "Programming Logic" in matrix_df.columns else row.iloc[4]).strip()
        if not logic_str or logic_str.lower() in ["nan", "none"]:
            continue

        output_val = str(row.get(target_col_name, "")).strip() if target_col_name else ""
        if output_val.endswith(".0"):
            output_val = output_val[:-2]

        if not output_val or output_val.lower() in ["nan", "none"]:
            continue

        match = re.search(r"col(?:umn)?\s*([A-Za-z0-9_]+)\s*=\s*([0-9A-Za-z]+)", logic_str, re.IGNORECASE)
        if match:
            col_letter = match.group(1).upper()
            val_target = match.group(2).strip()
            if val_target.endswith(".0"):
                val_target = val_target[:-2]

            resolved_col = resolve_column_name(src_file, col_letter, default_table, df.columns)
            if resolved_col and resolved_col in df.columns:
                col_expr = (
                    pl.col(resolved_col)
                    .cast(pl.Utf8)
                    .str.strip_chars()
                    .str.replace_all(r"\.0+$", "")
                )
                cond = (col_expr == val_target)

                if expr is None:
                    expr = pl.when(cond).then(pl.lit(output_val))
                else:
                    expr = expr.when(cond).then(pl.lit(output_val))

    return expr.otherwise(pl.lit("")) if expr is not None else pl.lit("")


def build_dnf_condition_expr(
    condition_tree: list[list[dict]],
    src_file: str,
    default_table: str,
    df_columns: list,
    fallback_col_letter: str = "",
) -> pl.Expr | None:
    """Build Polars boolean expression from Disjunctive Normal Form (OR of ANDs)."""
    branch_or_expr = None

    for and_group in condition_tree:
        group_and_expr = None

        for sc in and_group:
            sc_col_letter = sc.get("col") or fallback_col_letter
            sc_col_name = resolve_column_name(src_file, sc_col_letter, default_table, df_columns)

            if not sc_col_name or sc_col_name not in df_columns:
                continue

            sc_op = sc.get("op", "==")
            sc_val_raw = str(sc.get("val") or "").strip()
            is_col_ref = sc.get("is_col_ref", False)

            # Column vs Column equality
            if is_col_ref:
                target_col_b = resolve_column_name(src_file, sc_val_raw, default_table, df_columns)
                if target_col_b and target_col_b in df_columns:
                    col_a_clean = pl.col(sc_col_name).cast(pl.Utf8).str.replace_all(r"[\-\s]", "").str.strip_chars()
                    col_b_clean = pl.col(target_col_b).cast(pl.Utf8).str.replace_all(r"[\-\s]", "").str.strip_chars()
                    sub_expr = (col_a_clean == col_b_clean) & (col_a_clean != "")
                    if sc_op == "!=":
                        sub_expr = ~sub_expr
                else:
                    sub_expr = None
            else:
                raw_tokens = [
                    v.strip().strip("'\"").strip()
                    for v in re.split(r",|\bOR\b", sc_val_raw, flags=re.IGNORECASE)
                    if v.strip()
                ]

                match_blank = any(bv.upper() in ["BLANK", "NULL", "EMPTY", "''", '""'] for bv in raw_tokens)
                literal_values = [v.upper() for v in raw_tokens if v.upper() not in ["BLANK", "NULL", "EMPTY", "''", '""']]

                col_val_expr = (
                    pl.col(sc_col_name)
                    .cast(pl.Utf8)
                    .str.strip_chars()
                    .str.replace_all(r"\.0+$", "")
                    .str.to_uppercase()
                )

                sub_expr = None
                if literal_values:
                    sub_expr = col_val_expr.is_in(literal_values)

                if match_blank:
                    blank_cond = pl.col(sc_col_name).is_null() | (pl.col(sc_col_name).cast(pl.Utf8).str.strip_chars() == "")
                    sub_expr = (sub_expr | blank_cond) if sub_expr is not None else blank_cond

                if sc_op == "!=" and sub_expr is not None:
                    sub_expr = ~sub_expr

            if sub_expr is not None:
                if group_and_expr is None:
                    group_and_expr = sub_expr
                else:
                    group_and_expr = group_and_expr & sub_expr

        if group_and_expr is not None:
            if branch_or_expr is None:
                branch_or_expr = group_and_expr
            else:
                branch_or_expr = branch_or_expr | group_and_expr

    return branch_or_expr


class ConditionalExecutor(BaseExecutor):
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
        """Deterministic Rule Executor: Pure literal condition matching without guessing."""
        # 1. Matrix Lookup evaluation
        if "matrix" in raw_notes.lower() or "type" in target_field.lower() or dsl_dict.get("type") == "matrix":
            matrix_df = load_and_parse_matrix(target_field=target_field)
            if not matrix_df.empty:
                return evaluate_matrix_rule(
                    target_field=target_field,
                    df=df,
                    matrix_df=matrix_df,
                    src_file=src_file,
                    default_table=default_table,
                )

        # 2. Dynamic Column Concatenation evaluation
        if dsl_dict.get("type") == "concat":
            cols = dsl_dict.get("cols", [])
            delimiter = dsl_dict.get("delimiter", " ")
            col_exprs = []
            for c_let in cols:
                c_name = resolve_column_name(src_file, c_let, default_table, df.columns)
                if c_name and c_name in df.columns:
                    col_exprs.append(pl.col(c_name).cast(pl.Utf8).fill_null(""))
            if col_exprs:
                concat_expr = col_exprs[0]
                for e in col_exprs[1:]:
                    concat_expr = concat_expr + pl.lit(delimiter) + e
                return concat_expr.str.strip_chars().str.replace_all(r"\s+", " ")

        # 3. Deterministic Conditional Branches Evaluation
        branches = dsl_dict.get("branches", [])
        if not branches and (dsl_dict.get("if_val") or dsl_dict.get("then_val")):
            branches = [{
                "if_col": dsl_dict.get("if_col") or src_col,
                "if_val": dsl_dict.get("if_val"),
                "then_val": dsl_dict.get("then_val"),
                "else_val": dsl_dict.get("else_val"),
                "raw_condition": dsl_dict.get("raw_condition", ""),
                "condition_tree": dsl_dict.get("condition_tree"),
            }]

        if branches:
            expr = None
            last_else_expr = None

            for branch in branches:
                then_val_raw = str(branch.get("then_val") or "").strip()
                else_val_raw = branch.get("else_val")
                b_primary_col_letter = branch.get("if_col") or src_col
                b_primary_col_name = resolve_column_name(src_file, b_primary_col_letter, default_table, df.columns)

                then_expr = resolve_target_value_expr(then_val_raw, src_file, default_table, df.columns, primary_col_name=b_primary_col_name)

                if else_val_raw is not None:
                    last_else_expr = resolve_target_value_expr(str(else_val_raw), src_file, default_table, df.columns, primary_col_name=b_primary_col_name)

                condition_tree = branch.get("condition_tree")
                if not condition_tree:
                    raw_cond_str = str(branch.get("raw_condition") or "").upper()
                    is_ne = "<>" in raw_cond_str or "!=" in raw_cond_str or " NOT " in raw_cond_str
                    condition_tree = [[{
                        "col": b_primary_col_letter,
                        "op": "!=" if is_ne else "==",
                        "val": str(branch.get("if_val") or ""),
                        "is_col_ref": False
                    }]]

                branch_cond = build_dnf_condition_expr(condition_tree, src_file, default_table, df.columns, b_primary_col_letter)

                if branch_cond is not None:
                    if expr is None:
                        expr = pl.when(branch_cond).then(then_expr)
                    else:
                        expr = expr.when(branch_cond).then(then_expr)

            # Resolve fallback expression
            primary_col = resolve_column_name(src_file, src_col, default_table, df.columns) if src_col else None
            if last_else_expr is not None:
                fallback = last_else_expr
            elif primary_col and primary_col in df.columns:
                fallback = pl.col(primary_col).cast(pl.Utf8).fill_null("")
            else:
                fallback = pl.lit("")

            return expr.otherwise(fallback) if expr is not None else fallback

        # 4. Direct Column Fallback
        target_c = resolve_column_name(src_file, src_col, default_table, df.columns) if src_col else None
        if target_c and target_c in df.columns:
            return pl.col(target_c).cast(pl.Utf8)

        return pl.lit(None)