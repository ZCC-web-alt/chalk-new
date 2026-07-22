from __future__ import annotations

"""Materialize protected Science 125 Canary inputs for a single CI run.

The authoritative booklet context and reviewed evidence stay out of Git. GitHub
Environment secrets carry a gzip-compressed, split context index and a separate
reviewed-evidence payload; this script reconstructs them only in the ignored
runtime data directory and validates the index before a model call is possible.
"""

import argparse
import base64
import gzip
import io
import json
import os
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


CONTEXT_PART_1 = "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART1"
CONTEXT_PART_2 = "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART2"
EVIDENCE_B64 = "SCIENCE125_CANARY_REVIEWED_EVIDENCE_B64"
MAX_CONTEXT_BYTES = 1_000_000
MAX_EVIDENCE_BYTES = 1_000_000


class CanaryInputError(RuntimeError):
    pass


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise CanaryInputError(f"Required protected Canary input is missing: {name}.")
    return value


def _decode_base64(value: str, *, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise CanaryInputError(f"Protected Canary {label} is not valid base64.") from exc


def _gunzip_limited(payload: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as archive:
            value = archive.read(MAX_CONTEXT_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise CanaryInputError("Protected Canary context index is not valid gzip data.") from exc
    if len(value) > MAX_CONTEXT_BYTES:
        raise CanaryInputError("Protected Canary context index exceeds its allowed size.")
    return value


def _decode_utf8_json(payload: bytes, *, label: str, max_bytes: int) -> str:
    if len(payload) > max_bytes:
        raise CanaryInputError(f"Protected Canary {label} exceeds its allowed size.")
    try:
        text = payload.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanaryInputError(f"Protected Canary {label} is not valid UTF-8 JSON.") from exc
    if not isinstance(parsed, dict):
        raise CanaryInputError(f"Protected Canary {label} must be a JSON object.")
    return text


def _atomic_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    return temporary


def _validate_context(path: Path) -> None:
    from app.services.science125_context import load_science125_context_index

    load_science125_context_index(path)


def materialize_from_environment(
    environ: Mapping[str, str],
    *,
    context_path: Path,
    evidence_path: Path,
    validate_context: Callable[[Path], None] = _validate_context,
) -> None:
    context_b64 = _required(environ, CONTEXT_PART_1) + _required(environ, CONTEXT_PART_2)
    context_text = _decode_utf8_json(
        _gunzip_limited(_decode_base64(context_b64, label="context index")),
        label="context index",
        max_bytes=MAX_CONTEXT_BYTES,
    )
    evidence_text = _decode_utf8_json(
        _decode_base64(_required(environ, EVIDENCE_B64), label="reviewed evidence"),
        label="reviewed evidence",
        max_bytes=MAX_EVIDENCE_BYTES,
    )

    context_temporary: Path | None = None
    evidence_temporary: Path | None = None
    try:
        context_temporary = _atomic_write(context_path, context_text)
        evidence_temporary = _atomic_write(evidence_path, evidence_text)
        validate_context(context_temporary)
        context_temporary.replace(context_path)
        context_temporary = None
        evidence_temporary.replace(evidence_path)
        evidence_temporary = None
    finally:
        if context_temporary is not None:
            context_temporary.unlink(missing_ok=True)
        if evidence_temporary is not None:
            evidence_temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize protected Science 125 Canary inputs for one run."
    )
    parser.add_argument(
        "--context-output",
        type=Path,
        default=PROJECT_ROOT / "data" / "science125" / "science125-context-v1.json",
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=PROJECT_ROOT / "data" / "canary-reviewed-evidence.json",
    )
    args = parser.parse_args(argv)
    try:
        materialize_from_environment(
            os.environ,
            context_path=args.context_output.resolve(),
            evidence_path=args.evidence_output.resolve(),
        )
    except CanaryInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("Protected Science 125 Canary inputs were materialized and validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
