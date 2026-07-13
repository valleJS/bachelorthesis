"""Full-text screening pipeline for UNSURE-tagged Zotero items.

WORKFLOW OVERVIEW
-----------------
This script sits between the title/abstract screening (zotero_screen_collection.py)
and the full-text extraction pipeline (zotero_extractor.py). It resolves items that
could not be confidently classified from title and abstract alone.

Step-by-step:

1.  FETCH UNSURE ITEMS
    All items tagged UNSURE in the configured collection are fetched via the
    Zotero API.

2.  SKIP CHECK
    Items that already carry a RELEVANT or NOT_RELEVANT tag are skipped —
    they have already been resolved by a previous run of this script.
    The UNSURE tag is intentionally preserved on resolved items as an audit
    trail showing they required full-text screening.

3.  PDF RESOLUTION
    The item's attachment key and filename are retrieved via the Zotero API.
    The script first checks local Zotero storage at
        ~/Zotero/storage/<attachment_key>/<filename>
    and falls back to the legacy VPS flat directory at
        /Volumes/zotero.vgies.de/LibraryNew/<filename>
    for papers that only exist in the old personal collection.

4.  PDF TO MARKDOWN CONVERSION
    The resolved PDF is converted to Markdown via pymupdf4llm (preferred)
    or PyMuPDF as fallback. Reference sections are truncated before screening.

5.  FULL-TEXT SCREENING
    The full Markdown text is sent to Gemini with the same inclusion/exclusion
    criteria from title_abstract_scan.md, applied to the complete paper body
    rather than just the abstract. This provides the information the abstract
    lacked.

6.  TAGGING
    The resolved classification (RELEVANT or NOT_RELEVANT) is added as a new
    tag to the Zotero item. The UNSURE tag is preserved alongside it, making
    items visible in RELEVANT filters while retaining full transparency about
    the screening path.

7.  NOTE
    A child note is added explaining the full-text screening decision and
    referencing the criteria applied, for traceability.

SETUP
-----
    pip install pyzotero google-genai python-dotenv pymupdf4llm

Required .env variables:
    ZOTERO_API_KEY   — your Zotero API key (zotero.org/settings/keys)
    GOOGLE_API_KEY   — your Gemini API key (aistudio.google.com)

Usage:
    python zotero_resolve_unsure.py
    python zotero_resolve_unsure.py --dry-run
    python zotero_resolve_unsure.py --limit 5
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from google import genai
from dotenv import load_dotenv
from pyzotero import zotero

# ---------------------------------------------------------------------------
# Configuration
#
# ZOTERO_LIBRARY_ID:   For personal libraries, use your numeric user ID.
#                      For group libraries, use the group ID.
#                      Group ID is visible in the group URL:
#                        https://www.zotero.org/groups/6546997/...
#                                                      ^^^^^^^
# ZOTERO_LIBRARY_TYPE: "user" for personal libraries, "group" for shared ones.
#
# COLLECTION_KEY:      Find it in the collection URL on zotero.org:
#                        https://www.zotero.org/groups/6546997/.../collections/FPX2TVBY
#                                                                               ^^^^^^^^
# ---------------------------------------------------------------------------
ZOTERO_LIBRARY_ID   = "6546997"   # <-- replace with your library/group ID
ZOTERO_LIBRARY_TYPE = "group"     # <-- "user" or "group"
COLLECTION_KEY      = "FPX2TVBY"  # <-- replace with your collection key

# PDF resolution uses two locations in order:
# 1. Local Zotero storage (new shared collection — stored files)
#    ~/Zotero/storage/<attachment_key>/<filename>
# 2. VPS flat directory (old personal collection — linked files, fallback)
#    PDF_LEGACY_DIR/<filename>
PDF_LOCAL_STORAGE = Path.home() / "Zotero" / "storage"
PDF_LEGACY_DIR    = Path("/Volumes/zotero.vgies.de/LibraryNew")

# Criteria file — same as used in title/abstract screening.
CRITERIA_PATH = Path("prompts/title_abstract_scan.md")

MODEL_NAME = "gemini-2.5-flash"  # use full Flash for full-text; lite may miss nuance

UNSURE_TAG = "UNSURE"
CLASSIFICATION_TAGS = {"RELEVANT", "NOT_RELEVANT"}

DEFAULT_DELAY = 60 / 15


# ---------------------------------------------------------------------------
# Environment and clients
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    if not env_path.exists():
        for parent in root.parents:
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


def setup_gemini(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def setup_zotero(api_key: str) -> zotero.Zotero:
    return zotero.Zotero(ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE, api_key)


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------

def load_criteria() -> str:
    if not CRITERIA_PATH.exists():
        raise FileNotFoundError(
            f"Criteria file not found: {CRITERIA_PATH}. "
            "Ensure prompts/title_abstract_scan.md exists relative to the script."
        )
    return CRITERIA_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Zotero helpers
# ---------------------------------------------------------------------------

def fetch_unsure_items(zot: zotero.Zotero) -> list[dict]:
    """Fetch all items tagged UNSURE from the target collection."""
    return zot.everything(
        zot.collection_items_top(COLLECTION_KEY, tag=UNSURE_TAG)
    )


def get_existing_tags(item: dict) -> set[str]:
    return {t["tag"] for t in item.get("data", {}).get("tags", [])}


def is_already_resolved(item: dict) -> bool:
    """Return True if item already has a RELEVANT or NOT_RELEVANT tag."""
    return bool(get_existing_tags(item) & CLASSIFICATION_TAGS)


def get_attachment_info(zot: zotero.Zotero, item_key: str) -> tuple[str, str] | None:
    """Fetch children and return (attachment_key, filename) for the PDF, or None."""
    children = zot.children(item_key)
    for child in children:
        data = child.get("data", {})
        if data.get("itemType") != "attachment":
            continue
        filename = data.get("filename", "")
        if filename.lower().endswith(".pdf"):
            attachment_key = data.get("key", "")
            return attachment_key, filename
    return None


def add_tag(zot: zotero.Zotero, item: dict, tag: str) -> None:
    """Append a tag to an existing Zotero item without removing existing tags."""
    existing = item.get("data", {}).get("tags", [])
    if any(t["tag"] == tag for t in existing):
        return
    updated = dict(item["data"])
    updated["tags"] = existing + [{"tag": tag}]
    zot.update_item({**item, "data": updated})


def add_note(zot: zotero.Zotero, parent_key: str, reasoning: str) -> None:
    """Create a child note on the Zotero item with the screening reasoning."""
    html_content = f"<p><strong>Full-text screening result:</strong></p><p>{reasoning}</p>"
    template = zot.item_template("note")
    template["note"] = html_content
    template["parentItem"] = parent_key
    zot.create_items([template])


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def resolve_pdf(attachment_key: str, filename: str) -> Path:
    """Resolve PDF path, checking local Zotero storage first, then legacy VPS dir."""
    # 1. local Zotero storage (new shared collection)
    local_path = PDF_LOCAL_STORAGE / attachment_key / filename
    if local_path.exists():
        return local_path
    # 2. legacy VPS flat directory (old personal collection)
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
        print(f"  [WARN] pymupdf4llm failed for {pdf_path.name}: {exc}")

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
    import re
    cutoff_re = re.compile(
        r"^\s{0,3}#{1,6}\s+(?:\d+(?:\.\d+)*\s*[\-.)]?\s*)?"
        r"(?:references?|bibliography|appendix(?:es)?(?:\s+[a-z0-9ivxlcdm]+)?)\b.*$",
        flags=re.IGNORECASE,
    )
    lines = markdown_text.splitlines()
    for index, line in enumerate(lines):
        if cutoff_re.match(line.strip()):
            return "\n".join(lines[:index]).rstrip()
    return markdown_text.strip()


# ---------------------------------------------------------------------------
# Gemini screening
# ---------------------------------------------------------------------------

def analyze_full_text(
    client: genai.Client,
    title: str,
    full_text: str,
    criteria: str,
) -> tuple[str, str]:
    """
    Screen a paper against inclusion/exclusion criteria using full text.

    Returns:
        (classification, reasoning)
        classification: RELEVANT or NOT_RELEVANT only (not UNSURE —
            full text provides sufficient information for a determination)
        reasoning: the model's full explanation
    """
    prompt = f"""
{criteria}

You are screening the FULL TEXT of a paper, not just its abstract. This paper
was previously marked UNSURE because its title and abstract did not contain
sufficient information to make a confident determination. The full text is now
available, so a definitive classification of RELEVANT or NOT_RELEVANT is required.
Do NOT return UNSURE — the full text must be sufficient for a determination.

Article Title: {title}

Full Text (Markdown):
{full_text}

Apply the four-step screening process from the instructions above.
Respond with exactly one of these two labels on the first line:
- RELEVANT
- NOT_RELEVANT

Then provide your reasoning in 3-4 sentences on the following lines, referencing
the specific inclusion or exclusion criteria that apply.
"""
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )
    response_text = response.text.strip()
    first_line = response_text.splitlines()[0].upper()

    if "NOT_RELEVANT" in first_line:
        classification = "NOT_RELEVANT"
    else:
        classification = "RELEVANT"

    return classification, response_text


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_unsure_items(
    zot: zotero.Zotero,
    client: genai.Client,
    criteria: str,
    delay_seconds: float,
    limit: int | None,
    dry_run: bool,
) -> None:
    print(f"Fetching UNSURE items from collection {COLLECTION_KEY}...")
    items = fetch_unsure_items(zot)
    print(f"Found {len(items)} UNSURE items.")

    processed = 0

    for item in items:
        if limit is not None and processed >= limit:
            print(f"Reached processing limit ({limit}). Stopping.")
            break

        data = item.get("data", {})
        item_key = data.get("key", "")
        title = data.get("title", "").strip()
        item_type = data.get("itemType", "")

        if item_type in {"note", "attachment"}:
            continue

        print(f"\n[ITEM] {title[:80]}")

        # skip if already resolved by a previous run
        if is_already_resolved(item):
            print(f"  [SKIP] Already resolved.")
            continue

        # get PDF filename
        attachment_info = get_attachment_info(zot, item_key)
        if not attachment_info:
            print(f"  [SKIP] No PDF attachment found.")
            continue
        attachment_key, filename = attachment_info

        # resolve and convert PDF
        try:
            pdf_path = resolve_pdf(attachment_key, filename)
            markdown_text = pdf_to_markdown(pdf_path)
            cleaned_markdown = truncate_references(markdown_text)
            if not cleaned_markdown.strip():
                raise RuntimeError("PDF conversion produced empty Markdown.")
        except Exception as exc:
            print(f"  [ERROR] PDF processing failed: {exc}")
            continue

        # screen full text
        try:
            classification, reasoning = analyze_full_text(
                client, title, cleaned_markdown, criteria
            )
            print(f"  → {classification}")
        except Exception as exc:
            print(f"  [ERROR] Gemini screening failed: {exc}")
            continue

        # write back to Zotero
        if dry_run:
            print(f"  [DRY RUN] Would add tag '{classification}' and note.")
            print(f"  Reasoning: {reasoning[:150]}...")
        else:
            add_tag(zot, item, classification)
            add_note(zot, item_key, reasoning)
            print(f"  [OK] Tag and note written to Zotero.")

        processed += 1

        if delay_seconds > 0 and (limit is None or processed < limit):
            time.sleep(delay_seconds)

    print(f"\nDone. Resolved {processed} UNSURE items.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve UNSURE-tagged Zotero items via full-text Gemini screening."
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        dest="delay_seconds",
        help=f"Delay between Gemini API calls in seconds (default: {DEFAULT_DELAY:.1f}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of UNSURE items to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing anything back to Zotero.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = load_env()

    client = setup_gemini(env["GOOGLE_API_KEY"])
    zot = setup_zotero(env["ZOTERO_API_KEY"])
    criteria = load_criteria()

    process_unsure_items(
        zot=zot,
        client=client,
        criteria=criteria,
        delay_seconds=args.delay_seconds,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()