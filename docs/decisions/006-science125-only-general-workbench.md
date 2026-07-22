# ADR-006: Restrict the general workbench to Science 125

## Status

Superseded by ADR-007

## Date

2026-07-15

## Context

Chalk's established product is the chemistry hypothesis pipeline. The proposed M1
chemistry workbench and persistent research-project user interface duplicated its
entry points without adding a required user workflow. The only cross-disciplinary
experience needed for the competition is an auditable interface for the authoritative
Science 125 benchmark questions.

The legacy hypothesis prompt is chemistry-oriented. Reusing it for arbitrary Science
125 domains would misrepresent the system's capabilities and produce unsupported
scientific output. The dedicated Science 125 prompt and evaluation policy have not yet
been reviewed, so automatic hypothesis generation cannot be enabled truthfully.

## Decision

- Retain the legacy chemistry hypothesis pipeline as Chalk's primary hypothesis
  capability. Remove the M1 chemistry-workbench and research-project routes, sidebar
  entries, APIs, persistence, migrations, and client models.
- Expose one cross-disciplinary route, `/research/general/new`. It reads the
  authenticated `science125-v1` catalog and permits selection only from its 125
  canonical questions; free-text substitutions are rejected by the job API.
- The cross-disciplinary flow is: select a question, run literature presearch, let the
  researcher select the relevant literature, then prepare the locked hypothesis input.
  Science 125 jobs carry their question ID so their in-progress searches and HITL work
  cannot be resumed from an unrelated legacy workspace.
- The manifest remains a lightweight, versioned catalog of headlines and source metadata.
  Complete booklet explanations are served from a machine-local
  `science125-context-v1` index generated from the verified source PDF. The index is
  accepted only when its source-PDF SHA-256, item IDs, page mapping, headline, and
  per-item context hashes match the catalog; a missing or invalid index keeps the
  source-context endpoint unavailable rather than silently falling back to the headline.
- Literature search terms are separate, editable input from the authoritative question.
  The UI derives an initial keyword query from the headline and booklet context, while
  the job remains bound to the Science 125 item ID. This prevents literal question
  matching from returning irrelevant papers without weakening benchmark traceability.
- Researchers may add selected pages from owned PDFs through
  `POST /api/documents/{documentId}/page-excerpts`. The server re-reads only the
  requested pages, checks ownership, managed-storage containment and PDF validity,
  and returns PDF/text hashes. A researcher may hand off reviewed PDF pages even when
  an external literature provider returns no usable result. The reviewed hypothesis
  seed carries those hashes as structured page selections; the dedicated generation
  input assembler re-extracts the pages, verifies both hashes, and injects the verified
  text into the scientific input. Browser-provided page text is never authoritative.
- The server, not the browser, owns the Science 125 question binding. It rejects an
  exact benchmark headline that omits `science125Id`, preventing callers from routing
  a benchmark question through the legacy chemistry prompt. For a bound item, the
  worker loads the full explanation from the verified context index and places it
  before user-reviewed search material and page excerpts. Client text cannot replace
  or shorten the authoritative booklet context.
- Keep Science 125 hypothesis generation disabled until a domain-appropriate prompt
  and evaluation policy are explicitly accepted. The UI states this boundary and the
  API and job service both return `SCIENCE125_PROMPT_NOT_CONFIGURED` before any Qwen
  call, so neither public nor internal callers can fall back to a chemistry prompt.
- Retain `research-v1` only as the M0 structured contract and Qwen Canary validation
  artifact. Its project/round implementation described in ADR-005 is not part of the
  current product scope.

## Alternatives Considered

### Keep the two new workbenches and project model

Rejected because they add a second navigation and persistence model before the
competition's benchmark workflow has a reviewed prompt or a validated need for project
lineage.

### Reuse the chemistry prompt for Science 125

Rejected because a chemistry-biased prompt cannot honestly generate or validate plans
across mathematics, medicine, astronomy, ecology, and AI.

### Permit arbitrary questions in the general workbench

Rejected because the workbench exists to make the competition benchmark traceable to
the authoritative, versioned catalog. General free-text questions remain outside this
entry point.

## Consequences

- The visible general workflow is smaller and shareable, while chemistry users retain
  the existing Chalk tools without a duplicate workbench.
- The benchmark prompt is an explicit upcoming design decision. Until then, literature
  work, page-only evidence review and the complete server-assembled input can be
  demonstrated, but no benchmark hypothesis may be generated through the legacy Qwen
  hypothesis pipeline.
- A source-context index must be generated locally or mounted into the runtime data
  directory before the full-question view is available. The source PDF and generated
  index remain outside version control; only their verification contract is committed.
- Future Science 125 batch execution must be designed around the reviewed dedicated
  prompt and its evaluation rules. It must not reintroduce the removed M1 project model
  merely to run the benchmark.
