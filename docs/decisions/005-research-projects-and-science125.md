# ADR-005: Use a shared research contract for projects and Science 125

## Status

Accepted

## Date

2026-07-13

## Context

Chalk already has literature retrieval, multimodal evidence, hypothesis generation, human review, automated validation, and report export. Those capabilities currently converge on a legacy hypothesis record and do not provide a stable cross-disciplinary output, persistent parent/child research rounds, external-result feedback, or resumable Science 125 execution.

The competition workflow needs two user-facing workbenches: a cross-disciplinary hypothesis workbench and a chemistry workbench. They must share one scientific loop rather than develop separate hypothesis engines. The Web application also needs to evolve without changing the desktop-compatible legacy hypothesis table or requiring distributed infrastructure before the competition deadline.

## Decision

- Define `research-v1` as the shared, camelCase scientific output contract for both workbenches. Pydantic is its only authored source; committed JSON Schema and read-only TypeScript types are deterministic generated artifacts.
- Support the `general_science` and `chemistry` profiles. Chemistry may add specialist fields, while the common brief, candidate hypotheses, null hypothesis, evidence, research plan, quality, and provenance remain stable across profiles.
- Store future research projects, rounds, feedback, and batch state in Web-owned persistence. Link to legacy hypotheses and users with identifiers and immutable snapshots rather than foreign keys or cross-database transactions.
- Keep feedback creation separate from next-round generation. Saving evidence must be idempotent and must not implicitly incur another model call; a subsequent operation creates the child round.
- Give Science 125 an independent durable batch/item/attempt state machine. It must support leases, retries, and restart recovery instead of inheriting the current in-process job restart behavior.
- Deploy the competition release on one Alibaba Cloud ECS instance using Docker Compose, Nginx HTTPS, one Uvicorn worker, Next.js Server, and a persistent data volume.
- Use Qwen through DashScope with retrieval and structured prompting. Do not fine-tune a model for M0 or make fine-tuning a prerequisite for later project rounds.

## Alternatives Considered

### Build separate engines for the two workbenches

Rejected because validation, evidence handling, feedback, and provenance would drift. A profile-specific extension preserves chemistry depth without duplicating the scientific loop.

### Extend the legacy hypothesis table into the project system

Rejected because it is shared with the desktop-compatible core and represents one hypothesis rather than a project and its round lineage. Web-owned resources and soft references isolate migration risk.

### Generate a next round whenever feedback is saved

Rejected because request retries could duplicate model cost and rounds. Separating the operations gives feedback an independently idempotent lifecycle.

### Reuse in-process jobs for Science 125

Rejected because active jobs become failed after a process restart and cannot provide item-level recovery guarantees.

### Add a distributed queue or Kubernetes now

Rejected because sequential server-side processing on one persistent instance is sufficient for 125 items and has a substantially smaller operational surface before the deadline.

## Consequences

- Backend and frontend consumers share one versioned contract and can detect generated-artifact drift in CI.
- General scientific outputs cannot silently contain chemistry-only fields; chemistry outputs remain compatible with the common views.
- M1 must introduce Web-only project and round storage without cross-database foreign keys, and expose feedback storage separately from next-round generation.
- Science 125 persistence must be designed independently from the current job store before full-batch execution begins.
- Single-instance deployment simplifies recovery assumptions but requires persistent-volume backups, one-worker coordination, and explicit restart tests.
- Model improvements in M0 come from retrieval, prompts, validation, and feedback rather than training infrastructure.
