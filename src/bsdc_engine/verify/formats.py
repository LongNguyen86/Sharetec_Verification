from typing import Any, Dict, List


class FormatValidator:

    @staticmethod
    def validate_field_formats(
        data_map: Dict[str, Dict[str, str]], max_desc_length: int = 25
    ) -> List[Dict[str, Any]]:
        """Validate field constraints and format rules (e.g. Sharetec character length limits)."""
        format_issues = []
        count_no = 1

        for key, record in data_map.items():
            for col_name, val in record.items():
                if not val:
                    continue
                # Validate description character length constraints
                if col_name.lower() in [
                    "dp.dp-desc",
                    "dp-desc",
                    "description",
                    "tran-desc",
                ]:
                    if len(val) > max_desc_length:
                        format_issues.append(
                            {
                                "no": count_no,
                                "key": key,
                                "column": col_name,
                                "expected": f"Length <= {max_desc_length}",
                                "actual": f"Length = {len(val)} ('{val}')",
                                "issue": "FORMAT_EXCEEDED_LENGTH",
                            }
                        )
                        count_no += 1

        return format_issues