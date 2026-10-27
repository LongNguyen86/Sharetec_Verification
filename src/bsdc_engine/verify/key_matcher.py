import re
from typing import Dict, List, Any, Optional
import polars as pl


def find_column_by_patterns(columns: List[str], candidate_patterns: List[str]) -> Optional[str]:
    cols_map = {c.strip().lower(): c for c in columns}
    for pat in candidate_patterns:
        pat_lower = pat.lower()
        if pat_lower in cols_map:
            return cols_map[pat_lower]

    for pat in candidate_patterns:
        pat_lower = pat.lower()
        for cl_lower, orig_name in cols_map.items():
            if cl_lower.endswith(pat_lower) or pat_lower in cl_lower:
                return orig_name
    return None


def detect_key_columns(columns: list[str], file_name: str = "") -> list[str]:
    """
    Generic Natural Key Detector across all 18 core banking modules.
    Priority order:
    1. Account/Member/Card/Loan/GL identifier columns ending with num, id, no, acct.
    2. Composite keys if sub-sequence/line/item/cert exists.
    3. Exclude descriptive, type, status, and balance columns to prevent false matches.
    """
    if not columns:
        return []

    # Filter out descriptive and classification columns
    clean_cols = []
    for c in columns:
        cl = c.lower().strip()
        if any(ex in cl for ex in ["type", "status", "desc", "date", "amt", "amount", "bal", "balance", "flag"]):
            continue
        clean_cols.append(c)

    # Search for primary natural key candidates
    key_candidates = []
    for c in clean_cols:
        cl = c.lower()
        if re.search(r"(\.|^|_|-)(num|number|id|acct|account|no)($|\b|_|-)", cl):
            key_candidates.append(c)

    # Check for composite sub-identifiers (e.g. cert, item, seq, line)
    sub_keys = [c for c in clean_cols if any(sub in c.lower() for sub in ["cert", "item", "seq", "line"])]

    if key_candidates:
        if sub_keys and sub_keys[0] != key_candidates[0]:
            return [key_candidates[0], sub_keys[0]]
        return [key_candidates[0]]

    # Fallback to the first non-descriptive column, or the first column
    return [clean_cols[0]] if clean_cols else [columns[0]]


class KeyMatcher:

    @staticmethod
    def clean_key_value(val: Any) -> str:
        if val is None:
            return ""
        s = str(val).strip()
        if s.endswith(".0"):
            s = s[:-2]
        return s

    @classmethod
    def build_record_map(
        cls,
        df: pl.DataFrame,
        key_columns: List[str],
        compare_columns: List[str],
    ) -> Dict[str, Dict[str, str]]:
        header_map = {col.strip().lower(): col for col in df.columns}
        result_map = {}
        key_counter_map = {}

        records = df.to_dicts()
        for row in records:
            key_parts = []
            for col_name in key_columns:
                actual_col = header_map.get(col_name.lower())
                val = row.get(actual_col) if actual_col else ""
                key_parts.append(cls.clean_key_value(val))

            base_key = "_".join(key_parts)
            if not base_key or base_key == "_":
                continue

            lower_base_key = base_key.lower()
            occurrence = key_counter_map.get(lower_base_key, 0)
            key_counter_map[lower_base_key] = occurrence + 1
            final_key = base_key if occurrence == 0 else f"{base_key}_dup{occurrence}"

            fields_map = {}
            for col_name in compare_columns:
                actual_col = header_map.get(col_name.lower())
                val = cls.clean_key_value(row.get(actual_col)) if actual_col else ""
                fields_map[col_name] = val

            result_map[final_key] = fields_map

        return result_map