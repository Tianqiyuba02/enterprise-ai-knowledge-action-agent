"""Small deterministic Markdown plus YAML-front-matter parser."""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.ingestion.errors import MetadataValidationError, SourceDocumentError
from app.ingestion.models import DocumentMetadata, SourceDocument


def normalize_markdown_body(body: str) -> str:
    """Normalize line endings, trailing spaces, and the final line ending."""

    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def parse_source_text(text: str, *, source_name: str = "<memory>") -> SourceDocument:
    """Parse one UTF-8 Markdown source string into a strict typed document."""

    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SourceDocumentError(f"{source_name}: missing YAML front matter")

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise SourceDocumentError(f"{source_name}: unterminated YAML front matter")

    front_matter = "\n".join(lines[1:closing_index])
    try:
        raw_metadata = yaml.safe_load(front_matter)
    except yaml.YAMLError as exc:
        raise SourceDocumentError(f"{source_name}: malformed YAML front matter") from exc
    if not isinstance(raw_metadata, Mapping):
        raise MetadataValidationError(f"{source_name}: front matter must be a mapping")

    try:
        metadata = DocumentMetadata.model_validate(dict(raw_metadata))
    except ValidationError as exc:
        raise MetadataValidationError(f"{source_name}: invalid document metadata") from exc

    body = normalize_markdown_body("\n".join(lines[closing_index + 1 :]))
    if not body.strip():
        raise SourceDocumentError(f"{source_name}: Markdown body must not be empty")

    return SourceDocument(metadata=metadata, body=body, source_name=source_name)


def parse_source_file(path: Path) -> SourceDocument:
    """Read and parse one UTF-8 corpus file."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SourceDocumentError(f"{path}: source document could not be read as UTF-8") from exc
    return parse_source_text(text, source_name=str(path))
