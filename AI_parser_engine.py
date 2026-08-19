import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# Load environment variables from .env file automatically
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Import official new Google GenAI SDK
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ 'google-genai' library is not installed. Please run: pip install google-genai python-dotenv")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "workspace" / "rules.db"

# Configure API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not GEMINI_API_KEY:
    print("⚠️ WARNING: GEMINI_API_KEY or GOOGLE_API_KEY not found in environment or .env file!")

# Initialize Google GenAI Client
client = genai.Client(api_key=GEMINI_API_KEY)

# Model & Execution Parameters
MODEL_NAME = "gemini-3.6-flash"  # Recommended fast model
BATCH_SIZE = 50                  # Group 25 fields into a single Prompt (reduces total batches to ~8)
DELAY_BETWEEN_BATCHES = 14      # Delay (seconds) between batches to respect 5 RPM limit
MAX_RETRIES = 5                  # Maximum retry attempts on Rate Limit (429) or Server Overload (503)


def call_gemini_batch_with_retry(prompt: str) -> str:
    """Invoke Gemini API using google-genai SDK with retry logic for 429 and 503 errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                ),
            )
            return response.text
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                wait_time = 20
                print(f"   ⏳ [Rate Limit 429 Hit] Reached 5 RPM ceiling. Pausing for {wait_time}s (Attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait_time)
            elif "503" in err_msg or "UNAVAILABLE" in err_msg:
                wait_time = 8
                print(f"   ⏳ [Google Server Overload 503] Temporary high demand. Retrying in {wait_time}s (Attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait_time)
            else:
                print(f"   ❌ API Error: {e}")
                time.sleep(5)

    raise RuntimeError("❌ Maximum retries exceeded due to API errors!")


def parse_rules_with_llm_batch(rules_batch: list[dict]) -> list[dict]:
    """Create a batch Prompt for mapping rules and send a single request to Gemini."""
    prompt = f"""You are an expert Banking Migration DSL Parser.
Analyze the following list of raw mapping notes (raw_notes) and convert them into standard JSON format.

INPUT PARSE LIST:
{json.dumps(rules_batch, ensure_ascii=False, indent=2)}

OUTPUT RULES:
Return ONLY a single JSON array (Array of Objects), where each object contains:
- "id": (keep the original input ID)
- "rule_type": ("DIRECT" | "CONDITIONAL" | "CONSTANT" | "NO_MAPPING")
- "dsl_json": (Object containing detailed action structure)
- "dsl_readable": (Short human-readable description for QA review, e.g., "IF COL_AL=='INSTITUCION' THEN LEAVE BLANK ELSE ASSIGN COL_D")

STRICT REQUIREMENT: Return ONLY pure JSON array format inside ```json ... ``` codeblock, DO NOT add any intro or explanation.
"""

    response_text = call_gemini_batch_with_retry(prompt)

    # Extract JSON from LLM response
    try:
        json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response_text, re.DOTALL)
        clean_json_str = json_match.group(1) if json_match else response_text.strip()
        parsed_results = json.loads(clean_json_str)
        return parsed_results
    except Exception as e:
        print(f"   ⚠️ Error parsing JSON from LLM response: {e}")
        return []


def process_unparsed_rules(cu_id: str = None):
    """Retrieve unparsed rules from SQLite and invoke LLM in batches."""
    if not DB_PATH.exists():
        print(f"❌ Database not found at {DB_PATH}. Please run rule_engine.py first!")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # Retrieve list of rules needing LLM processing
        query = """
            SELECT id, target_field, data_file, column_letter, raw_notes 
            FROM rule_store 
            WHERE (status IN ('NEEDS_REVIEW', 'UNPARSED') OR dsl_readable = 'NEEDS_LLM_PARSING')
        """
        params = []
        if cu_id:
            query += " AND (cu_id = ? OR is_global = 1)"
            params.append(cu_id)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            print("✅ All rules parsed successfully. No rules left requiring LLM processing!")
            return

        print(f"🤖 Found {len(rows)} rules needing AI/LLM decoding...")

        items = []
        for r_id, field, file_name, col, notes in rows:
            items.append({
                "id": r_id,
                "field": field,
                "file": file_name,
                "col": col,
                "raw_notes": notes
            })

        # Split list into batches of 25 rules
        total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_idx in range(total_batches):
            batch_items = items[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
            print(f"\n🧠 [Batch {batch_idx + 1}/{total_batches}] Sending {len(batch_items)} rules to Gemini AI...")

            parsed_list = parse_rules_with_llm_batch(batch_items)

            if parsed_list:
                for item in parsed_list:
                    rule_id = item.get("id")
                    r_type = item.get("rule_type", "CONDITIONAL")
                    d_json = item.get("dsl_json", {})
                    d_readable = item.get("dsl_readable", "")

                    cursor.execute("""
                        UPDATE rule_store 
                        SET rule_type = ?, dsl_json = ?, dsl_readable = ?, status = 'PROVISIONAL_NEEDS_REVIEW', parsed_by = 'LLM_GEMINI'
                        WHERE id = ?
                    """, (r_type, json.dumps(d_json, ensure_ascii=False), d_readable, rule_id))

                conn.commit()
                print(f"   ✅ Successfully updated Batch {batch_idx + 1} into Database!")
            else:
                print(f"   ⚠️ Batch {batch_idx + 1} returned empty response.")

            # Delay between batches to respect 5 RPM limit
            if batch_idx < total_batches - 1:
                print(f"   💤 Sleeping for {DELAY_BETWEEN_BATCHES}s to respect Rate Limit (5 RPM)...")
                time.sleep(DELAY_BETWEEN_BATCHES)

        print("\n🎉 COMPLETED! All rules processed by AI and updated in Database.")


if __name__ == "__main__":
    target_cu = sys.argv[1].upper() if len(sys.argv) > 1 else None
    process_unparsed_rules(cu_id=target_cu)