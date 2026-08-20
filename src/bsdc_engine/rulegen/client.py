import os
import json
import re
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.bsdc_engine.config import settings
from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.rulegen.prompts import build_batch_prompt

logger = get_logger(__name__)

# Automatically load environment variables from .env file
load_dotenv()


class LLMParserClient:
    """Client for interacting with Google Gemini API with batching and retry logic."""

    def __init__(self, model_name: str = "gemini-3.6-flash", max_retries: int = 5):
        api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or getattr(settings, "gemini_api_key", None)
            or getattr(settings, "google_api_key", None)
        )
        if not api_key:
            logger.warning("GEMINI_API_KEY or GOOGLE_API_KEY not configured in environment or .env file!")
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.model_name = model_name
        self.max_retries = max_retries

    def call_gemini_batch_with_retry(self, prompt: str) -> str:
        """Invoke Gemini API using google-genai SDK with retry logic for 429 and 503 errors."""
        if not self.client:
            raise RuntimeError("Gemini API Client is not initialized due to missing API key!")

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
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
                    logger.warning(
                        f"   ⏳ [Rate Limit 429 Hit] Reached RPM ceiling. Pausing for {wait_time}s "
                        f"(Attempt {attempt}/{self.max_retries})..."
                    )
                    time.sleep(wait_time)
                elif "503" in err_msg or "UNAVAILABLE" in err_msg:
                    wait_time = 8
                    logger.warning(
                        f"   ⏳ [Google Server Overload 503] Temporary high demand. Retrying in {wait_time}s "
                        f"(Attempt {attempt}/{self.max_retries})..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"   ❌ API Error: {e}")
                    time.sleep(5)

        raise RuntimeError("Maximum retries exceeded due to Gemini API errors!")

    def parse_rules_with_llm_batch(self, rules_batch: list[dict]) -> list[dict]:
        """Create a batch prompt for mapping rules and send a single request to Gemini."""
        prompt = build_batch_prompt(rules_batch)
        response_text = self.call_gemini_batch_with_retry(prompt)

        try:
            json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response_text, re.DOTALL)
            clean_json_str = json_match.group(1) if json_match else response_text.strip()
            parsed_results = json.loads(clean_json_str)
            return parsed_results
        except Exception as e:
            logger.error(f"Error parsing JSON from LLM response: {e}")
            return []