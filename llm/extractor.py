"""Generic LLM-backed extraction engine for structured literature review data.

This module configures Gemini, sends the prompt, parses the model response,
and normalizes the output into a flat dictionary. The orchestration, PDF
handling, retries, and CSV persistence are handled by the pipeline module.

The module is fully topic-agnostic: extraction fields and the prompt text
are supplied by the caller (loaded from a versioned criteria file).

Requires: pip install google-genai python-dotenv
"""

from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=FutureWarning)

from google import genai
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_NAME = "gemini-2.5-flash"


class ExtractionError(RuntimeError):
    """Raised when Gemini cannot be queried or the response is not valid JSON."""

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


@dataclass(slots=True)
class ExtractionResult:
    """Normalized model output alongside the raw response text."""

    fields: dict[str, str]
    raw_response: str


def create_client(model_name: str = DEFAULT_MODEL_NAME) -> tuple[genai.Client, str]:
    """Configure Gemini from the environment and return a (client, model_name) tuple."""
    load_dotenv(ROOT / ".env")
    load_dotenv()

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ExtractionError(
            "GOOGLE_API_KEY is not set. "
            "Add it to the environment or the project .env file."
        )

    client = genai.Client(api_key=api_key)
    return client, model_name


def extract_fields(
    client: genai.Client,
    model_name: str,
    markdown_text: str,
    prompt_text: str,
    extraction_fields: list[str],
) -> ExtractionResult:
    """Run Gemini on the supplied Markdown and return normalized extraction fields.

    Args:
        client: Configured Gemini client.
        model_name: Gemini model string to use.
        markdown_text: Full paper body converted to Markdown.
        prompt_text: Complete prompt text from the criteria file.
            The Markdown text is appended automatically.
        extraction_fields: List of field names to extract from the
            JSON response. Fields not present in the response default
            to empty strings.
    """
    prompt = prompt_text + "\n\nMarkdown text:\n" + markdown_text

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    raw_response = _response_text(response)
    parsed = parse_json_response(raw_response, extraction_fields)
    return ExtractionResult(fields=parsed, raw_response=raw_response)


def parse_json_response(
    response_text: str,
    extraction_fields: list[str],
) -> dict[str, str]:
    """Parse a JSON object from the response, keeping only the requested fields.

    Tries three strategies in order:
    1. Parse the full response text as JSON.
    2. Extract a ```json ... ``` fenced block.
    3. Extract the outermost { ... } brace block.
    """
    normalized_text = response_text.strip()
    candidates = [normalized_text]

    fenced_block = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        normalized_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_block:
        candidates.append(fenced_block.group(1).strip())

    brace_block = re.search(r"\{.*\}", normalized_text, flags=re.DOTALL)
    if brace_block:
        candidates.append(brace_block.group(0).strip())

    parsed_object: dict[str, Any] | None = None
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(loaded, dict):
            parsed_object = loaded
            break
        last_error = json.JSONDecodeError("Expected a JSON object.", candidate, 0)

    if parsed_object is None:
        raise ExtractionError(
            f"Gemini response was not valid JSON: {last_error}",
            raw_response=response_text,
        )

    return {
        field: _normalize_value(parsed_object.get(field, ""))
        for field in extraction_fields
    }


def _response_text(response: Any) -> str:
    """Extract response text from the Gemini SDK response object."""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    parts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        candidate_parts = getattr(content, "parts", None) or []
        for part in candidate_parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                parts.append(part_text.strip())

    if parts:
        return "\n".join(parts).strip()

    return str(response).strip()


def _normalize_value(value: Any) -> str:
    """Convert Gemini output into a clean string representation."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()