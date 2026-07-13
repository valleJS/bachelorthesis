"""LLM-assisted normalization script for signaling-theory extraction CSV.

WORKFLOW
--------
Step 1 — Generate suggestions:
    python normalize_llm.py --input ../output/extractions_v3_repaired.csv

    Reads unique values per controlled field, sends them to Gemini with
    coarse-mapping instructions, writes normalization_suggestions.md.

Step 2 — Review normalization_suggestions.md (edit if needed).

Step 3 — Apply:
    python normalize_llm.py --input ../output/extractions_v3_repaired.csv --apply

    Applies suggestions, derives industry_sector_broad column,
    writes extractions_v3_normalized.csv and normalization_log.md.

Install:
    pip install pandas google-genai python-dotenv
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google import genai

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV_DEFAULT = ROOT / "output" / "extractions_v3_repaired.csv"
OUTPUT_CSV = ROOT / "output" / "extractions_v3_normalized.csv"
SUGGESTIONS_MD = Path(__file__).parent / "normalization_suggestions.md"
LOG_MD = Path(__file__).parent / "normalization_log.md"

MODEL_NAME = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Canonical picklists — Gemini must map to these
# ---------------------------------------------------------------------------

SIGNAL_VEHICLE_CANONICAL = [
    "patent", "source_code", "prototype", "whitepaper",
    "founder_experience", "founder_education", "team_composition",
    "research_alliance", "blockchain_development", "certification",
    "trademark", "r&d_investment", "social_capital", "media_coverage",
    "government_grant", "vc_backing", "accelerator_affiliation",
]

SIGNAL_VEHICLE_CATEGORY_CANONICAL = [
    "appropriability", "feasibility", "human_capital", "other",
]

FINANCING_CONTEXT_CANONICAL = [
    "VC", "angel", "equity_crowdfunding", "reward_crowdfunding",
    "ICO", "IPO", "STO", "other",
]

FINANCING_STAGE_CANONICAL = [
    "seed", "first_round", "second_round", "early_stage",
    "growth", "time_period",
]

INDUSTRY_SECTOR_CANONICAL = [
    "biotech", "software", "blockchain", "deep_tech", "health",
    "energy", "finance", "manufacturing", "telecommunications",
    "consumer", "services", "transportation", "agriculture",
    "general",
]

EFFECTIVENESS_CANONICAL = [
    "effective", "ineffective", "mixed",
]

# ---------------------------------------------------------------------------
# Field configurations: field → (canonical list, prompt instruction)
# ---------------------------------------------------------------------------

FIELDS_TO_NORMALIZE: dict[str, tuple[list[str], str]] = {
    "signal_vehicles": (
        SIGNAL_VEHICLE_CANONICAL,
        "Map each raw value to the CLOSEST canonical form from the list. "
        "Collapse all patent variants (application, granted, pending, etc.) "
        "to 'patent'. Collapse all source code variants to 'source_code'. "
        "Collapse all prototype/proof-of-concept variants to 'prototype'. "
        "Collapse all founder experience variants to 'founder_experience'. "
        "Collapse all whitepaper variants to 'whitepaper'. "
        "If a value clearly does not fit any canonical form, map it to the "
        "single most thematically similar one. Do not create new canonical "
        "forms. Every value must map to exactly one item from the list.",
    ),
    "signal_vehicle_category": (
        SIGNAL_VEHICLE_CATEGORY_CANONICAL,
        "Map each raw value to exactly one of: appropriability, feasibility, "
        "human_capital, other. Strip any other(...) wrapper and map to 'other'. "
        "Every value must map to exactly one item from the list.",
    ),
    "financing_context": (
        FINANCING_CONTEXT_CANONICAL,
        "Map each raw value to exactly one of the canonical financing mechanisms. "
        "Corporate VC maps to 'VC'. All crowdfunding variants that are not "
        "equity-based map to 'other'. Every value must map to exactly one item.",
    ),
    "financing_stage": (
        FINANCING_STAGE_CANONICAL,
        "Map each raw value to the closest canonical stage. "
        "Any year range (e.g. 2014-2020) maps to 'time_period'. "
        "All later/follow-on/growth rounds map to 'growth'. "
        "Series A maps to 'first_round'. Series B and beyond map to 'growth'. "
        "Every value must map to exactly one item from the list.",
    ),
    "industry_sector": (
        INDUSTRY_SECTOR_CANONICAL,
        "Map each raw value to the single broadest matching canonical sector. "
        "Do not combine sectors. Do not add specifics. "
        "Everything AI/ML/software maps to 'software'. "
        "Everything biotech/pharma/health maps to either 'biotech' or 'health'. "
        "Everything blockchain/crypto/ICO maps to 'blockchain'. "
        "Everything hardware/deeptech/nanotech maps to 'deep_tech'. "
        "If genuinely cross-sector or unspecific, map to 'general'. "
        "Every value must map to exactly one item from the list.",
    ),
    "signal_effectiveness_direction": (
        EFFECTIVENESS_CANONICAL,
        "Map each raw value to exactly one of: effective, ineffective, mixed. "
        "Any qualified effective (e.g. 'effective for VCs') maps to 'effective'. "
        "Every value must map to exactly one item from the list.",
    ),
}

# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

def create_client() -> genai.Client:
    load_dotenv(ROOT / ".env")
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not set.")
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Unique value extraction
# ---------------------------------------------------------------------------

def get_unique_values(df: pd.DataFrame, field: str) -> list[str]:
    if field not in df.columns:
        return []
    return (
        df[field]
        .dropna()
        .str.split(",")
        .explode()
        .str.strip()
        .str.lower()
        .replace("", None)
        .dropna()
        .unique()
        .tolist()
    )


# ---------------------------------------------------------------------------
# Gemini suggestion
# ---------------------------------------------------------------------------

def suggest_mappings(
    client: genai.Client,
    field: str,
    values: list[str],
    canonical: list[str],
    instruction: str,
) -> dict[str, str]:
    if not values:
        return {}

    values_json = json.dumps(values, ensure_ascii=False)
    canonical_json = json.dumps(canonical, ensure_ascii=False)

    prompt = f"""You are normalizing extracted data from an academic literature
review on signaling theory in entrepreneurial finance.

Field: {field}

Canonical values (you must only use these as output values):
{canonical_json}

Normalization instructions:
{instruction}

Raw values to normalize:
{values_json}

Return exactly one valid JSON object mapping each raw value to its canonical
form. Every key must be a raw value from the input list. Every value must be
one of the canonical values listed above. Do not invent new canonical values.
Do not wrap output in markdown fences or commentary.

Return format: {{"raw_value": "canonical_value", ...}}
"""

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )
    raw = getattr(response, "text", str(response)).strip()
    cleaned = re.sub(r"```json\s*", "", raw)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        print(f"  [WARNING] Could not parse Gemini response for {field}.")
        return {}
    try:
        result = json.loads(match.group())
        # enforce that all values are from the canonical list
        for k, v in result.items():
            if v not in canonical:
                print(f"  [WARNING] Non-canonical value '{v}' for key '{k}' "
                      f"in field {field}. Keeping as-is for review.")
        return result
    except json.JSONDecodeError as exc:
        print(f"  [WARNING] JSON parse error for {field}: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Suggestions markdown writer
# ---------------------------------------------------------------------------

def write_suggestions_md(
    suggestions: dict[str, dict[str, str]],
    path: Path,
) -> None:
    lines = [
        "# Normalization Suggestions\n\n",
        "Review each mapping below. Edit canonical values if needed.\n",
        "Run with `--apply` to apply these mappings.\n\n",
        "---\n",
    ]
    for field, mapping in suggestions.items():
        lines.append(f"\n## {field}\n\n")
        lines.append("| Raw Value | Canonical Value |\n")
        lines.append("|-----------|----------------|\n")
        for raw, canonical in sorted(mapping.items()):
            lines.append(f"| `{raw}` | `{canonical}` |\n")

    path.write_text("".join(lines), encoding="utf-8")
    print(f"Suggestions written to {path.name}")


# ---------------------------------------------------------------------------
# Suggestions markdown reader
# ---------------------------------------------------------------------------

def read_suggestions_md(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    result: dict[str, dict[str, str]] = {}
    current_field: str | None = None

    for line in text.splitlines():
        field_match = re.match(r"^## (.+)$", line.strip())
        if field_match:
            current_field = field_match.group(1).strip()
            result[current_field] = {}
            continue
        if current_field and line.startswith("|") and "`" in line:
            parts = [p.strip().strip("`") for p in line.split("|") if p.strip()]
            if len(parts) == 2:
                raw, canonical = parts
                if raw and canonical and raw != "Raw Value":
                    result[current_field][raw] = canonical

    return result


# ---------------------------------------------------------------------------
# Normalization application
# ---------------------------------------------------------------------------

# Fields where positional alignment must be preserved — never deduplicate.
POSITIONAL_FIELDS = {
    "signal_vehicles",
    "signal_vehicle_category",
    "signal_effectiveness_direction",
}


def normalize_comma_field(
    value: str,
    alias_map: dict[str, str],
    deduplicate: bool = True,
) -> str:
    if not isinstance(value, str) or not value.strip():
        return value
    items = [item.strip().lower() for item in value.split(",") if item.strip()]
    normalized = [alias_map.get(item, item) for item in items]
    if not deduplicate:
        return ", ".join(normalized)
    # deduplicate while preserving order (non-positional fields only)
    seen: set[str] = set()
    deduped = []
    for v in normalized:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return ", ".join(deduped)


def apply_suggestions(
    df: pd.DataFrame,
    suggestions: dict[str, dict[str, str]],
) -> pd.DataFrame:
    df = df.copy()
    for field, mapping in suggestions.items():
        if field not in df.columns:
            continue
        lower_mapping = {k.lower(): v for k, v in mapping.items()}
        deduplicate = field not in POSITIONAL_FIELDS
        df[field] = df[field].apply(
            lambda v, m=lower_mapping, d=deduplicate: normalize_comma_field(v, m, d)
        )
    return df


def derive_industry_broad(df: pd.DataFrame) -> pd.DataFrame:
    """Derive industry_sector_broad by taking the first value per row.

    Since industry_sector is already normalized to broad canonical values,
    this simply deduplicates to the first listed broad sector.
    For the analysis heatmaps, use industry_sector_broad.
    """
    df = df.copy()
    if "industry_sector" not in df.columns:
        return df
    df["industry_sector_broad"] = (
        df["industry_sector"]
        .fillna("")
        .str.split(",")
        .str[0]
        .str.strip()
        .replace("", pd.NA)
    )
    return df


# ---------------------------------------------------------------------------
# Log writer
# ---------------------------------------------------------------------------

def write_log_md(
    suggestions: dict[str, dict[str, str]],
    input_path: Path,
    output_path: Path,
    path: Path,
) -> None:
    lines = [
        "# Normalization Log\n\n",
        f"Input: `{input_path.name}`\n",
        f"Output: `{output_path.name}`\n\n",
        "---\n",
    ]
    for field, mapping in suggestions.items():
        changed = {k: v for k, v in mapping.items() if k != v}
        lines.append(f"\n## {field} — {len(changed)} mappings applied\n\n")
        if changed:
            lines.append("| Raw Value | Canonical Value |\n")
            lines.append("|-----------|----------------|\n")
            for raw, canonical in sorted(changed.items()):
                lines.append(f"| `{raw}` | `{canonical}` |\n")
        else:
            lines.append("_No changes applied._\n")

    path.write_text("".join(lines), encoding="utf-8")
    print(f"Log written to {path.name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM-assisted coarse normalization for extraction CSV."
    )
    parser.add_argument("--input", type=Path, default=INPUT_CSV_DEFAULT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply suggestions from normalization_suggestions.md.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print(f"Reading {args.input.name}...")
    df = pd.read_csv(args.input, dtype=str)
    print(f"  {len(df)} rows loaded.")

    if not args.apply:
        client = create_client()
        suggestions: dict[str, dict[str, str]] = {}

        for field, (canonical, instruction) in FIELDS_TO_NORMALIZE.items():
            print(f"\n[{field}] Fetching unique values...")
            values = get_unique_values(df, field)
            print(f"  {len(values)} unique values found.")
            if not values:
                continue
            print(f"  Asking Gemini for coarse canonical mappings...")
            mapping = suggest_mappings(
                client, field, values, canonical, instruction
            )
            suggestions[field] = mapping
            print(f"  {len(mapping)} mappings returned.")

        write_suggestions_md(suggestions, SUGGESTIONS_MD)
        print(
            f"\nReview {SUGGESTIONS_MD.name}, "
            f"then run with --apply to normalize."
        )

    else:
        if not SUGGESTIONS_MD.exists():
            raise FileNotFoundError(
                f"{SUGGESTIONS_MD} not found. "
                "Run without --apply first to generate suggestions."
            )

        print(f"Reading suggestions from {SUGGESTIONS_MD.name}...")
        suggestions = read_suggestions_md(SUGGESTIONS_MD)

        df_normalized = apply_suggestions(df, suggestions)
        df_normalized = derive_industry_broad(df_normalized)

        df_normalized.to_csv(OUTPUT_CSV, index=False)
        print(f"Normalized CSV written to {OUTPUT_CSV.name}.")
        print(f"  Columns: {list(df_normalized.columns)}")

        write_log_md(suggestions, args.input, OUTPUT_CSV, LOG_MD)


if __name__ == "__main__":
    main()