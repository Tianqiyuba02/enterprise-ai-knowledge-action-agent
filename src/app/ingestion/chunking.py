"""Deterministic heading-aware Markdown chunking without a tokenizer dependency."""

import re
import unicodedata
from dataclasses import dataclass

from app.ingestion.errors import ChunkingError
from app.ingestion.models import DocumentChunk, SourceDocument

DEFAULT_TARGET_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 50

_HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_TOKEN_PATTERN = re.compile(r"\S+")
_NON_ANCHOR_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class _Section:
    label: str
    anchor: str
    heading_line: str | None
    body: str


def count_tokens(text: str) -> int:
    """Count deterministic non-whitespace lexical units, not model-specific tokens."""

    return sum(1 for _match in _TOKEN_PATTERN.finditer(text))


class HeadingAwareChunker:
    """Split each Markdown section into stable overlapping lexical-token windows."""

    def __init__(
        self,
        *,
        target_tokens: int = DEFAULT_TARGET_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be nonnegative and less than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, document: SourceDocument) -> tuple[DocumentChunk, ...]:
        chunks: list[DocumentChunk] = []
        for section in _extract_sections(document.body):
            chunks.extend(self._chunk_section(section, start_index=len(chunks)))
        if not chunks:
            raise ChunkingError("The source document did not produce any nonempty chunks.")
        return tuple(chunks)

    def _chunk_section(
        self,
        section: _Section,
        *,
        start_index: int,
    ) -> list[DocumentChunk]:
        heading = section.heading_line
        heading_tokens = count_tokens(heading or "")
        body_matches = list(_TOKEN_PATTERN.finditer(section.body))

        if not body_matches:
            if heading is None:
                return []
            return [
                _build_chunk(
                    chunk_index=start_index,
                    section=section,
                    content=heading,
                )
            ]

        content_budget = self.target_tokens - heading_tokens
        if content_budget <= self.overlap_tokens:
            raise ChunkingError(
                f"Section heading '{section.label}' leaves no safe overlapping token window."
            )

        chunks: list[DocumentChunk] = []
        start_token = 0
        while start_token < len(body_matches):
            end_token = min(start_token + content_budget, len(body_matches))
            start_character = body_matches[start_token].start()
            end_character = body_matches[end_token - 1].end()
            body_slice = section.body[start_character:end_character].strip()
            content = f"{heading}\n\n{body_slice}" if heading else body_slice
            chunks.append(
                _build_chunk(
                    chunk_index=start_index + len(chunks),
                    section=section,
                    content=content,
                )
            )
            if end_token == len(body_matches):
                break
            start_token = end_token - self.overlap_tokens

        return chunks


def _build_chunk(*, chunk_index: int, section: _Section, content: str) -> DocumentChunk:
    normalized_content = content.strip()
    if not normalized_content:
        raise ChunkingError(f"Section '{section.label}' produced an empty chunk.")
    return DocumentChunk(
        chunk_index=chunk_index,
        section_label=section.label,
        anchor=section.anchor,
        content=normalized_content,
        token_count=count_tokens(normalized_content),
    )


def _extract_sections(body: str) -> tuple[_Section, ...]:
    sections: list[_Section] = []
    anchor_counts: dict[str, int] = {}
    current_label = "Preamble"
    current_heading: str | None = None
    current_lines: list[str] = []

    def append_current() -> None:
        content = "\n".join(current_lines).strip()
        if current_heading is None and not content:
            return
        base_anchor = _slugify(current_label)
        occurrence = anchor_counts.get(base_anchor, 0) + 1
        anchor_counts[base_anchor] = occurrence
        anchor = base_anchor if occurrence == 1 else f"{base_anchor}-{occurrence}"
        sections.append(
            _Section(
                label=current_label,
                anchor=anchor,
                heading_line=current_heading,
                body=content,
            )
        )

    for line in body.splitlines():
        heading_match = _HEADING_PATTERN.match(line)
        if heading_match is None:
            current_lines.append(line)
            continue
        append_current()
        current_label = heading_match.group(2).strip().rstrip("#").strip()
        current_heading = line.strip()
        current_lines = []

    append_current()
    return tuple(sections)


def _slugify(label: str) -> str:
    ascii_label = (
        unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = _NON_ANCHOR_PATTERN.sub("-", ascii_label).strip("-")
    return slug or "section"
