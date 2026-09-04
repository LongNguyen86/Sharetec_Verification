from datetime import datetime
import re
from typing import Any, Dict, List


class DataComparator:

    @staticmethod
    def is_leading_zero_code(val_str: str) -> bool:
        """Check if string value is a code with leading zeros (e.g. '0001', '0100')."""
        return bool(re.match(r"^0[0-9]{2,}.*", val_str)) and "." not in val_str

    @staticmethod
    def clean_cell_value(val: Any) -> str:
        """Clean cell string removing trailing float zeros or midnight timestamp components."""
        if val is None:
            return ""
        s = str(val).strip()
        if s.endswith(".0"):
            s = s[:-2]
        if s.endswith(" 00:00:00") or s.endswith("T00:00:00"):
            s = s[:-9]
        return s

    @classmethod
    def is_value_matching(
        cls, value_mappings: dict, col_name: str, exp_val: str, act_val: str
    ) -> bool:
        """Perform exact text, date, numeric tolerance, and value mapping evaluation."""
        exp_clean = cls.clean_cell_value(exp_val)
        act_clean = cls.clean_cell_value(act_val)

        # 1. Exact case-insensitive comparison
        if exp_clean.lower() == act_clean.lower():
            return True

        # 2. Flexible Date / DateTime evaluation
        date_formats = (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%Y-%m-%dT%H:%M:%S",
        )
        for fmt_s in date_formats:
            try:
                dt_s = datetime.strptime(exp_clean, fmt_s)
                for fmt_t in date_formats:
                    try:
                        dt_t = datetime.strptime(act_clean, fmt_t)
                        if dt_s.date() == dt_t.date():
                            return True
                    except ValueError:
                        pass
            except ValueError:
                pass

        # 3. Numeric comparison with 1e-6 float variance tolerance
        if not cls.is_leading_zero_code(exp_clean) and not cls.is_leading_zero_code(
            act_clean
        ):
            try:
                d1 = float(exp_clean.replace(",", ""))
                d2 = float(act_clean.replace(",", ""))
                if abs(d1 - d2) < 1e-6:
                    return True
            except ValueError:
                pass

        # 4. Value Mappings bidirectional lookup
        col_rules = value_mappings.get(col_name, {})
        if col_rules:
            for rule_k, rule_v in col_rules.items():
                k_str = str(rule_k).strip().lower()
                v_str = str(rule_v).strip().lower()
                if (exp_clean.lower() == k_str and act_clean.lower() == v_str) or (
                    exp_clean.lower() == v_str and act_clean.lower() == k_str
                ):
                    return True

        return False

    @classmethod
    def compare_maps(
        cls,
        expected_map: Dict[str, Dict[str, str]],
        actual_map: Dict[str, Dict[str, str]],
        compare_columns: List[str],
        value_mappings: Dict[str, Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Reconcile expected_map against actual_map row-by-row and field-by-field."""
        mismatches = []
        value_mappings = value_mappings or {}
        actual_key_case_map = {k.lower(): k for k in actual_map.keys()}
        count_no = 1

        # STEP A: System-level missing column validation
        valid_columns = []
        for col in compare_columns:
            has_exp = any(rec.get(col) for rec in expected_map.values())
            has_act = any(rec.get(col) for rec in actual_map.values())

            if has_exp and not has_act:
                mismatches.append(
                    {
                        "no": count_no,
                        "key": "SYSTEM",
                        "column": col,
                        "expected": "Available in Source",
                        "actual": "Missing in Target",
                        "issue": "MISSING_COLUMN",
                    }
                )
                count_no += 1
            elif not has_exp and has_act:
                mismatches.append(
                    {
                        "no": count_no,
                        "key": "SYSTEM",
                        "column": col,
                        "expected": "Missing in Source",
                        "actual": "Available in Target",
                        "issue": "MISSING_COLUMN",
                    }
                )
                count_no += 1
            else:
                valid_columns.append(col)

        # STEP B: Record reconciliation loop
        for key, exp_rec in expected_map.items():
            act_key = actual_key_case_map.get(key.lower())
            act_rec = actual_map.get(act_key) if act_key else None

            if act_rec is None:
                mismatches.append(
                    {
                        "no": count_no,
                        "key": key,
                        "column": "ALL_FIELDS",
                        "expected": "DATA AVAILABLE",
                        "actual": "MISSING IN TARGET",
                        "issue": "MISSING_RECORD",
                    }
                )
                count_no += 1
            else:
                for col in valid_columns:
                    exp_val = exp_rec.get(col, "")
                    act_val = act_rec.get(col, "")
                    if not cls.is_value_matching(value_mappings, col, exp_val, act_val):
                        mismatches.append(
                            {
                                "no": count_no,
                                "key": key,
                                "column": col,
                                "expected": exp_val,
                                "actual": act_val,
                                "issue": "VARIANCE",
                            }
                        )
                        count_no += 1

        return mismatches