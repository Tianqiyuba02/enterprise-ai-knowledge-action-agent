import re

from app.ingestion.chunking import HeadingAwareChunker, count_tokens
from app.ingestion.parser import parse_source_text

FRONT_MATTER = """---
doc_code: POL-TEST-001
version: "1.0"
title: Chunking Test Policy
status: approved
effective_date: 2026-01-01
expiry_date:
jurisdiction: GLOBAL
audience_groups:
  - all_employees
source_uri: synthetic://tests/POL-TEST-001/1.0
---
"""


def _document(body: str):
    return parse_source_text(f"{FRONT_MATTER}{body}")


def test_heading_aware_chunks_are_deterministic_and_preserve_sections() -> None:
    document = _document(
        """# Policy

Opening text.

## Scope

Scope text.

## Responsibilities

Responsibility text.
"""
    )
    chunker = HeadingAwareChunker()

    first = chunker.chunk(document)
    second = chunker.chunk(document)

    assert first == second
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))
    assert [chunk.section_label for chunk in first] == [
        "Policy",
        "Scope",
        "Responsibilities",
    ]
    assert [chunk.anchor for chunk in first] == ["policy", "scope", "responsibilities"]
    assert first[1].content.startswith("## Scope")
    assert all(chunk.content.strip() and chunk.token_count > 0 for chunk in first)


def test_duplicate_headings_receive_stable_distinct_anchors() -> None:
    document = _document(
        """## Scope

First scope.

## Scope

Second scope.
"""
    )

    chunks = HeadingAwareChunker().chunk(document)

    assert [chunk.anchor for chunk in chunks] == ["scope", "scope-2"]


def test_long_section_uses_target_window_and_exact_overlap() -> None:
    words = [f"word{index}" for index in range(25)]
    document = _document(f"## Long Section\n\n{' '.join(words)}\n")
    chunker = HeadingAwareChunker(target_tokens=12, overlap_tokens=3)

    chunks = chunker.chunk(document)

    assert len(chunks) == 4
    assert all(chunk.token_count <= 12 for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    first_words = re.findall(r"word\d+", chunks[0].content)
    second_words = re.findall(r"word\d+", chunks[1].content)
    assert first_words[-3:] == second_words[:3]


def test_token_count_uses_documented_non_whitespace_units() -> None:
    assert count_tokens("one  two\nthree\tfour") == 4


def test_heading_without_body_still_produces_nonempty_chunk() -> None:
    document = _document("# Policy\n\n## Empty Section\n")

    chunks = HeadingAwareChunker().chunk(document)

    assert chunks[-1].section_label == "Empty Section"
    assert chunks[-1].content == "## Empty Section"
    assert chunks[-1].token_count == 3
