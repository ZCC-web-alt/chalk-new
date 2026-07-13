# ADR-001: Run legacy hypothesis orchestration through persistent Web Jobs

## Status

Accepted

## Date

2026-07-11

## Context

The legacy Chalk hypothesis workflow is synchronous, long-running, and may pause multiple times for human review. The Web application must preserve desktop compatibility, avoid importing Qt workers, survive browser refreshes, and prevent a server restart from accidentally repeating model or external-service calls.

The legacy SQLite database remains the source of truth for completed `Hypothesis` and `HypothesisFeedback` records. Web-only Job state and managed artifacts belong under `chalk-new`.

## Decision

- Invoke `HypothesisOrchestrator` directly from a dedicated two-thread hypothesis executor. Never import `HypothesisWorker` or PySide6 in the Web backend.
- Persist Job status, stage, progress, safe feedback prompts, result summaries, and errors in `chalk-new/data/web.db`.
- Use an in-process feedback broker only to block and wake the active worker thread. The durable `WAITING_FOR_FEEDBACK` row lets the browser restore the review UI after a refresh while the same server process is running.
- Mark `QUEUED`, `RUNNING`, and `WAITING_FOR_FEEDBACK` Jobs as `FAILED/SERVER_RESTARTED` during shutdown or startup. A restarted process never attempts to resume an in-memory orchestrator.
- Allow one active hypothesis-generation Job per user. Exact duplicate requests return the existing Job; different requests return `409 HYPOTHESIS_JOB_ACTIVE`.
- Save the completed legacy `Hypothesis` and any `HypothesisFeedback` rows in one transaction. Failed or cancelled Jobs do not create partial legacy records.
- Store HTML reports, Agent Trace JSON, and workflow ZIP metadata in a user-scoped Web table. Artifact files stay under `chalk-new/data/hypothesis-assets/{userId}/{hypothesisId}`.
- Treat HITL JSON edits and legacy output as untrusted. API responses remove paths, report previews use a sandbox without same-origin permission, and workflow exports can only read structure files from Chalk-managed structure directories.

## Alternatives Considered

### Import the Qt HypothesisWorker

Rejected because it couples the Web server to PySide6, Qt signals, and desktop lifecycle assumptions.

### Persist and resume the complete orchestrator state

Rejected for this migration phase. The legacy orchestrator does not expose a stable serializable checkpoint, and replaying partially completed external calls could duplicate cost or produce inconsistent results.

### Store completed hypotheses only in web.db

Rejected because the desktop application must continue to read Web-generated hypotheses and HITL history from the existing legacy schema.

## Consequences

- Browser refresh recovery works while the backend process remains alive.
- Backend restart is explicit and deterministic: the user reruns the failed hypothesis Job.
- Waiting HITL tasks consume a thread from the dedicated hypothesis pool, but do not block document-analysis workers.
- A future durable workflow engine can supersede this ADR if Chalk needs cross-process checkpoint continuation or distributed execution.
