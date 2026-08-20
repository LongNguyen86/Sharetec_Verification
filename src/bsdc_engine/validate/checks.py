"""Individual Preflight Validation Rules for Excel Mapping Files."""


def check_missing_data_file(
    row_idx: int, field_val: str, column_val: str, effective_data_file: str
) -> str | None:
    """Rule 1: Column declared but missing Data File on this or above rows."""
    if column_val and not effective_data_file:
        return (
            f"Row {row_idx:04d} | Field '{field_val}': Has Column='{column_val}' "
            "but Data File not declared on this or above rows!"
        )
    return None


def check_converted_missing_mapping(
    row_idx: int,
    field_val: str,
    is_converted: bool,
    has_source_mapping: bool,
    has_notes: bool,
) -> str | None:
    """Rule 2: Converted = Yes but missing both Mapping (Data File/Column) and Notes."""
    if is_converted and not has_source_mapping and not has_notes:
        return (
            f"Row {row_idx:04d} | Field '{field_val}': Marked Converted='Yes' "
            "but MISSING Mapping (Data File/Column) and Notes!"
        )
    return None


def check_empty_sheet(has_header: bool, mapped_field_count: int) -> str | None:
    """Rule 3: Entire Sheet left blank without N/A in Notes column."""
    if has_header and mapped_field_count == 0:
        return (
            "Entire Sheet is left blank (No Fields mapped). 💡 Solution: "
            "If Credit Union DOES NOT USE this module, type 'N/A' in Notes "
            "column of any row to confirm!"
        )
    return None