"""Screen a Zotero collection using Gemini and write results back via the Zotero API.

For each item in the target collection, the script:
1. Skips items that already carry a classification tag (RELEVANT, NOT_RELEVANT, UNSURE).
2. Sends the title and abstract to Gemini for inclusion/exclusion screening.
3. Adds the classification tag to the Zotero item.
4. Creates a child note on the Zotero item with the full Gemini reasoning.

Setup:
    pip install pyzotero google-genai python-dotenv

Required .env variables:
    ZOTERO_API_KEY   — your Zotero API key (zotero.org/settings/keys)
    GOOGLE_API_KEY   — your Gemini API key (aistudio.google.com)

Usage:
    python zotero_screen_collection.py [--delay SECONDS] [--limit N] [--dry-run]
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

CRITERIA_PATH = Path("prompts/title_abstract_scan.md")
MODEL_NAME = "gemini-2.5-flash-lite"  # cheapest sufficient model for screening
DEFAULT_DELAY = 60 / 15  # 15 requests per minute

CLASSIFICATION_TAGS = {"RELEVANT", "NOT_RELEVANT", "UNSURE"}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    if not env_path.exists():
        # walk up to find a .env in a parent directory
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


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def setup_gemini(api_key: str) -> genai.Client:
    """Create and return a Gemini client using the new google-genai SDK."""
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

def fetch_collection_items(zot: zotero.Zotero) -> list[dict]:
    """Fetch all top-level items from the target collection (auto-paginated)."""
    return zot.everything(zot.collection_items_top(COLLECTION_KEY))


def get_existing_tags(item: dict) -> set[str]:
    """Return the set of tag strings already on an item."""
    return {t["tag"] for t in item.get("data", {}).get("tags", [])}


def has_classification_tag(item: dict) -> bool:
    """Return True if the item already carries any classification tag."""
    return bool(get_existing_tags(item) & CLASSIFICATION_TAGS)


def add_tag(zot: zotero.Zotero, item: dict, tag: str) -> None:
    """Append a classification tag to an existing Zotero item."""
    existing = item.get("data", {}).get("tags", [])
    # avoid duplicates
    if any(t["tag"] == tag for t in existing):
        return
    updated = dict(item["data"])
    updated["tags"] = existing + [{"tag": tag}]
    zot.update_item({**item, "data": updated})


def add_note(zot: zotero.Zotero, parent_key: str, reasoning: str) -> None:
    """Create a child note item attached to the parent item."""
    # Zotero notes are stored as HTML; wrap plain text in a paragraph.
    html_content = f"<p>{reasoning}</p>"
    template = zot.item_template("note")
    template["note"] = html_content
    template["parentItem"] = parent_key
    zot.create_items([template])


# ---------------------------------------------------------------------------
# Gemini screening
# ---------------------------------------------------------------------------

def analyze_paper(
    client: genai.Client,
    title: str,
    abstract: str,
    criteria: str,
) -> tuple[str, str]:
    """
    Screen a paper against the inclusion/exclusion criteria.

    Returns:
        (classification, reasoning)
        classification: one of "RELEVANT", "NOT_RELEVANT", "UNSURE"
        reasoning: the model's full explanation
    """
    prompt = f"""
{criteria}

Please analyze the following paper:

Article Title: {title}

Abstract: {abstract}

Based on the inclusion and exclusion criteria provided, determine:
1. Can you make a clear determination about this paper?
2. If yes, does it meet the inclusion criteria?

Respond with exactly one of these three labels on the first line:
- RELEVANT
- NOT_RELEVANT
- UNSURE

Then provide your reasoning in 2-3 sentences on the following lines,
referencing the specific inclusion or exclusion criterion that applies.
"""
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )
    response_text = response.text.strip()

    first_line = response_text.splitlines()[0].upper()

    if "NOT_RELEVANT" in first_line:
        classification = "NOT_RELEVANT"
    elif "RELEVANT" in first_line:
        classification = "RELEVANT"
    else:
        classification = "UNSURE"

    return classification, response_text


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_collection(
    zot: zotero.Zotero,
    client: genai.Client,
    criteria: str,
    delay_seconds: float,
    limit: int | None,
    dry_run: bool,
) -> None:
    print(f"Fetching items from collection {COLLECTION_KEY}...")
    items = fetch_collection_items(zot)
    print(f"Found {len(items)} items.")

    processed = 0

    for item in items:
        if limit is not None and processed >= limit:
            print(f"Reached processing limit ({limit}). Stopping.")
            break

        data = item.get("data", {})
        item_key = data.get("key", "")
        title = data.get("title", "").strip()
        abstract = data.get("abstractNote", "").strip()
        item_type = data.get("itemType", "")

        # skip non-paper items (notes, attachments, etc.)
        if item_type in {"note", "attachment"}:
            continue

        # skip already-classified items
        if has_classification_tag(item):
            print(f"[SKIP] Already classified: {title[:70]}")
            continue

        if not title and not abstract:
            print(f"[SKIP] No title or abstract for item {item_key}.")
            continue

        print(f"\n[PROCESSING] {title[:70]}...")

        try:
            classification, reasoning = analyze_paper(
                client, title, abstract, criteria
            )
            print(f"  → {classification}")

            if dry_run:
                print(f"  [DRY RUN] Would add tag '{classification}' and note.")
                print(f"  Reasoning: {reasoning[:120]}...")
            else:
                add_tag(zot, item, classification)
                add_note(zot, item_key, reasoning)

            processed += 1

        except Exception as exc:
            print(f"  [ERROR] {item_key}: {exc}")
            continue

        if delay_seconds > 0 and (limit is None or processed < limit):
            time.sleep(delay_seconds)

    print(f"\nDone. Processed {processed} items.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Screen a Zotero collection using Gemini and write results back via the Zotero API."
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
        help="Maximum number of unclassified items to process.",
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

    process_collection(
        zot=zot,
        client=client,
        criteria=criteria,
        delay_seconds=args.delay_seconds,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()