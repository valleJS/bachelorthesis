"""Full-text literature extraction pipeline for Zotero libraries.

WORKFLOW OVERVIEW
-----------------
This script queries a Zotero library via API, processes papers tagged
RELEVANT, extracts structured data using Gemini, and writes results to
both a versioned CSV and Zotero notes for safe reruns.

The pipeline is fully topic-agnostic: the extraction fields and prompt
are defined entirely in a versioned criteria file (criteria/vN.md).
Changing your research question is as simple as writing a new criteria
file — no Python edits required.

CRITERIA FILE FORMAT
--------------------
    ---
    version: 1
    description: Initial extraction criteria
    fields:
      - signal_vehicles
      - mediation_moderation_effects
      - results
      - data
      - method
    ---

    [extraction prompt text, including the JSON schema you want back]

The YAML front-matter defines:
  - version:     Integer version number. Determines output CSV name and
                 skip-check note string.
  - description: Human-readable label for this criteria version.
  - fields:      List of JSON keys the LLM should return. These become
                 the extraction columns in the output CSV.

The body after the second --- is the full prompt sent to Gemini, with
the paper's Markdown appended automatically.

STEP-BY-STEP
-------------
1. Load criteria file → version, description, fields, prompt.
2. Fetch all items tagged RELEVANT from the configured Zotero collection.
3. For each item, skip if a Zotero note says "Analyzed under criteria
   version N" or if the item key is already in the output CSV.
4. Resolve the PDF from local Zotero storage or a legacy directory.
5. Convert PDF to Markdown; truncate reference sections.
6. Send Markdown + prompt to Gemini; parse the JSON response using the
   field list from the criteria file.
7. Append the result row to the versioned CSV (extractions_vN.csv).
8. Add a "processed" note to the Zotero item.

VERSIONING
----------
When extraction criteria change, create a new file (criteria/v2.md) with
an incremented version number. The script produces a new CSV and only
processes items without a v2 note — all v1 results stay untouched.

SETUP
-----
    pip install pyzotero google-genai python-dotenv pymupdf4llm

Required .env variables:
    ZOTERO_API_KEY   — your Zotero API key (zotero.org/settings/keys)
    GOOGLE_API_KEY   — your Gemini API key (aistudio.google.com)

Usage:
    python pipeline.py --criteria criteria/v1.md
    python pipeline.py --criteria criteria/v1.md --dry-run
    python pipeline.py --criteria criteria/v1.md --limit 10
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from pyzotero import zotero

ROOT = Path(__file__).resolve().parents[2]  # goes up to src/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm import extractor # PATHS MIGHT NEED UPDATING!!!
from llm.extractor import ExtractionError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZOTERO_LIBRARY_ID   = "6546997"
ZOTERO_LIBRARY_TYPE = "group"
COLLECTION_KEY      = "FPX2TVBY"

PDF_LOCAL_STORAGE = Path.home() / "Zotero" / "storage"
PDF_LEGACY_DIR    = Path("/Volumes/zotero.vgies.de/pdfs")

RELEVANT_TAG = "RELEVANT"
OUTPUT_DIR = ROOT / "output"

DEFAULT_ROW_DELAY = 4.0
DEFAULT_RATE_LIMIT_BACKOFF = 8.0
DEFAULT_MAX_RETRIES = 3

LOGGER = logging.getLogger(__name__)

# Metadata columns added by the pipeline (not from Gemini extraction).
META_FIELDS = (
    "extraction_response",
    "extraction_error",
    "criteria_version",
    "zotero_key",
    "title",
)

# Regex to truncate reference/bibliography/appendix sections from Markdown.
CUTOFF_HEADING_RE = re.compile(
    r"^\s{0,3}#{1,6}\s+(?:\d+(?:\.\d+)*\s*[\-.)]?\s*)?"
    r"(?:references?|bibliography|appendix(?:es)?(?:\s+[a-z0-9ivxlcdm]+)?)\b.*$",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Criteria file parsing
# ---------------------------------------------------------------------------

def load_criteria(criteria_path: Path) -> tuple[int, str, list[str], str]:
    """Parse a versioned criteria file.

    Returns:
        (version, description, fields, prompt_text)
    """
    text = criteria_path.read_text(encoding="utf-8").strip()

    front_matter_match = re.match(
        r"^---\s*\n(.*?)\n---\s*\n(.*)", text, flags=re.DOTALL
    )
    if not front_matter_match:
        raise ValueError(
            f"Criteria file {criteria_path} must start with a YAML front-matter "
            "block delimited by ---."
        )

    header_text = front_matter_match.group(1)
    prompt_text = front_matter_match.group(2).strip()

    # --- version ---
    version_match = re.search(r"^version:\s*(\d+)", header_text, flags=re.MULTILINE)
    if not version_match:
        raise ValueError(f"Criteria file {criteria_path} must contain 'version: N' in the header.")
    version = int(version_match.group(1))

    # --- description ---
    description_match = re.search(r"^description:\s*(.+)", header_text, flags=re.MULTILINE)
    description = description_match.group(1).strip() if description_match else ""

    # --- fields ---
    fields = _parse_fields(header_text)
    if not fields:
        raise ValueError(
            f"Criteria file {criteria_path} must contain a 'fields:' list in the "
            "header, e.g.:\n  fields:\n    - signal_vehicles\n    - results"
        )

    return version, description, fields, prompt_text


def _parse_fields(header_text: str) -> list[str]:
    """Extract the fields list from YAML front-matter without a YAML library.

    Supports the common indented-list format:
        fields:
          - field_one
          - field_two
    """
    # Find the fields: block and collect indented "- item" lines that follow.
    lines = header_text.splitlines()
    fields: list[str] = []
    in_fields_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("fields:"):
            # inline value like "fields: [a, b]" — not supported, use list form
            in_fields_block = True
            continue

        if in_fields_block:
            # A new top-level key ends the block.
            if stripped and not stripped.startswith("-") and ":" in stripped:
                break
            item_match = re.match(r"^\s+-\s+(.+)$", line)
            if item_match:
                fields.append(item_match.group(1).strip())

    return fields


def build_output_fields(extraction_fields: list[str]) -> tuple[str, ...]:
    """Combine extraction fields with pipeline metadata columns."""
    return tuple(extraction_fields) + META_FIELDS


def skip_note_string(version: int) -> str:
    return f"Analyzed under criteria version {version}"


# ---------------------------------------------------------------------------
# Environment and clients
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.exists():
        for parent in ROOT.parents:
            candidate = parent / ".env"
            if candidate.exists():
                env_path = candidate
                break
    load_dotenv(env_path)

    required = ["ZOTERO_API_KEY", "GOOGLE_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return {k: os.environ[k] for k in required}


def setup_zotero(api_key: str) -> zotero.Zotero:
    return zotero.Zotero(ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE, api_key)


# ---------------------------------------------------------------------------
# Zotero helpers
# ---------------------------------------------------------------------------

def fetch_relevant_items(zot: zotero.Zotero) -> list[dict]:
    return zot.everything(
        zot.collection_items_top(COLLECTION_KEY, tag=RELEVANT_TAG)
    )


def fetch_children(zot: zotero.Zotero, item_key: str) -> list[dict]:
    return zot.children(item_key)


def is_already_processed(children: list[dict], version: int) -> bool:
    target = skip_note_string(version)
    for child in children:
        if child.get("data", {}).get("itemType") != "note":
            continue
        note_content = child.get("data", {}).get("note", "")
        plain_text = re.sub(r"<[^>]+>", "", note_content)
        if target in plain_text:
            return True
    return False


def get_attachment_info(children: list[dict]) -> tuple[str, str] | None:
    for child in children:
        data = child.get("data", {})
        if data.get("itemType") != "attachment":
            continue
        filename = data.get("filename", "")
        if filename.lower().endswith(".pdf"):
            return data.get("key", ""), filename
    return None


def add_processed_note(
    zot: zotero.Zotero,
    item_key: str,
    version: int,
    dry_run: bool,
) -> None:
    note_text = skip_note_string(version)
    if dry_run:
        LOGGER.info("  [DRY RUN] Would add note: %s", note_text)
        return
    note = {
        "itemType": "note",
        "parentItem": item_key,
        "note": f"<p>{note_text}</p>",
        "tags": [],
        "collections": [],
        "relations": {},
    }
    zot.create_items([note])


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def resolve_pdf(attachment_key: str, filename: str) -> Path:
    local_path = PDF_LOCAL_STORAGE / attachment_key / filename
    if local_path.exists():
        return local_path
    legacy_path = PDF_LEGACY_DIR / filename
    if legacy_path.exists():
        return legacy_path
    raise FileNotFoundError(
        f"PDF not found in either location:\n"
        f"  Local: {local_path}\n"
        f"  Legacy: {legacy_path}"
    )


def pdf_to_markdown(pdf_path: Path) -> str:
    try:
        import pymupdf4llm  # type: ignore
        markdown_text = pymupdf4llm.to_markdown(str(pdf_path))
        if isinstance(markdown_text, str) and markdown_text.strip():
            return markdown_text
    except Exception as exc:
        LOGGER.warning("pymupdf4llm failed for %s: %s", pdf_path.name, exc)

    try:
        import fitz  # type: ignore
        document = fitz.open(str(pdf_path))
        try:
            pages = [
                document.load_page(i).get_text("text")
                for i in range(document.page_count)
            ]
        finally:
            document.close()
        return "\n\n".join(p.strip() for p in pages if p and p.strip())
    except Exception as exc:
        raise RuntimeError(f"Failed to parse PDF {pdf_path.name}: {exc}") from exc


def truncate_references(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    for index, line in enumerate(lines):
        if CUTOFF_HEADING_RE.match(line.strip()):
            return "\n".join(lines[:index]).rstrip()
    return markdown_text.strip()


# ---------------------------------------------------------------------------
# Extraction with retries
# ---------------------------------------------------------------------------

def _looks_like_rate_limit(message: str) -> bool:
    return any(
        token in message
        for token in ("rate limit", "resource exhausted", "429", "too many requests", "quota", "exceeded")
    )


def extract_with_retry(
    gemini_client: Any,
    model_name: str,
    markdown_text: str,
    prompt_text: str,
    extraction_fields: list[str],
    max_retries: int,
    backoff_seconds: float,
) -> extractor.ExtractionResult:
    """Run extraction with rate-limit-aware exponential backoff retries."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return extractor.extract_fields(
                gemini_client, model_name, markdown_text,
                prompt_text=prompt_text,
                extraction_fields=extraction_fields,
            )
        except ExtractionError as exc:
            last_error = exc
            if _looks_like_rate_limit(str(exc).lower()) and attempt < max_retries - 1:
                sleep_seconds = backoff_seconds * (2 ** attempt)
                LOGGER.warning(
                    "Rate limit detected; sleeping %.1f s before retry %d/%d.",
                    sleep_seconds, attempt + 2, max_retries,
                )
                time.sleep(sleep_seconds)
                continue
            raise
    raise RuntimeError(f"Extraction failed after {max_retries} retries: {last_error}")


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_or_create_csv(output_path: Path, output_fields: tuple[str, ...]) -> pd.DataFrame:
    if output_path.exists():
        return pd.read_csv(output_path)
    return pd.DataFrame(columns=list(output_fields))


def save_csv(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


def is_in_csv(df: pd.DataFrame, item_key: str) -> bool:
    if "zotero_key" not in df.columns or df.empty:
        return False
    return item_key in df["zotero_key"].values


def append_row(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_collection(
    zot: zotero.Zotero,
    gemini_client: Any,
    model_name: str,
    version: int,
    prompt_text: str,
    extraction_fields: list[str],
    output_fields: tuple[str, ...],
    output_path: Path,
    row_delay: float,
    max_retries: int,
    backoff_seconds: float,
    dry_run: bool,
    limit: int | None,
) -> None:
    LOGGER.info("Fetching RELEVANT items from collection %s...", COLLECTION_KEY)
    items = fetch_relevant_items(zot)
    LOGGER.info("Found %d RELEVANT items.", len(items))

    df = load_or_create_csv(output_path, output_fields)
    processed = 0

    for item in items:
        if limit is not None and processed >= limit:
            LOGGER.info("Reached processing limit (%d). Stopping.", limit)
            break

        data = item.get("data", {})
        item_key = data.get("key", "")
        title = data.get("title", "").strip()
        item_type = data.get("itemType", "")

        if item_type in {"note", "attachment"}:
            continue

        LOGGER.info("\n[ITEM] %s", title[:80])

        children = fetch_children(zot, item_key)

        if is_already_processed(children, version):
            LOGGER.info("  [SKIP] Already processed under criteria version %d.", version)
            continue

        if is_in_csv(df, item_key):
            LOGGER.info("  [SKIP] Already in output CSV.")
            continue

        attachment_info = get_attachment_info(children)
        if not attachment_info:
            LOGGER.warning("  [SKIP] No PDF attachment found.")
            continue
        attachment_key, filename = attachment_info

        try:
            pdf_path = resolve_pdf(attachment_key, filename)
        except FileNotFoundError as exc:
            LOGGER.warning("  [SKIP] %s", exc)
            continue

        # convert PDF to Markdown
        try:
            markdown_text = pdf_to_markdown(pdf_path)
            cleaned_markdown = truncate_references(markdown_text)
            if not cleaned_markdown.strip():
                raise RuntimeError("PDF conversion produced empty Markdown.")
        except Exception as exc:
            LOGGER.error("  [ERROR] PDF conversion failed: %s", exc)
            row = {field: "" for field in output_fields}
            row.update({
                "zotero_key": item_key,
                "title": title,
                "criteria_version": version,
                "extraction_error": str(exc),
            })
            df = append_row(df, row)
            save_csv(df, output_path)
            continue

        # run Gemini extraction
        try:
            if dry_run:
                LOGGER.info("  [DRY RUN] Would extract from: %s", pdf_path.name)
                processed += 1
                continue

            result = extract_with_retry(
                gemini_client, model_name, cleaned_markdown,
                prompt_text, extraction_fields,
                max_retries, backoff_seconds,
            )

        except Exception as exc:
            LOGGER.error("  [ERROR] Extraction failed: %s", exc)
            row = {field: "" for field in output_fields}
            row.update({
                "zotero_key": item_key,
                "title": title,
                "criteria_version": version,
                "extraction_error": str(exc),
                "extraction_response": getattr(exc, "raw_response", ""),
            })
            df = append_row(df, row)
            save_csv(df, output_path)
            continue

        # build output row
        row = {field: "" for field in output_fields}
        row.update(result.fields)
        row.update({
            "zotero_key": item_key,
            "title": title,
            "criteria_version": version,
            "extraction_response": result.raw_response,
            "extraction_error": "",
        })

        df = append_row(df, row)
        save_csv(df, output_path)
        LOGGER.info("  [OK] Extracted and saved.")

        add_processed_note(zot, item_key, version, dry_run)

        processed += 1

        if row_delay > 0:
            time.sleep(row_delay)

    LOGGER.info("\nDone. Processed %d items. Output: %s", processed, output_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract structured fields from Zotero RELEVANT papers."
    )
    parser.add_argument(
        "--criteria", type=Path, required=True,
        help="Path to the versioned criteria file, e.g. criteria/v1.md",
    )
    parser.add_argument(
        "--row-delay", type=float, default=DEFAULT_ROW_DELAY,
        help=f"Delay between Gemini API calls in seconds (default: {DEFAULT_ROW_DELAY}).",
    )
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--backoff-seconds", type=float, default=DEFAULT_RATE_LIMIT_BACKOFF)
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of items to process.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to CSV or Zotero.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main() -> None:
    configure_logging()
    args = parse_args()

    # load criteria — now returns fields list too
    version, description, extraction_fields, prompt_text = load_criteria(args.criteria)
    LOGGER.info("Criteria version %d: %s", version, description)
    LOGGER.info("Extraction fields: %s", extraction_fields)

    output_fields = build_output_fields(extraction_fields)
    output_path = OUTPUT_DIR / f"extractions_v{version}.csv"
    LOGGER.info("Output CSV: %s", output_path)

    env = load_env()
    gemini_client, model_name = extractor.create_client()
    zot = setup_zotero(env["ZOTERO_API_KEY"])

    process_collection(
        zot=zot,
        gemini_client=gemini_client,
        model_name=model_name,
        version=version,
        prompt_text=prompt_text,
        extraction_fields=extraction_fields,
        output_fields=output_fields,
        output_path=output_path,
        row_delay=args.row_delay,
        max_retries=args.max_retries,
        backoff_seconds=args.backoff_seconds,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()