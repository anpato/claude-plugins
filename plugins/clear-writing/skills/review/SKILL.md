---
name: review
description: >-
  Read documentation and suggest improvements without editing it. Use when asked to review
  an RFC, ADR, README, how-to guide, or reference doc for clarity, concision, accuracy, or
  completeness, or to suggest wording changes. Reviews the document, not the implementation.
---

# Review Documentation

Help the author improve the document with specific, useful suggestions. Leave the source
unchanged unless the user asks you to apply edits.

## Read and assess

Read the requested document and supplied sources before judging individual passages. Infer
its audience, purpose, type, and stage: an early proposal can legitimately contain open questions.
If the document is unavailable, ask for access or its text rather than reviewing from its title.

Read [writing principles](../../references/writing-principles.md) and the relevant section of
[document types](../../references/document-types.md). Respect the repository's conventions and
the user's requested scope. For mixed documents, apply the criteria to the relevant sections.

Look for ambiguous meaning, contradictions, unsupported claims, missing information the reader
needs, and unnecessary effort caused by repetition, jargon, or structure. Do not demand every
possible section or propose a redesign under the guise of a writing correction.

Distinguish a factual error supported by evidence from an unverified claim or missing explanation.
Use available sources to check consequential claims within scope; cite the evidence when calling
something incorrect. Do not imply that a prose review verifies code or system behavior. If resolving
an issue needs unavailable facts or a decision, say what needs confirming instead of inventing a fix.

## Suggest changes

Lead with a brief assessment of whether the document serves its reader. Then list the useful
suggestions, ordered by impact: misleading or ambiguous claims first, missing context next,
and wording or repetition last. Group repeated issues and omit purely cosmetic preferences.
Do not manufacture findings or meet a quota; if no material changes are needed, say so.

For each suggestion, provide:

- **Passage:** a short exact quote with a section name or verified file and line reference.
  For missing content, identify where it belongs instead of inventing a quote.
- **Issue:** what the reader could misunderstand or fail to do, in one or two sentences.
- **Suggestion:** replacement wording that preserves the meaning, or a concrete addition,
  deletion, or move. If a replacement depends on an unknown fact, ask a targeted question or
  label a conditional example clearly; do not present it as ready to paste.

Keep replacements faithful to quantities, conditions, uncertainty, decision status, and normative
words such as "must" and "should." Keep citations with their claims. Do not rewrite the whole
document unless requested. For long documents, prioritize consequential findings and disclose
any limits on what was read or checked.

If the user subsequently asks to apply suggestions, use
[clear-writing](../clear-writing/SKILL.md), apply the authorized changes, and preserve unresolved
questions. A request for review alone is not permission to modify files or post review comments.
