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

        mb_first_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_3")]
        mb_mid_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_4")]
        mb_last_f_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_5")]
        mb_last_g_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_6")]
        mb_branch_col = [c for c in df.columns if c.startswith("MEMBER_ACCOUNTS::col_18")]

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
                return pl.when(val_u == "1").then(pl.lit("CLOSED")).otherwise(pl.lit("ACTIVE"))
            return pl.lit("ACTIVE")

        if target_field == "mb.mb-num":
            col_a = resolve_column_name(src_file, "A", default_table, df.columns)
            col_b = resolve_column_name(src_file, "B", default_table, df.columns)
            if col_a and col_a in df.columns:
                val_a = pl.col(col_a).cast(pl.Utf8).fill_null("").str.strip_chars()
                if col_b and col_b in df.columns:
                    val_b = pl.col(col_b).cast(pl.Utf8).fill_null("").str.strip_chars()
                    cond = (val_a == val_b) & (val_a != "0") & (val_a != "")
                    return pl.when(cond.fill_null(False)).then(pl.lit("99") + val_a.str.zfill(8)).otherwise(val_a)
                return val_a

        target_c = resolve_column_name(src_file, src_col, default_table, df.columns)
        if target_c and target_c in df.columns:
            return pl.col(target_c).cast(pl.Utf8)

        return pl.lit(None)