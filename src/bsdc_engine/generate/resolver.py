import re


def col_letter_to_index(letter: str) -> int:
    """Convert Excel column letters (A, B, C...) to 0-based index."""
    if not letter:
        return -1
    words = re.findall(r"[A-Za-z]+", str(letter))
    if not words:
        return -1
    clean_letter = words[-1].upper()
    result = 0
    for char in clean_letter:
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def resolve_column_name(
    data_file: str, col_letter: str, default_table: str, available_cols: list
) -> str | None:
    """Resolve exact DataFrame column name strictly preventing cross-table leaks."""
    data_file_clean = (
        data_file.strip().upper().replace(" ", "_")
        if data_file and str(data_file).strip().upper() not in ["", "N/A", "NAN", "NONE"]
        else None
    )
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