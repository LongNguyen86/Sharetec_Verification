import json
import re
import polars as pl
from src.bsdc_engine.text import col_letter_to_index
from src.bsdc_engine.generate.executors.base import BaseExecutor


def resolve_column_name(data_file: str, col_letter: str, default_table: str, available_cols: list) -> str | None:
    data_file_clean = data_file.strip().upper().replace(" ", "_") if data_file and str(data_file).strip().upper() not in ["", "N/A", "NAN", "NONE"] else None
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


def find_member_table_prefix(columns: list[str], default_table: str) -> str:
    """Dynamically detect table prefix containing Member/Account data in the current DataFrame."""
    tables = list(set(c.split("::")[0] for c in columns if "::" in c))
    for t in tables:
        if any(keyword in t for keyword in ["MEMBER", "CUST", "ACCT", "CLIENT"]):
            return t
    return default_table.upper().replace(" ", "_") if default_table else (tables[0] if tables else "")


def clean_action_val(val_str: str) -> str:
    """Extract clean assigned target value by stripping ASSIGN, CREATE, quotes, and trailing punctuation (; , .)."""
    if not val_str:
        return ""
    s = val_str.strip()
    if s.upper() in ["LEAVE BLANK", "BLANK", "NULL", "NONE"]:
        return ""
    s = re.sub(r"^(ASSIGN|CREATE)\s+(ALL\s+)?", "", s, flags=re.IGNORECASE).strip()
    s = s.rstrip(";,.").strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    s = s.rstrip(";,.").strip()
    return s


def is_column_reference(val_str: str) -> str | None:
    """Check if value is a reference to a source column e.g. 'COLUMN B', 'COL B', 'B'."""
    m = re.search(r"^\s*(?:COLUMN|COL)?\s*([A-Za-z]{1,3})\s*$", val_str, re.IGNORECASE)
    if m:
        col_let = m.group(1).upper()
        reserved_codes = [
            "M", "F", "Y", "N", "CD", "CK", "SH", "PR", "NO", "YES",
            "BUS", "DBA", "COOP", "GRPO", "IGLE", "INCP", "SN", "YUTH", "NON"
        ]
        if col_let not in reserved_codes:
            return col_let
    return None


def parse_concat_column_expr(
    val_str: str, src_file: str, default_table: str, available_cols: list
) -> pl.Expr | None:
    """Parse column addition expressions like 'COLUMN D + E + F + G' or 'COLUMN F + G' into Polars concat."""
    cleaned = clean_action_val(val_str)
    s = re.sub(r"^\s*(?:COLUMN|COL)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    
    if "+" in s:
        parts = [p.strip() for p in s.split("+")]
        col_exprs = []
        for p in parts:
            p_clean = re.sub(r"^\s*(?:COLUMN|COL)?\s*", "", p, flags=re.IGNORECASE).strip()
            col_let = is_column_reference(p_clean) or (p_clean.upper() if re.match(r"^[A-Za-z]{1,3}$", p_clean) else None)
            if col_let:
                ref_col = resolve_column_name(src_file, col_let, default_table, available_cols)
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


def extract_starting_prefix(then_str: str) -> str | None:
    """Extract prefix number from instructions like 'CREATE ... STARTING WITH 98'."""
    m = re.search(r"(?:STARTING|BEGINNING)\s+WITH\s+([0-9]+)", then_str, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def resolve_target_value_expr(
    val_str: str, src_file: str, default_table: str, available_cols: list, primary_col_name: str | None = None
) -> pl.Expr:
    """Resolves whether action value is a reference to a source column, column concatenation, prefix rule, or literal string."""
    prefix_num = extract_starting_prefix(val_str)
    if prefix_num and primary_col_name and primary_col_name in available_cols:
        return pl.lit(prefix_num) + pl.col(primary_col_name).cast(pl.Utf8).fill_null("").str.strip_chars()

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
        raw_notes_upper = raw_notes.upper()
        
        # 1. Dynamically evaluate chained DSL branches if present in dsl_dict
        branches = dsl_dict.get("branches", [])
        if not branches and (dsl_dict.get("if_val") or dsl_dict.get("then_val")):
            branches = [{
                "if_col": dsl_dict.get("if_col") or src_col,
                "if_val": dsl_dict.get("if_val"),
                "then_val": dsl_dict.get("then_val"),
                "else_val": dsl_dict.get("else_val"),
                "raw_condition": dsl_dict.get("raw_condition", ""),
            }]

        if branches:
            base_branches = []
            prefix_branches = []

            for branch in branches:
                then_val_raw = str(branch.get("then_val") or "").strip()
                prefix_match = re.search(
                    r"ADD\s+([A-Za-z0-9_]+)\s+TO\s+THE\s+BEGINNING", 
                    then_val_raw, 
                    re.IGNORECASE
                )
                if prefix_match:
                    prefix_branches.append((branch, prefix_match.group(1)))
                else:
                    base_branches.append(branch)

            expr = None
            last_else_expr = None

            for branch in base_branches:
                b_col_letter = branch.get("if_col") or src_col
                b_col_name = resolve_column_name(src_file, b_col_letter, default_table, df.columns)

                if not b_col_name or b_col_name not in df.columns:
                    continue

                if_val_raw = str(branch.get("if_val") or "").strip()
                then_val_raw = str(branch.get("then_val") or "").strip()
                else_val_raw = branch.get("else_val")
                raw_cond_str = str(branch.get("raw_condition") or "").upper()

                then_expr = resolve_target_value_expr(then_val_raw, src_file, default_table, df.columns, primary_col_name=b_col_name)

                if else_val_raw is not None:
                    last_else_expr = resolve_target_value_expr(str(else_val_raw), src_file, default_table, df.columns, primary_col_name=b_col_name)

                # Check if operator is NOT EQUAL (<> or !=)
                is_not_equal = "<>" in raw_cond_str or "!=" in raw_cond_str or " NOT " in raw_cond_str

                target_col_ref_let = is_column_reference(if_val_raw)
                if target_col_ref_let:
                    target_col_b = resolve_column_name(src_file, target_col_ref_let, default_table, df.columns)
                    if target_col_b and target_col_b in df.columns:
                        col_a_clean = pl.col(b_col_name).cast(pl.Utf8).str.replace_all(r"[\-\s]", "").str.strip_chars()
                        col_b_clean = pl.col(target_col_b).cast(pl.Utf8).str.replace_all(r"[\-\s]", "").str.strip_chars()
                        cond_expr = (col_a_clean == col_b_clean) & (col_a_clean != "") & (col_a_clean != "0")
                        if is_not_equal:
                            cond_expr = ~cond_expr
                    else:
                        cond_expr = None
                else:
                    raw_vals = [v.strip().upper() for v in if_val_raw.split(",") if v.strip()]
                    match_blank = "BLANK" in raw_vals or "NULL" in raw_vals or "EMPTY" in raw_vals
                    clean_vals = [v for v in raw_vals if v not in ["BLANK", "NULL", "EMPTY"]]

                    expanded_vals = []
                    for v in clean_vals:
                        expanded_vals.append(v)
                        if "<" in v:
                            expanded_vals.append(v.replace("<", "O"))
                            expanded_vals.append(v.replace("<", ""))

                    col_upper = pl.col(b_col_name).cast(pl.Utf8).str.strip_chars().str.to_uppercase()

                    cond_expr = None
                    if expanded_vals:
                        cond_expr = col_upper.is_in(expanded_vals)

                    if match_blank:
                        blank_cond = pl.col(b_col_name).is_null() | (pl.col(b_col_name).cast(pl.Utf8).str.strip_chars() == "")
                        cond_expr = (cond_expr | blank_cond) if cond_expr is not None else blank_cond

                    if is_not_equal and cond_expr is not None:
                        cond_expr = ~cond_expr

                if cond_expr is not None:
                    if expr is None:
                        expr = pl.when(cond_expr).then(then_expr)
                    else:
                        expr = expr.when(cond_expr).then(then_expr)

            primary_col = resolve_column_name(src_file, src_col, default_table, df.columns)
            fallback = last_else_expr if last_else_expr is not None else (
                pl.col(primary_col).cast(pl.Utf8) if primary_col and primary_col in df.columns else pl.lit("")
            )

            result_expr = expr.otherwise(fallback) if expr is not None else fallback

            # Prevent assigning ELSE values to BLANK/NULL source rows if BLANK was not explicitly part of IF conditions
            if primary_col and primary_col in df.columns:
                primary_blank_cond = pl.col(primary_col).is_null() | (pl.col(primary_col).cast(pl.Utf8).str.strip_chars() == "")
                has_explicit_blank = any(
                    "BLANK" in [v.strip().upper() for v in str(b.get("if_val") or "").split(",")]
                    or "NULL" in [v.strip().upper() for v in str(b.get("if_val") or "").split(",")]
                    for b in branches
                )
                if not has_explicit_blank:
                    result_expr = pl.when(primary_blank_cond).then(pl.lit("")).otherwise(result_expr)

            for p_branch, p_prefix in prefix_branches:
                p_col_letter = p_branch.get("if_col") or src_col
                p_col_name = resolve_column_name(src_file, p_col_letter, default_table, df.columns)

                if not p_col_name or p_col_name not in df.columns:
                    continue

                p_val_raw = str(p_branch.get("if_val") or "0").strip().upper()
                p_col_upper = pl.col(p_col_name).cast(pl.Utf8).str.strip_chars().str.to_uppercase()

                p_cond = (p_col_upper == p_val_raw) | (p_col_upper == "0")

                result_expr = (
                    pl.when(p_cond)
                    .then(
                        pl.when(result_expr == "")
                        .then(pl.lit(p_prefix))
                        .otherwise(pl.lit(f"{p_prefix},") + result_expr)
                    )
                    .otherwise(result_expr)
                )

            return result_expr

        # 2. Domain specific fallback overrides
        mb_prefix = find_member_table_prefix(df.columns, default_table)

        mb_first_col = [c for c in df.columns if c.startswith(f"{mb_prefix}::col_3")]
        mb_mid_col = [c for c in df.columns if c.startswith(f"{mb_prefix}::col_4")]
        mb_last_f_col = [c for c in df.columns if c.startswith(f"{mb_prefix}::col_5")]
        mb_last_g_col = [c for c in df.columns if c.startswith(f"{mb_prefix}::col_6")]
        mb_branch_col = [c for c in df.columns if c.startswith(f"{mb_prefix}::col_18")]

        if "MB.FIRST-NAME" in raw_notes_upper or target_field.endswith("first-name"):
            if mb_first_col: return pl.col(mb_first_col[0]).cast(pl.Utf8)

        if "MB.MIDDLE-NAME" in raw_notes_upper or target_field.endswith("middle-name"):
            if mb_mid_col: return pl.col(mb_mid_col[0]).cast(pl.Utf8)

        if "MB.LAST-NAME" in raw_notes_upper or target_field.endswith("last-name"):
            if mb_last_f_col:
                val_f = pl.col(mb_last_f_col[0]).fill_null("").cast(pl.Utf8)
                val_g = pl.col(mb_last_g_col[0]).fill_null("").cast(pl.Utf8) if mb_last_g_col else pl.lit("")
                return (val_f + pl.lit(" ") + val_g).str.strip_chars()

        if "MB.BRANCH" in raw_notes_upper or target_field.endswith(".branch"):
            if mb_branch_col:
                val_br = pl.col(mb_branch_col[0]).cast(pl.Utf8).fill_null("1").str.strip_chars()
                return pl.when(val_br == "").then(pl.lit("1")).otherwise(val_br)
            return pl.lit("1")

        if target_field.endswith("grp-type"):
            if "CERTIF" in sec_name.upper(): return pl.lit("CD")
            col_b = resolve_column_name(src_file, "B", default_table, df.columns)
            if col_b and col_b in df.columns:
                val_b = pl.col(col_b).cast(pl.Utf8).fill_null("").str.strip_chars()
                return (
                    pl.when(val_b.str.starts_with("3") | (val_b == "12") | (val_b == "1202"))
                    .then(pl.lit("CD"))
                    .when(val_b.str.starts_with("2"))
                    .then(pl.lit("CK"))
                    .when(val_b.str.starts_with("1"))
                    .then(pl.lit("SH"))
                    .otherwise(pl.lit("SH"))
                )
            return pl.lit("CD") if "CERTIF" in sec_name.upper() else pl.lit("SH")

        if target_field == "dp.type":
            col_b = resolve_column_name(src_file, "B", default_table, df.columns)
            if col_b and col_b in df.columns: return pl.col(col_b).cast(pl.Utf8)

        if target_field.endswith("status-cd"):
            col_u = resolve_column_name(src_file, "U", default_table, df.columns)
            if col_u and col_u in df.columns:
                val_u = pl.col(col_u).cast(pl.Utf8).fill_null("").str.strip_chars()
                return pl.when(val_u == "1").then(pl.lit("CLOSED")).otherwise(pl.lit("ACTIVE")).otherwise(pl.lit("ACTIVE"))
            return pl.lit("ACTIVE")

        if target_field == "mb.mb-num":
            col_a = resolve_column_name(src_file, "A", default_table, df.columns)
            col_b = resolve_column_name(src_file, "B", default_table, df.columns)
            if col_a and col_a in df.columns:
                val_a = pl.col(col_a).cast(pl.Utf8).fill_null("").str.strip_chars()
                val_a_clean = val_a.str.replace_all(r"[\-\s]", "")
                if col_b and col_b in df.columns:
                    val_b = pl.col(col_b).cast(pl.Utf8).fill_null("").str.strip_chars()
                    val_b_clean = val_b.str.replace_all(r"[\-\s]", "")
                    cond = (val_a_clean == val_b_clean) & (val_a_clean != "0") & (val_a_clean != "")
                    return pl.when(cond.fill_null(False)).then(pl.lit("98") + val_a).otherwise(val_a)
                return val_a

        target_c = resolve_column_name(src_file, src_col, default_table, df.columns)
        if target_c and target_c in df.columns:
            return pl.col(target_c).cast(pl.Utf8)

        return pl.lit(None)