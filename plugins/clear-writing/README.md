# Clear Writing

Write and edit clear, concise, accurate documentation. The skill cuts repetition, jargon, and
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

The skill can activate automatically when drafting or editing documentation, including RFCs,
ADRs, READMEs, and guides. Invoke it explicitly with `/clear-writing:clear-writing`:

```text
/clear-writing:clear-writing Tighten docs/design.md without changing its meaning.
/clear-writing:clear-writing Draft a README from this code for someone setting it up for the first time.
```

It works independently of `rubber-ducky` and can apply when writing an RFC with that plugin.
A language edit does not imply that the document's technical claims have been verified.

## Validation

```bash
command claude plugin validate plugins/clear-writing
```
