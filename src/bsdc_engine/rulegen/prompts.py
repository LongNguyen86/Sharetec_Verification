"""Prompt templates for LLM Rule Drafting."""

import json

BATCH_SYSTEM_PROMPT = """You are an expert Banking Migration DSL Parser.
Analyze the following list of raw mapping notes (raw_notes) and convert them into standard JSON format.

OUTPUT RULES:
Return ONLY a single JSON array (Array of Objects), where each object contains:
- "id": (keep the original input ID)
- "rule_type": ("DIRECT" | "CONDITIONAL" | "CONSTANT" | "NO_MAPPING")
- "dsl_json": (Object containing detailed action structure)
- "dsl_readable": (Short human-readable description for QA review, e.g., "IF COL_AL=='INSTITUCION' THEN LEAVE BLANK ELSE ASSIGN COL_D")

STRICT REQUIREMENT: Return ONLY pure JSON array format inside ```json ... ``` codeblock, DO NOT add any intro or explanation.
"""


def build_batch_prompt(rules_batch: list[dict]) -> str:
    """Construct batch prompt containing JSON payload of unparsed rules."""
    return f"""{BATCH_SYSTEM_PROMPT}

INPUT PARSE LIST:
{json.dumps(rules_batch, ensure_ascii=False, indent=2)}
"""