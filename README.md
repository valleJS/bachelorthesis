# LLM-Assisted Systematic Literature Review Pipeline

This repository contains the extraction pipeline developed for the bachelor thesis
*"Signaling Technological Capabilities in Entrepreneurial Finance: An LLM-Assisted Systematic Review"* 
by Valentin Gies.

## Overview

The pipeline processes academic papers through three sequential stages using the
Gemini 2.5 Flash API, with researcher-defined criteria at each stage. Zotero serves
as the pipeline's state manager throughout, tracking extraction status, criteria
versions, and processing history per paper via programmatic API integration. All
screening and extraction criteria are versioned and documented within the respective
stage folders.

## Repository Structure

```
├── llm/
│   ├── extractor.py                     # LLM API integration and extraction logic
│   └── __init__.py
├── Stage 1: Relevance Screening (Title & Abstract)/
│   ├── prompts/                         # Screening criteria
│   └── zotero_resolve_unsure.py         # Script to apply inclusion/exclusion criteria
├── Stage 2: Relevance Screening (Full Text)/
│   ├── prompts/                         # Screening criteria
│   └── zotero_resolve_unsure.py         # Script for resolving uncertain screening results at full-text level
├── Stage 3: Full Text Extraction/
│   ├── criteria/                        # Extraction criteria and prompt templates
│   └── pipeline.py                      # Main extraction pipeline
├── Normalization Script & Logs/
│   ├── normalize_llm.py                 # Two-stage normalization script
│   ├── normalization_log.md             # Complete mapping log of all normalization decisions
│   └── manual_correction_log.md         # Log of manual corrections traceable to their Zotero key
├── Output CSVs/
│   ├── extractions_raw.csv              # Raw LLM extraction output
│   └── extractions_v3_normalized.csv    # Final normalized dataset (127 papers, 16 dimensions)
└── README.md
```

## Pipeline Stages

**Stage 1** screens papers by title and abstract against inclusion and exclusion
criteria. Each paper is tagged in Zotero with its screening result, enabling
programmatic querying of the library to identify papers requiring full-text review.

**Stage 2** screens full-text PDFs against the same criteria, resolving cases
that could not be determined from title and abstract alone. Papers passing
this stage are tagged in Zotero for full-text extraction.

**Stage 3** extracts sixteen structured analytical dimensions from each paper,
including construct definitions, signal vehicles, effectiveness directions,
complementarity effects, and contextual variables. The pipeline queries Zotero
to identify unprocessed papers and tags each completed extraction with its
criteria version, enabling incremental corpus updates without reprocessing.

**Normalization** maps the extracted values of the controlled-vocabulary fields into canonical forms through
a two-stage process: LLM-suggested canonical mappings followed by researcher
approval. All mappings and manual corrections are documented in the
normalization logs.

## Output

The final dataset (`extractions_v3_normalized.csv`) contains 127 papers with
16 extracted dimensions per paper. Zotero keys in the `zotero_key` column
enable traceability to individual papers and direct lookup in the source
library. This dataset forms the empirical basis for all quantitative and
qualitative findings reported in the thesis.

## Notes

- File paths in the scripts reference the original local development
  environment and may need to be adjusted to run in a different setup.
- The pipeline requires a valid Gemini API key.
- The Zotero integration requires access to the corresponding Zotero library
  and a valid Zotero API key.
