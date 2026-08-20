"""Generate static preview representations for rule review."""


def generate_rule_preview(raw_notes: str, dsl_readable: str, rule_type: str) -> str:
    """Format expected preview representation for Excel report or QA logs."""
    if not raw_notes:
        return dsl_readable
    return f"[{rule_type}] {raw_notes} ===> {dsl_readable}"