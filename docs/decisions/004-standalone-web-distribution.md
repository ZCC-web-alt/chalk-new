# ADR-004: Distribute Chalk Web as a self-contained project

## Status

Accepted

## Date

2026-07-12

## Context

Chalk Web originally lived beside the desktop repository and loaded scientific modules, databases, and resources from that parent checkout. The frontend could run independently, but the backend depended on a specific directory name and location. Copying the Web directory elsewhere either broke imports or silently continued to use the original Chalk data.

The Web application needs a reproducible distribution boundary that can be copied or renamed without changing code. Runtime databases and user assets must also belong to that copied instance. At the same time, the Web backend should continue to reuse Chalk's established scientific implementation instead of maintaining a second implementation.

## Decision

- Vendor the Web-compatible scientific core under `src/chalk_app` inside the Web project.
- Exclude desktop-only application and UI modules, their PySide6 dependency, caches, desktop worker entry points, and the hard-coded desktop manual PDF script from the Web distribution.
- Describe the bundled package with the root `pyproject.toml`, and install it through `backend/requirements.txt` using `-e ..`.
- Derive the project, backend, source, and data paths from files inside the copied project rather than from a parent checkout or a fixed directory name.
- Keep Web runtime state under the local `data/` directory by default. A copied instance therefore owns its `app.db`, `web.db`, uploads, sessions, keys, and generated assets.
- Keep `CHALK_LEGACY_ROOT` only as an explicit development compatibility override. Standalone startup and documented installation do not set or require it.
- Put both `backend` and `src` on the development server's `PYTHONPATH` so the project-relative launcher works before and after editable installation.

The source distribution is self-contained, but it is not an offline bundle: Python and Node.js dependencies still come from their package managers, and network-backed providers retain their normal connectivity and credential requirements.

## Alternatives Considered

### Continue importing from the desktop checkout

Rejected because it makes deployment location-dependent, shares mutable source and data across installations, and can appear to work while reading the wrong database.

### Duplicate scientific logic inside the backend package

Rejected because two implementations would drift and require parallel fixes and validation.

### Package the entire desktop application

Rejected because browser delivery does not need the Qt UI or desktop worker entry points. Including them would enlarge the dependency surface and blur the boundary between desktop and Web responsibilities.

### Remove `CHALK_LEGACY_ROOT` immediately

Rejected for now because the override remains useful for deliberate compatibility testing during migration. It is opt-in and is not part of the standalone installation path.

## Consequences

- `chalk-new` can be copied, renamed, or moved without retaining the original repository.
- Each copy uses its own runtime data unless an operator explicitly configures another data directory.
- Scientific source updates must be deliberately synchronized into the vendored package and verified through Web tests.
- New scientific modules must avoid desktop-only imports, or declare optional dependencies behind feature boundaries, before entering the Web distribution.
- Release checks must include an import and backend smoke test from a relocated copy with `CHALK_LEGACY_ROOT` unset.
