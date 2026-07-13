# ADR-003: Keep untrusted pickle and server paths out of browser workflows

## Status

Accepted

## Date

2026-07-12

## Context

The desktop evidence workbench can select arbitrary local directories and import official OCP-Dense pickle files. A desktop user already has local filesystem authority. A browser user does not, and the FastAPI process may serve several users.

The legacy OCP-Dense importer calls `pickle.load`. Pickle is executable serialization and is unsafe for untrusted uploads. A browser field containing a server path would also let a remote user probe or index files outside managed storage.

## Decision

- Expose CSV, JSON, JSONL and NDJSON manifests through a size-limited, user-isolated upload and persistent Job.
- Store uploads only under `data/evidence-imports/{userId}` and delete the temporary manifest after indexing.
- Do not expose OCP-Dense pickle upload or arbitrary server-path registration to normal Web users.
- Keep trusted OCP-Dense and local-directory operations in the desktop/local-admin surface until a non-executable format and an explicit admin authorization model exist.
- Sanitize evidence responses so server paths from legacy provenance never reach the browser.

## Alternatives Considered

### Upload pickle files directly

Rejected because file extension, MIME and size checks cannot make pickle deserialization safe.

### Let users type server paths

Rejected because ownership of a Chalk account does not grant filesystem authority on the API host.

### Remove all computational evidence import

Rejected because structured text manifests are reviewable and can be handled safely within managed storage.

## Consequences

- Browser users retain the common manifest workflow and curated evidence import.
- Official pickle ingestion requires trusted local administration.
- A future Web OCP-Dense importer should use a non-executable interchange format or a separately sandboxed conversion service.

