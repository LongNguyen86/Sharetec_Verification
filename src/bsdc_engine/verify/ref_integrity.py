from typing import Any, Dict, List, Set


class ReferentialIntegrityChecker:

    @staticmethod
    def check_parent_key_existence(
        child_map: Dict[str, Dict[str, str]],
        foreign_key_col: str,
        parent_keys: Set[str],
    ) -> List[Dict[str, Any]]:
        """Verify that foreign keys in child table exist within the parent primary key set."""
        ri_issues = []
        count_no = 1

        for key, record in child_map.items():
            fk_val = record.get(foreign_key_col, "").strip()
            if fk_val and fk_val.lower() not in parent_keys:
                ri_issues.append(
                    {
                        "no": count_no,
                        "key": key,
                        "column": foreign_key_col,
                        "expected": "Exists in Parent Table",
                        "actual": f"FK Value '{fk_val}' Not Found",
                        "issue": "ORPHAN_RECORD",
                    }
                )
                count_no += 1

        return ri_issues