import re


def clean_excel_text(val: str | None) -> str:
    """Clean Excel cell values: strip whitespaces, tabs, newlines."""
    if val is None:
        return ""
    cleaned = str(val).replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    return re.sub(r"\s+", " ", cleaned)


def clean_sharepoint_path(raw_path: str) -> str:
    """Clean SharePoint server relative path strings."""
    if not raw_path:
        return ""
    cleaned = raw_path.replace("\n", "").replace("\r", "").replace("\t", "").strip()
    cleaned = cleaned.strip('"').strip("'")
    parts = [part.strip() for part in cleaned.split("/") if part.strip()]
    return "/".join(parts)


def col_letter_to_index(col_str: str) -> int:
    """Convert Excel column letter (e.g., 'A', 'Z', 'AA') to 0-based index."""
    col_str = col_str.upper().strip()
    exp = 0
    idx = 0
    for char in reversed(col_str):
        idx += (ord(char) - ord("A") + 1) * (26**exp)
        exp += 1
    return idx - 1


def index_to_col_letter(idx: int) -> str:
    """Convert 0-based index to Excel column letter (0 -> 'A', 25 -> 'Z', 26 -> 'AA')."""
    result = ""
    idx += 1
    while idx > 0:
        idx, remainder = divmod(idx - 1, 26)
        result = chr(65 + remainder) + result
    return result