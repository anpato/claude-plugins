---
name: clear-writing
description: >-
  Write or edit documentation in clear, concise, accurate language. Use when drafting or
  revising RFCs, ADRs, READMEs, design docs, technical explanations, or guides, and when asked
  to tighten prose, remove jargon, or make a document easier to understand.
---

# Clear Writing

Help the reader understand the subject and act on it. Use the fewest words that preserve the
meaning, reasoning, and necessary context. Accuracy takes priority over brevity.

## Establish the meaning

- Read the material being edited and any supplied sources. Infer the audience and purpose
  from the request; ask only when missing context would materially change the document.
- Identify what the reader needs to know, decide, or do. Lead with that, then explain why.
- Respect the requested scope, voice, and repository format. A language edit does not authorize
  changing the design, deciding an open question, or turning a proposal into a commitment.
- When drafting, ground technical claims in the available sources. When editing, distinguish
  clearer wording from verified facts; do not imply that a prose edit validates the system.
  Check consequential contradictions or uncertainties when sources are available. Otherwise,
  flag the specific gap without inventing an answer.

## Make the language direct

- Name the actor and action: "The worker retries the request" instead of "Retry handling is
  performed." Prefer active voice when the actor matters; passive voice is fine when it does not.
- Use concrete verbs: "decide" instead of "make a determination"; "use" instead of "utilize."
  Replace abstract claims with the actual behavior, condition, or consequence.
- Keep subjects close to their verbs. Split sentences that carry several independent claims.
  Keep conditions and exceptions next to the claims they limit.
- Use one term for one concept. Preserve exact API names, identifiers, and domain terms.
  Explain unfamiliar terms on first use when the audience needs it; do not replace precise
  terminology with a vague everyday synonym.
- Remove throat-clearing, inflated adjectives, and commentary about the document itself.
  Delete phrases such as "it is important to note" and "as previously mentioned" when the
  sentence works without them.
- Avoid slogans, rhetorical questions, forced metaphors, and invented labels that make the
  reader decode the prose. State the mechanism or distinction directly. Use a comparison only
  when it clarifies a real choice or likely misunderstanding.
- Replace unsupported "seamless," "robust," "simple," or "obvious" with evidence or remove them.
  Do not add numbers or guarantees to make a claim sound more specific.

## Cut without losing information

- Give each paragraph one main point. Keep the explanation and evidence with that point.
- Remove repeated conclusions and background the intended reader already knows. Keep rationale,
  prerequisites, trade-offs, failure behavior, and examples needed to use or assess the design.
- Use headings for navigation, lists for parallel items or steps, and tables for comparisons.
  Do not turn connected reasoning into a pile of fragments or give every sentence a heading.
- Follow required templates. Otherwise, omit empty sections and sections that merely repeat
  earlier material. Add a summary only when it helps readers navigate a longer document.
- Scale detail to the task. Do not chase a word limit, compression percentage, or sentence length
  unless the user supplied one. A short sentence that hides the reason is not an improvement.

## Preserve accuracy

- Preserve negation, quantities, units, conditions, exceptions, scope, and sequence.
- Preserve the force of words such as "must," "should," "may," and "will." Keep current behavior,
  proposals, requirements, assumptions, and unresolved questions distinguishable.
- Keep uncertainty when it is real. "May reduce latency" must not become "reduces latency"
  without evidence. Remove empty hedging only when the claim remains equally justified.
- Keep citations attached to the claims they support. Do not invent references, measurements,
  decisions, or explanations. Preserve quotes exactly, or clearly present a paraphrase.
- If a sentence has two plausible meanings, resolve it from the sources or flag the ambiguity.
  Do not silently choose the tidier interpretation.

## Examples

These examples illustrate language edits, not facts to reuse.

| Before | After |
| --- | --- |
| In order to facilitate the retrieval of records, the service makes use of a cache. | The service uses a cache to retrieve records. |
| It is important to note that clients must include a token on every request. | Clients must include a token on every request. |
| This isn't just a queue; it's the backbone of a resilient processing story. | The queue holds jobs until a worker can process them. |
| Requests may, under certain circumstances where the cache is cold, experience increased latency. | Requests may take longer when the cache is cold. |
| The worker will make up to three retry attempts, except in cases where the request has failed validation. | The worker retries up to three times unless the request fails validation. |

## Before delivering

Compare the revision with the source for changed meaning, missing constraints, or stronger claims.
Read it as someone encountering the subject for the first time: can they identify what happens,
why, and under which conditions? Remove any sentence that adds neither information nor needed context.

Return the requested document or edit. Keep any accompanying explanation brief; mention substantive
meaning changes and unresolved factual gaps. Do not append a writing lesson or a list of every cut.
