from typing import Any, Dict, List


class AggregateChecker:

    @staticmethod
    def check_control_totals(
        expected_map: Dict[str, Dict[str, str]],
        actual_map: Dict[str, Dict[str, str]],
        numeric_columns: List[str],
    ) -> List[Dict[str, Any]]:
        """Calculate and compare row counts and numeric control total sums between expected and actual data."""
        issues = []
        count_no = 1

        # 1. Total Row Count Reconciliation
        exp_count = len(expected_map)
        act_count = len(actual_map)
        if exp_count != act_count:
            issues.append(
                {
                    "no": count_no,
                    "key": "AGGREGATE",
                    "column": "ROW_COUNT",
                    "expected": str(exp_count),
                    "actual": str(act_count),
                    "issue": "ROW_COUNT_MISMATCH",
                }
            )
            count_no += 1

        # 2. Control Total Sum Reconciliation for numeric columns
        for col in numeric_columns:
            sum_exp = 0.0
            sum_act = 0.0

            for rec in expected_map.values():
                try:
                    sum_exp += float(rec.get(col, "0").replace(",", ""))
                except ValueError:
                    pass

            for rec in actual_map.values():
                try:
                    sum_act += float(rec.get(col, "0").replace(",", ""))
                except ValueError:
                    pass

            if abs(sum_exp - sum_act) >= 1e-2:
                issues.append(
                    {
                        "no": count_no,
                        "key": "AGGREGATE",
                        "column": f"SUM({col})",
                        "expected": f"{sum_exp:,.2f}",
                        "actual": f"{sum_act:,.2f}",
                        "issue": "CONTROL_TOTAL_VARIANCE",
                    }
                )
                count_no += 1

        return issues