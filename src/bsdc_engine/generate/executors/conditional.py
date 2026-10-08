import json
import re
from pathlib import Path
import pandas as pd
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
    tables = list(set(c.split("::")[0] for c in columns if "::" in c))
    for t in tables:
        if any(keyword in t for keyword in ["MEMBER", "CUST", "ACCT", "CLIENT"]):
            return t
    return default_table.upper().replace(" ", "_") if default_table else (tables[0] if tables else "")


def clean_action_val(val_str: str) -> str:
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
    if not val_str:
        return None
    m = re.search(r"^\s*(?:COLUMN|COL)\s+([A-Za-z]{1,3})\s*$", val_str, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def parse_concat_column_expr(
    val_str: str, src_file: str, default_table: str, available_cols: list
) -> pl.Expr | None:
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
    m = re.search(r"(?:STARTING|BEGINNING)\s+WITH\s+([0-9]+)", then_str, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def resolve_target_value_expr(
    val_str: str, src_file: str, default_table: str, available_cols: list, primary_col_name: str | None = None
) -> pl.Expr:
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


def get_active_run_matrix_file(target_field: str, run_id: str | None = None) -> Path | None:
    matrix_dir = None

    if run_id:
        for sub in ["input/matrix", "in/matrix", "input", "in"]:
            cand = Path(f"workspace/runs/{run_id}/{sub}")
            if cand.exists() and any(cand.glob("*.xlsx")):
                matrix_dir = cand
                break

    if not matrix_dir:
        runs_dir = Path("workspace/runs")
        if runs_dir.exists():
            valid_runs = [d for d in runs_dir.iterdir() if d.is_dir()]
            if valid_runs:
                latest_run = max(valid_runs, key=lambda d: d.stat().st_mtime)
                for sub in ["input/matrix", "in/matrix", "input", "in"]:
                    cand = latest_run / sub
                    if cand.exists() and any(cand.glob("*.xlsx")):
                        matrix_dir = cand
                        break

    if not matrix_dir:
        for fallback_path in [Path("workspace/input/matrix"), Path("workspace/input")]:
            if fallback_path.exists() and any(fallback_path.glob("*.xlsx")):
                matrix_dir = fallback_path
                break

    if not matrix_dir:
        print("⚠️ [Matrix Lookup] Matrix directory not found!")
        return None

    xlsx_files = [f for f in matrix_dir.glob("*.xlsx") if not f.name.startswith("~$")]
    if not xlsx_files:
        print(f"⚠️ [Matrix Lookup] No Excel matrix files found in: {matrix_dir}")
        return None

    tf_lower = target_field.lower()

    if tf_lower.startswith("dp.") or "grp-type" in tf_lower or tf_lower == "dp.type":
        for f in xlsx_files:
            fname = f.name.lower()
            if any(k in fname for k in ["share", "certificate", "deposit", "dp"]):
                return f

    if tf_lower.startswith("ln.") or tf_lower == "ln.type":
        for f in xlsx_files:
            fname = f.name.lower()
            if any(k in fname for k in ["loan", "ln"]):
                return f

    return xlsx_files[0]


def load_and_parse_matrix(target_field: str, run_id: str | None = None) -> pd.DataFrame:
    target_file = get_active_run_matrix_file(target_field, run_id)
    if not target_file:
        return pd.DataFrame()

    print(f"ℹ️ [Matrix Lookup] Loading domain matrix file: {target_file}")
    df_matrix = pd.read_excel(target_file, sheet_name=0, header=3, dtype=str)
    df_matrix.columns = [str(c).strip() for c in df_matrix.columns]
    return df_matrix


def get_certified_deposits_term_map(run_id: str | None = None) -> dict[str, str]:
    """
    Dynamically scan input directory and extract Member ID -> Term mapping
    by inspecting CSV header columns, excluding auxiliary files.
    """
    search_dirs = []
    if run_id:
        search_dirs.extend([
            Path(f"workspace/runs/{run_id}/input/raw_data"),
            Path(f"workspace/runs/{run_id}/input"),
            Path(f"workspace/runs/{run_id}"),
        ])
    search_dirs.extend([Path("workspace/input"), Path("workspace")])

    exclude_keywords = ["ACCRUAL", "YTD", "HIST", "TRANS", "SUMMARY", "DRAFT"]
    target_file = None

    for d in search_dirs:
        if not d.exists():
            continue
        csv_files = [f for f in d.rglob("*.csv") if not f.name.startswith("~$")]
        for f in csv_files:
            fname_upper = f.name.upper()
            if any(ex in fname_upper for ex in exclude_keywords):
                continue
            
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    header = fh.readline().upper()
                if "TERM" in header or "CDTERM" in header:
                    target_file = f
                    break
            except Exception:
                continue

        if target_file:
            break

    if not target_file:
        return {}

    term_map = {}
    try:
        df_cd = pl.read_csv(target_file, has_header=True, infer_schema_length=0)
        cols = df_cd.columns
        
        member_col = cols[0]
        term_col = cols[3] if len(cols) > 3 else cols[0]

        for c in cols:
            c_upper = c.upper()
            if any(k in c_upper for k in ["MEMBER", "MB_NUM", "MB_NUR", "ACCT"]):
                member_col = c
            if any(k in c_upper for k in ["CDTERM", "TERM", "CD_TERM"]):
                term_col = c

        for row in df_cd.select([member_col, term_col]).to_dicts():
            m_val = str(row.get(member_col, "") or "").strip().split(".")[0]
            t_val = str(row.get(term_col, "") or "").strip().split(".")[0]
            if m_val and t_val and t_val != "0":
                term_map[m_val] = t_val
    except Exception as e:
        print(f"⚠️ [Matrix Lookup] Could not parse term map: {e}")

    return term_map


def parse_col_d_condition(logic_str: str, col_d_expr: pl.Expr) -> pl.Expr | None:
    if not logic_str or col_d_expr is None:
        return None

    col_d_num = col_d_expr.cast(pl.Utf8).str.strip_chars().str.replace(r"\.0$", "").cast(pl.Int64, strict=False)

    m_d_part = re.search(r"(?:column|col)\s+D\s*=?\s*([^\n\r]+)", logic_str, re.IGNORECASE)
    if not m_d_part:
        return None

    d_text = m_d_part.group(1).strip()
    d_text = re.split(r"\b(?:create|make|then|assign)\b", d_text, flags=re.IGNORECASE)[0].strip()

    cond = None

    ranges = re.findall(r"([0-9]+)\s*(?:through|-|to)\s*([0-9]+)", d_text, re.IGNORECASE)
    for r_min, r_max in ranges:
        c_range = (col_d_num >= int(r_min)) & (col_d_num <= int(r_max))
        cond = (cond | c_range) if cond is not None else c_range

    d_text_clean = re.sub(r"[0-9]+\s*(?:through|-|to)\s*[0-9]+", "", d_text, flags=re.IGNORECASE)
    singles = re.findall(r"\b([0-9]+)\b", d_text_clean)
    for s in singles:
        c_single = (col_d_num == int(s))
        cond = (cond | c_single) if cond is not None else c_single

    return cond


def evaluate_matrix_rule(
    target_field: str,
    df: pl.DataFrame,
    matrix_df: pd.DataFrame,
    src_file: str,
    default_table: str,
) -> pl.Expr:
    if matrix_df.empty:
        return pl.lit("")

    col_b_name = resolve_column_name(src_file, "B", default_table, df.columns)
    col_a_name = resolve_column_name(src_file, "A", default_table, df.columns)

    if not col_b_name or col_b_name not in df.columns:
        return pl.lit("")

    col_b_expr = (
        pl.col(col_b_name)
        .cast(pl.Utf8)
        .str.strip_chars()
        .str.replace(r"\.0$", "")
    )

    col_d_expr = pl.lit("0")
    term_map = get_certified_deposits_term_map()
    if term_map and col_a_name and col_a_name in df.columns:
        col_a_clean = pl.col(col_a_name).cast(pl.Utf8).str.strip_chars().str.replace(r"\.0$", "")
        col_d_expr = col_a_clean.replace_strict(term_map, default=pl.lit("0"))

    is_group_type = "grp-type" in target_field or "group" in target_field.lower()

    target_col_name = None
    if is_group_type:
        for col_candidate in ["Group Type", "dp-type.grp-type", "ln-type.grp-type", "Group", "Share Group", "Account Group"]:
            if col_candidate in matrix_df.columns:
                target_col_name = col_candidate
                break
        if not target_col_name and len(matrix_df.columns) >= 9:
            target_col_name = matrix_df.columns[8]
    else:
        for col_candidate in ["NEW Share Type", "NEW Loan Type", "dp-type.type", "ln-type.type", "Share Type", "Loan Type", "Unique Type Code"]:
            if col_candidate in matrix_df.columns:
                target_col_name = col_candidate
                break
        if not target_col_name and len(matrix_df.columns) >= 7:
            target_col_name = matrix_df.columns[6]

    expr = None
    first_output_per_code = {}

    for _, row in matrix_df.iterrows():
        logic_str = str(row.get("Programming Logic", "") if "Programming Logic" in matrix_df.columns else row.iloc[4] if len(row) > 4 else "").strip()
        old_share_type = str(row.iloc[0]).strip() if len(row) > 0 else ""
        old_desc = str(row.iloc[1]).strip() if len(row) > 1 else ""
        if old_share_type.endswith(".0"):
            old_share_type = old_share_type[:-2]

        output_val = ""
        if target_col_name and target_col_name in row:
            output_val = str(row.get(target_col_name, "")).strip()

        if output_val.endswith(".0"):
            output_val = output_val[:-2]

        if is_group_type and (output_val in ["", "nan", "none"] or pd.isna(output_val)):
            desc_lower = old_desc.lower()
            if any(k in desc_lower for k in ["certifi", "deposito", "term", "cd"]):
                output_val = "Certificate"
            elif any(k in desc_lower for k in ["draft", "checking", "check"]):
                output_val = "Share Draft"
            else:
                output_val = "Reg Share"

        match_b = re.search(r"(?:col(?:umn)?|col_?)\s*B\s*=\s*([0-9A-Za-z]+)", logic_str, re.IGNORECASE)
        val_b_target = match_b.group(1).strip() if match_b else old_share_type
        if val_b_target.endswith(".0"):
            val_b_target = val_b_target[:-2]

        if not is_group_type:
            match_make = re.search(r"(?:make|create)\s+(?:share\s*type|loan\s*type|type)\s*=?\s*([0-9A-Za-z]+)", logic_str, re.IGNORECASE)
            if match_make:
                extracted_type = match_make.group(1).strip()
                if len(extracted_type) >= len(output_val) or output_val in ["", "nan", "none"]:
                    output_val = extracted_type

            if output_val.isdigit() and len(output_val) < 4:
                output_val = output_val.zfill(4)

        if not val_b_target or val_b_target.lower() in ["nan", "none"]:
            continue

        if val_b_target not in first_output_per_code and output_val not in ["", "nan", "none"]:
            first_output_per_code[val_b_target] = output_val

        cond_b = (col_b_expr == val_b_target)
        if old_share_type and old_share_type != val_b_target:
            cond_b = cond_b | (col_b_expr == old_share_type)

        cond_expr = cond_b

        has_col_d_req = "column d" in logic_str.lower() or "col d" in logic_str.lower()
        if not is_group_type and has_col_d_req:
            cond_d = parse_col_d_condition(logic_str, col_d_expr)
            if cond_d is not None:
                cond_expr = cond_b & cond_d
            else:
                cond_expr = None

        if cond_expr is not None and output_val not in ["", "nan", "none"]:
            if expr is None:
                expr = pl.when(cond_expr).then(pl.lit(output_val))
            else:
                expr = expr.when(cond_expr).then(pl.lit(output_val))

    if not is_group_type:
        for code_val, default_out in first_output_per_code.items():
            if expr is None:
                expr = pl.when(col_b_expr == code_val).then(pl.lit(default_out))
            else:
                expr = expr.when(col_b_expr == code_val).then(pl.lit(default_out))

    default_fallback = "Reg Share" if is_group_type else ""
    return expr.otherwise(pl.lit(default_fallback)) if expr is not None else pl.lit(default_fallback)


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

        if (
            target_field in ["dp.type", "dp.grp-type", "ln.type", "ln.grp-type"]
            or target_field.startswith("dp.")
            or target_field.startswith("ln.")
            or target_field.endswith("grp-type")
        ):
            matrix_df = load_and_parse_matrix(target_field=target_field)
            if not matrix_df.empty:
                return evaluate_matrix_rule(
                    target_field=target_field,
                    df=df,
                    matrix_df=matrix_df,
                    src_file=src_file,
                    default_table=default_table,
                )

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
                    last_else_expr = resolve_target_value_expr(str(else_val_raw), str(src_file), default_table, df.columns, primary_col_name=b_col_name)

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