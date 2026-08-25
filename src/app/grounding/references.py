"""Bounded opaque references for one exact retrieved evidence set."""

from dataclasses import dataclass

from app.knowledge.models import RetrievedEvidence


@dataclass(frozen=True, slots=True)
class ReferencedEvidence:
    reference: str
    evidence: RetrievedEvidence


def assign_evidence_references(
    evidence: tuple[RetrievedEvidence, ...],
) -> tuple[ReferencedEvidence, ...]:
    if len(evidence) > 6:
        raise ValueError("Grounded generation accepts at most six evidence items.")
    return tuple(
        ReferencedEvidence(reference=f"E{index}", evidence=item)
        for index, item in enumerate(evidence, start=1)
    )
