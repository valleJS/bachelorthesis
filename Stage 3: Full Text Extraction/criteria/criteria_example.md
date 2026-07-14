---
version: 1
description: <Short label for this extraction criteria version>
fields:
  - construct_a_definition
  - construct_a_operationalization
  - construct_b_definition
  - construct_b_operationalization
  - mechanism
  - mechanism_category
  - mechanism_effectiveness
  - interaction_effects
  - mediation_moderation_effects
  - empirical_context
  - data
  - method
  - results
---

# ── How to use this template ──────────────────────────────────────────
#
# 1. Replace <Short label ...> in the header with a description of your
#    review, e.g. "Platform governance extraction" or "CSR–performance
#    meta-analysis".
#
# 2. Edit the fields list to match the JSON keys you want the LLM to
#    return. Every field listed here becomes a column in your output CSV.
#    Add, remove, or rename freely — the pipeline reads this list at
#    runtime.
#
# 3. Rewrite everything below the closing --- to fit your research
#    question. The text below is sent verbatim to the LLM as the
#    extraction prompt, with the paper's Markdown appended at the end.
#
# 4. Keep the JSON schema at the bottom of the prompt in sync with the
#    fields list above — same keys, same order.
#
# 5. Delete these comment lines before running the pipeline.
# ──────────────────────────────────────────────────────────────────────

You are extracting structured data for a systematic literature review.

You are given full Markdown converted from a scholarly PDF. The text can be
long, so navigate it selectively:
- Search the "Methods", "Methodology", "Data", "Sample", "Results",
  "Discussion", "Conclusion", and "Abstract" sections first.
- Use tables, figure captions, and nearby headings only when they explicitly
  state a relevant data source, method, or result.
- Ignore references, bibliography, appendices, acknowledgements, and author
  notes.

Only extract information that is explicitly stated in the paper. Do not infer
from outside knowledge, citation titles, or your own domain assumptions. If a
field is not found, return an empty string.

Extraction rules:

- "construct_a_definition" must capture how the paper explicitly or implicitly
  conceptualizes or defines Construct A. If you cannot confidently identify a
  definition, return an empty string.

- "construct_a_operationalization" must capture how the paper operationalizes
  or measures Construct A. If the paper uses a proxy without defining the
  construct, describe the proxy. If not measured, return an empty string.

- "construct_b_definition" must capture how the paper explicitly or implicitly
  conceptualizes or defines Construct B. If you cannot confidently identify a
  definition, return an empty string.

- "construct_b_operationalization" must capture how the paper operationalizes
  or measures Construct B. If the paper uses a proxy without defining the
  construct, describe the proxy. If not measured, return an empty string.

- "mechanism" must be a comma-separated list of the concrete mechanisms,
  instruments, or channels the paper examines as linking Construct A to
  Construct B.

- "mechanism_category" must classify each mechanism into a category relevant
  to your theory. Return as a comma-separated list matching the order of
  "mechanism" exactly.

- "mechanism_effectiveness" must summarize whether the paper finds each
  mechanism to be effective, ineffective, or mixed. Return as a comma-
  separated list matching the order of "mechanism" exactly.

- "interaction_effects" must indicate whether the paper explicitly examines
  mechanisms working in combination. Return "yes" if examined, "no" if
  explicitly absent, or empty string if not addressed.

- "mediation_moderation_effects" should include only explicitly examined
  mediation or moderation effects.

- "empirical_context" must name the empirical setting, industry, country, or
  domain the study focuses on. If industry-agnostic, return "general".

- "data" should summarize the data source, sample, context, and unit of
  analysis.

- "method" should name the research design, model, or analytical approach.

- "results" should summarize the main findings that are explicitly stated.

Return exactly one valid JSON object and nothing else. Do not wrap the output
in markdown fences or commentary. Use this schema:
{
  "construct_a_definition": "",
  "construct_a_operationalization": "",
  "construct_b_definition": "",
  "construct_b_operationalization": "",
  "mechanism": "",
  "mechanism_category": "",
  "mechanism_effectiveness": "",
  "interaction_effects": "",
  "mediation_moderation_effects": "",
  "empirical_context": "",
  "data": "",
  "method": "",
  "results": ""
}