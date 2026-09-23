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
    Strict business key detection per BSDC domain rules:
    - Member: ONLY member number (e.g. mb.mb-num)
    - Shares Certificates: mb-num + cert-num (EXCLUDE type so type changes trigger VARIANCE instead of MISSING_RECORD)
    - Shares Regular/Drafts: mb-num
    - Loans: ln-num (or mb-num)
    """
    fn_lower = file_name.lower()

    # 1. MEMBER MODULE: Strictly single member account number ONLY
    if ("member" in fn_lower or "mb" in fn_lower) and not any(x in fn_lower for x in ["share", "loan", "dp", "ln"]):
        mb_col = find_column_by_patterns(columns, ["mb.mb-num", "mb-num", "member_num", "member-num", "account"])
        if mb_col:
            return [mb_col]

    # 2. SHARES / DEPOSIT MODULE: Exclude 'type' column from primary keys
    if any(x in fn_lower for x in ["share", "dp", "deposit"]):
        mb_col = find_column_by_patterns(columns, ["dp.mb-num", "mb-num", "account_num", "member_num"])
        
        # For Certificates / CDs: Primary Key is Member Number + Certificate Number
        if any(x in fn_lower for x in ["certific", "cd"]):
            cert_col = find_column_by_patterns(
                columns, 
                ["dp.cert-num", "cert-num", "certnum", "dp.cert", "cert", "cert_num", "certificate", "cert-no", "cert_no"]
            )
            keys = [k for k in [mb_col, cert_col] if k]
            if keys:
                return keys

        # For Regular Shares / Drafts: Primary Key is Member Number
        if mb_col:
            return [mb_col]

    # 3. LOAN MODULE: Exclude 'type' column from primary keys
    if any(x in fn_lower for x in ["loan", "ln"]):
        ln_col = find_column_by_patterns(columns, ["ln.ln-num", "ln-num", "loan_num", "loan-num", "note-num"])
        if ln_col:
            return [ln_col]
        
        mb_col = find_column_by_patterns(columns, ["ln.mb-num", "mb-num"])
        if mb_col:
            return [mb_col]

    # 4. Fallback Generic Search (Excluding type columns)
    for c in columns:
        cl = c.lower()
        if re.search(r"(\.|^|-|_)(num|number|id|account|acct)($|\b|-|_)", cl) and "type" not in cl:
            return [c]

    return [columns[0]]


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
        is_shares_module: bool = False,
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

                if col_name.lower() in ["dp.dp-desc", "dp-desc", "description"]:
                    if len(val) > 25:
                        val = val[:25].strip()

                fields_map[col_name] = val

            result_map[final_key] = fields_map

        return result_map