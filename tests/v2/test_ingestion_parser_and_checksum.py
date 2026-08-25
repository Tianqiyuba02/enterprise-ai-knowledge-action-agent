from copy import deepcopy

import pytest
import yaml

from app.ingestion.checksum import calculate_source_checksum
from app.ingestion.errors import MetadataValidationError, SourceDocumentError
from app.ingestion.models import DocumentSourceStatus
from app.ingestion.parser import parse_source_text

BASE_METADATA = {
    "doc_code": "POL-HR-900",
    "version": "1.0",
    "title": "Synthetic Test Policy",
    "status": "approved",
    "effective_date": "2026-01-01",
    "expiry_date": None,
    "jurisdiction": "AU-VIC",
    "audience_groups": ["all_employees", "melbourne_employees"],
    "source_uri": "synthetic://tests/POL-HR-900/1.0",
}
BODY = "# Synthetic Test Policy\n\n## Scope\n\nThis policy is entirely fictitious.\n"


def _source(
    metadata: dict[str, object] | None = None,
    *,
    body: str = BODY,
    sort_keys: bool = False,
) -> str:
    front_matter = yaml.safe_dump(
        metadata or BASE_METADATA,
        sort_keys=sort_keys,
        allow_unicode=True,
    ).rstrip()
    return f"---\n{front_matter}\n---\n{body}"


@pytest.mark.parametrize("status", ["approved", "draft"])
def test_valid_approved_and_draft_metadata(status: str) -> None:
    metadata = {**BASE_METADATA, "status": status}

    document = parse_source_text(_source(metadata))

    assert document.metadata.status is DocumentSourceStatus(status)
    assert document.metadata.doc_code == "POL-HR-900"
    assert document.body.endswith("\n")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "withdrawn"),
        ("jurisdiction", "AU-QLD"),
        ("audience_groups", ["contractors"]),
        ("audience_groups", []),
        ("expiry_date", "2026-01-01"),
    ],
)
def test_invalid_controlled_metadata_is_rejected(field: str, value: object) -> None:
    metadata = {**BASE_METADATA, field: value}

    with pytest.raises(MetadataValidationError):
        parse_source_text(_source(metadata))


def test_unknown_metadata_field_is_rejected() -> None:
    metadata = {**BASE_METADATA, "unexpected": "not allowed"}

    with pytest.raises(MetadataValidationError):
        parse_source_text(_source(metadata))


@pytest.mark.parametrize(
    "supersedes",
    [
        {"doc_code": "POL-HR-900"},
        {"doc_code": "POL-HR-901", "version": "1.0"},
        {"doc_code": "POL-HR-900", "version": "1.0"},
    ],
)
def test_invalid_supersedes_identity_is_rejected(
    supersedes: dict[str, str],
) -> None:
    metadata = {
        **BASE_METADATA,
        "version": "1.0",
        "supersedes": supersedes,
    }

    with pytest.raises(MetadataValidationError):
        parse_source_text(_source(metadata))


def test_draft_cannot_declare_supersession() -> None:
    metadata = {
        **BASE_METADATA,
        "status": "draft",
        "version": "2.0",
        "supersedes": {"doc_code": "POL-HR-900", "version": "1.0"},
    }

    with pytest.raises(MetadataValidationError):
        parse_source_text(_source(metadata))


@pytest.mark.parametrize(
    "source",
    [
        BODY,
        "---\ndoc_code: [unterminated\n---\nBody\n",
        "---\n- not\n- a\n- mapping\n---\nBody\n",
        _source(body=" \n\t\n"),
    ],
)
def test_malformed_missing_or_empty_source_is_rejected(source: str) -> None:
    with pytest.raises(SourceDocumentError):
        parse_source_text(source)


def test_checksum_is_deterministic_and_ignores_yaml_and_audience_order() -> None:
    first = parse_source_text(_source(BASE_METADATA, sort_keys=False), source_name="first.md")
    reordered = deepcopy(BASE_METADATA)
    reordered["audience_groups"] = ["melbourne_employees", "all_employees"]
    second = parse_source_text(_source(reordered, sort_keys=True), source_name="second.md")

    assert calculate_source_checksum(first) == calculate_source_checksum(second)


def test_checksum_normalizes_line_endings_and_trailing_newline() -> None:
    lf = parse_source_text(_source(body=BODY), source_name="lf.md")
    crlf_source = _source(body=BODY.rstrip("\n")).replace("\n", "\r\n")
    crlf = parse_source_text(crlf_source, source_name="crlf.md")

    assert calculate_source_checksum(lf) == calculate_source_checksum(crlf)


def test_body_change_changes_checksum() -> None:
    original = parse_source_text(_source())
    changed = parse_source_text(_source(body=BODY.replace("fictitious", "synthetic")))

    assert calculate_source_checksum(original) != calculate_source_checksum(changed)


def test_authority_metadata_change_changes_checksum() -> None:
    original = parse_source_text(_source())
    changed = parse_source_text(_source({**BASE_METADATA, "jurisdiction": "GLOBAL"}))

    assert calculate_source_checksum(original) != calculate_source_checksum(changed)


def test_source_location_does_not_change_checksum() -> None:
    first = parse_source_text(_source(), source_name="/old/location/policy.md")
    relocated_metadata = {
        **BASE_METADATA,
        "source_uri": "synthetic://relocated/POL-HR-900/1.0",
    }
    second = parse_source_text(
        _source(relocated_metadata),
        source_name="/new/location/policy.md",
    )

    assert calculate_source_checksum(first) == calculate_source_checksum(second)
