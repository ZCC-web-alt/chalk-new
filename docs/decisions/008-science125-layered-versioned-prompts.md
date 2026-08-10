# ADR-008: Version Science 125 domain and question prompt modules

## Status

Accepted

## Date

2026-08-09

## Context

ADR-007 introduced one prompt module for each of the 12 Science 125 domains. That
level is useful for broad scientific guardrails, but it cannot express the distinct
observables, controls, failure modes, and inference boundaries of all 125 questions.
A single chemistry module, for example, must cover pigments, superheavy elements,
interfaces, energy storage, chirality, polymers, AI for chemistry, living materials,
and origins-of-reproduction questions without assuming that any of them is an
electrocatalysis problem.

The prompt is part of a reproducible research run. Changing it silently after a batch
starts would make two reports with the same batch identity incomparable. Conversely,
copying an entire 125-question registry into every Qwen context would waste tokens and
increase cross-question contamination. Team review also needs an honest state: a
machine-authored draft is not an expert-reviewed scientific instruction.

## Decision

- Introduce the immutable `science125-prompts-v2` registry, bound by SHA-256 to both
  `science125-v1` and `science125-routing-v1`. It contains exactly 12 domain modules
  and one module for every continuous ID from `S125-001` through `S125-125`.
- Compose each new prompt in this order: Qwen safety and JSON rules, hash-verified
  booklet context, reviewed evidence, server routing, `research-v1` rules, one domain
  module, one question module, primary/secondary method modules, and the JSON Schema.
  Only the current question's modules are loaded into the model context.
- Keep `science125-prompts-v1` available for batches already pinned to v1. New
  interactive runs and batches use v2. A batch stores both `promptVersion` and
  `promptRegistrySha256`; a mismatch stops before retrieval or a Qwen call.
- Give every question module a stable module SHA-256 and the fields `researchObjective`,
  `requiredConcepts`, `evidenceRequirements`, `variablesAndObservables`,
  `comparatorsAndControls`, `discriminatingTests`,
  `negativeEvidenceAndFailureModes`, `scopeBoundaries`, `forbiddenInferences`, and
  `requiredDomainChecks`.
- Restrict `requiredDomainChecks` to the existing `research-v1` enum. The server maps
  the module's required checks into the output extension and overwrites model-supplied
  routing or provenance fields.
- Mark all initial modules `draft_pending_review`. Change a module to `team_reviewed`
  only after a recorded team review of every field. The first review targets are
  `S125-006`, `S125-043`, and `S125-054`.
- Expose only `promptVersion`, `promptModuleHash`, and `promptReviewStatus` through the
  question profile API. Do not expose full internal modules or accept prompt content,
  prompt versions, module hashes, or providers from the client.
- Keep standard English scientific terms inside primarily Chinese modules where those
  terms reduce ambiguity. Modules constrain evidence and reasoning but do not inject
  candidate mechanisms, answers, unverifiable thresholds, or citations.
- Continue to use DashScope Qwen as the only foundation-model path. Neither v2 nor its
  fallback behavior imports the legacy chemistry or electrocatalysis prompt.

## Alternatives Considered

### Expand only the 12 domain modules

Rejected because domain-level instructions cannot distinguish the observables and
failure modes of individual questions. They also encourage generic plans that appear
scientific without being discriminating.

### Store prompt text in the browser or API request

Rejected because clients could alter scientific constraints, bypass review status,
or produce reports that claim the same version while using different instructions.

### Replace v1 in place

Rejected because existing reports and batches need their original prompt behavior for
audit and reproduction. Version pinning is cheaper and safer than silent mutation.

### Load all 125 modules into every call

Rejected because it increases cost and makes unrelated question language available to
the model, raising prompt contamination risk without improving the current task.

### Mark generated modules as expert reviewed

Rejected because review status is provenance, not a quality aspiration. False expert
labels would invalidate the audit trail.

## Consequences

- Each report can identify the exact domain and question instruction through stable
  hashes, and a batch cannot resume under a different registry unnoticed.
- Prompt construction and API responses gain additive version metadata while the
  public `research-v1` structure remains unchanged.
- Updating one question requires a new registry hash and deliberate version policy;
  editing a module in place will block an already pinned v2 batch.
- The team must review 125 modules over time. Until then, the UI and API accurately
  report `draft_pending_review`, and real Qwen pilots remain subject to human review.
