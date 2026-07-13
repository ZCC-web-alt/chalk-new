# ADR-002: Use revisioned scientific workspaces and dry-run-only modeling exports

## Status

Accepted

## Date

2026-07-11

## Context

The desktop Chalk workflows analyze one file at a time and primarily display model output as text. A browser workflow must support multiple uploaded or document-owned resources, recover after refresh, keep user corrections auditable, and prevent generated modeling suggestions from being mistaken for validated calculation inputs or completed scientific evidence.

Multimodal model output, spreadsheet content, uploaded structures, and legacy workflow packages are all untrusted inputs. The Web backend must preserve user ownership and provenance without exposing server paths or mutating the desktop application's caches.

## Decision

- Store user-scoped multimodal assets, analysis runs, modeling workspaces, revisions, structures, and artifacts in `chalk-new/data/web.db`. Managed files stay under user-specific `chalk-new/data` directories.
- Keep each model's original structured result immutable. Human corrections create a revised copy with a monotonically increasing revision and a server-computed diff. `expectedRevision` prevents a stale browser tab from overwriting newer work.
- Persist explicit provenance for every multimodal source and modeling parameter. Parameter sources are limited to model defaults, manual context, literature, hypothesis workflow data, or user revision.
- Inject multimodal evidence into hypothesis generation only when the user explicitly selects successful runs. Snapshot the selected run revision and corrected result so later edits cannot silently change an existing hypothesis trace.
- Validate image and workbook uploads at the API boundary, enforce bounded decoding and archive limits, and allow batch analysis to report partial success. All-selected-source failure still fails the Job.
- Treat structures as exportable only after `pymatgen` successfully parses them. An unvalidated structure produces a warning or preparation guide, never a placeholder POSCAR.
- Export only reviewable dry-run packages. Packages may contain INCAR, KPOINTS, a validated POSCAR, guides, manifest, provenance, and risk notices. They never contain licensed POTCAR binaries, execute commands, submit VASP, or write suggestions into the evidence database as completed calculations.
- Render validated structure geometry in the browser with Three.js. The preview is an inspection aid, not proof that a calculation is physically or numerically valid.

## Alternatives Considered

### Overwrite model output after each correction

Rejected because it destroys the distinction between machine extraction and human review, makes two-tab conflicts silent, and weakens hypothesis provenance.

### Export a zero-coordinate POSCAR when no structure is available

Rejected because a syntactically plausible placeholder could be mistaken for runnable scientific input.

### Execute VASP, Materials Studio, or remote scheduler jobs from Chalk Web

Rejected for this migration phase. It would require credential management, licensed software boundaries, scheduler integration, resource quotas, and a substantially stronger execution sandbox.

### Automatically promote modeling suggestions into evidence

Rejected because generated parameters and atomate2 dry-runs are plans, not observed computational results.

## Consequences

- Browser refreshes can restore Jobs, runs, workspaces, and revision history without relying on Qt process state.
- Users can correct extracted data and modeling parameters while retaining a reproducible audit trail.
- Exports may omit POSCAR until the user supplies a valid structure; this is intentional and visible in the manifest.
- Real calculation execution and collaborative editing remain separate future capabilities rather than implicit behavior in the current API.
