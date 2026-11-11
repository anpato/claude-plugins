# Clear Writing

Write, edit, and review clear, concise, accurate documentation. The skills cut repetition, jargon, and
clever phrasing while preserving requirements, caveats, reasoning, and evidence.

## Install

```bash
command claude plugin install clear-writing@apato-plugins
```

If the plugin is not listed yet, update the marketplace first:

```bash
command claude plugin marketplace update apato-plugins
```

## Use

Both skills can activate automatically for the relevant task or be invoked explicitly:

| Command | Purpose |
| --- | --- |
| `/clear-writing:clear-writing` | Draft or edit documentation. |
| `/clear-writing:review` | Suggest improvements without changing the document. |

```text
/clear-writing:clear-writing Tighten docs/design.md without changing its meaning.
/clear-writing:clear-writing Draft a README from this code for someone setting it up for the first time.
/clear-writing:review Read docs/proposal-rfc.md and suggest improvements.
```

Reviews give prioritized suggestions with a quoted passage or location, a brief explanation,
and proposed wording or a concrete next step when facts need confirming.

Both skills infer the audience and document type and share guidance for RFCs, ADRs, READMEs,
how-to guides, and reference docs. The guidance respects repository conventions and does not
require every document to follow the same template.

The plugin works independently of `rubber-ducky` and can apply when writing an RFC with that
plugin. A language edit or review does not imply that technical claims have been verified.

## Validation

```bash
command claude plugin validate plugins/clear-writing
```
