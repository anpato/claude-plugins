# Document types

Infer the type from the document's purpose and content; ask only if the distinction matters and
is unclear. Use the relevant section below as criteria, not a mandatory template. Follow the
repository's format. Apply different criteria to different sections of mixed documents. For an
unlisted type, use the reader's task and the shared writing principles rather than forcing a fit.

## RFC or design proposal

The reader needs to understand and evaluate a proposed change.

- State the problem, scope, proposed behavior, and reason for the proposal early. Separate
  current behavior from intended behavior; ground current-state claims in sources.
- Explain real alternatives and consequential trade-offs. Make the reasoning assessable;
  avoid presenting the preferred option as inevitable or inventing alternatives to fill a template.
- Distinguish requirements, constraints, proposed decisions, accepted decisions, and open questions.
  Preserve review status. Missing decisions can be appropriate in a draft when clearly identified.
- Include interfaces, failure behavior, rollout, and validation when they affect the reader's
  ability to judge the proposal. Keep implementation inventories only when useful to that judgment.
- Review for claims such as "more scalable" without a workload or mechanism, hidden assumptions,
  and trade-offs buried in implementation detail. Ask for missing evidence rather than supplying it.

## ADR

The reader needs to understand one decision and why it was made.

- State the decision and status clearly, with the context, constraints, alternatives considered,
  rationale, and consequences needed to understand it later.
- Preserve the distinction between a proposed and accepted decision. Record actual reasoning;
  do not invent a retrospective justification or imply alternatives were considered without evidence.
- Keep enough historical context to explain the choice without turning the record into a full RFC.
  Name accepted costs and follow-up obligations as well as benefits.
- Respect the repository's amendment and supersession rules. Editing an accepted record must not
  silently change the decision or rewrite its history.

## README

The reader needs to know what the project does, whether it fits their need, and how to start.

- Explain purpose and audience, then provide the shortest supported path to a useful result.
  Include prerequisites, installation, required configuration, and a minimal usage example as needed.
- Make the working directory, placeholders, and expected result clear where they matter.
  Keep setup and usage commands consistent with the actual project and package names.
- Link to deeper guides and reference material rather than copying them into the introduction.
  Separate contributor setup from ordinary use when they require different steps.
- Review for unexplained prerequisites, examples that cannot run as shown, and installation
  instructions buried beneath background. Say if commands were inspected but not executed.

## How-to guide

The reader needs to complete a specific task and know whether it worked.

- State the intended outcome, starting conditions, permissions, and prerequisites needed for it.
- Order steps by dependency. Identify where commands run, what users must replace, and what state
  carries between steps. Place branches and cautions before the action they affect.
- Include observable success criteria and recovery for likely failures when supported by sources.
  Explain destructive effects before their commands; do not invent rollback guarantees.
- Keep conceptual background brief and link elsewhere for depth. Review for hidden steps,
  ambiguous instructions, and missing verification. A runnable example still needs authorization
  before any action outside the user's requested scope.

## Reference documentation

The reader needs to look up an exact contract or behavior.

- Use stable names and a consistent structure. State applicable versions or scope when behavior
  differs; preserve exact identifiers, signatures, types, units, and enum values.
- Specify required and optional inputs, defaults, outputs, errors, side effects, and relevant
  limits or edge cases. Distinguish omitted, null, empty, and zero where they behave differently.
- Use examples to clarify the contract, not replace it. Separate guaranteed behavior from
  implementation details, and requirements from recommendations.
- Review for vague promises, missing defaults, inconsistent terminology, and examples that
  contradict the definition. Unknown behavior is a gap to verify, not a value to infer.
