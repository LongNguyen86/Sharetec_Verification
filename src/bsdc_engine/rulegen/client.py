import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.bsdc_engine.config import settings
from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.rulegen.prompts import build_batch_prompt

logger = get_logger(__name__)

load_dotenv()


class LLMParserClient:
    """Client for interacting with Google Gemini API without retries."""

    def __init__(self, model_name: str = "gemini-3.6-flash"):
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

    def call_gemini_batch(self, prompt: str) -> str:
        """Invoke Gemini API directly in a single request without retry attempts."""
        if not self.client:
            raise RuntimeError("Gemini API Client is not initialized due to missing API key!")

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
            logger.error(f"Gemini API execution error: {e}")
            raise e

    def call_gemini_batch_with_retry(self, prompt: str) -> str:
        """Alias for backward compatibility, executes once without retries."""
        return self.call_gemini_batch(prompt)

    def parse_rules_with_llm_batch(self, rules_batch: list[dict]) -> list[dict]:
        """Create a batch prompt for mapping rules and send a single request to Gemini."""
        prompt = build_batch_prompt(rules_batch)
        response_text = self.call_gemini_batch(prompt)

        try:
            start_idx = response_text.find("[")
            end_idx = response_text.rfind("]")
            if start_idx != -1 and end_idx != -1:
                clean_json_str = response_text[start_idx : end_idx + 1]
            else:
                clean_json_str = response_text.strip()
            parsed_results = json.loads(clean_json_str)
            return parsed_results
        except Exception as e:
            logger.error(f"Error parsing JSON from LLM response: {e}")
            return []