"""Canonical immutable-source checksum for V2 policy documents."""

import hashlib
import json

from app.ingestion.models import SourceDocument
from app.ingestion.parser import normalize_markdown_body


def canonical_source_payload(document: SourceDocument) -> bytes:
    """Return canonical UTF-8 bytes excluding source location and runtime/index data."""

    metadata = document.metadata
    supersedes = (
        {
            "doc_code": metadata.supersedes.doc_code,
            "version": metadata.supersedes.version,
        }
        if metadata.supersedes is not None
        else None
    )
    canonical = {
        "audience_groups": sorted(group.value for group in metadata.audience_groups),
        "body": normalize_markdown_body(document.body),
        "declared_source_status": metadata.status.value,
        "doc_code": metadata.doc_code,
        "effective_date": metadata.effective_date.isoformat(),
        "expiry_date": metadata.expiry_date.isoformat() if metadata.expiry_date else None,
        "jurisdiction": metadata.jurisdiction.value,
        "supersedes": supersedes,
        "title": metadata.title,
        "version": metadata.version,
    }
    return json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def calculate_source_checksum(document: SourceDocument) -> str:
    """Calculate the lowercase SHA-256 digest of canonical immutable source data."""

    return hashlib.sha256(canonical_source_payload(document)).hexdigest()
