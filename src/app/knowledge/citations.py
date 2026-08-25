"""Trusted public citation construction from one exact retrieved evidence set."""

from app.api.knowledge_models import KnowledgeCitation, KnowledgeQueryResponse
from app.grounding.client import InvalidGroundedResponseError
from app.grounding.models import GroundedAnswerDraft, KnowledgeAnswerStatus
from app.grounding.references import ReferencedEvidence


def build_knowledge_response(
    draft: GroundedAnswerDraft,
    referenced_evidence: tuple[ReferencedEvidence, ...],
) -> KnowledgeQueryResponse:
    evidence_by_reference = {item.reference: item.evidence for item in referenced_evidence}
    ordered_references = tuple(dict.fromkeys(draft.evidence_refs))
    unknown_references = [
        reference for reference in ordered_references if reference not in evidence_by_reference
    ]
    if unknown_references:
        raise InvalidGroundedResponseError(
            "Grounded generation referenced evidence outside the retrieved set."
        )

    selected_evidence = tuple(evidence_by_reference[reference] for reference in ordered_references)
    if draft.status is KnowledgeAnswerStatus.CONFLICTING_EVIDENCE:
        document_identities = {
            (evidence.doc_code, evidence.version) for evidence in selected_evidence
        }
        if len(document_identities) < 2:
            raise InvalidGroundedResponseError(
                "Conflicting evidence must reference two distinct documents."
            )

    citations: list[KnowledgeCitation] = []
    seen_citations: set[tuple[str, str, str, str, int | None]] = set()
    for evidence in selected_evidence:
        identity = (
            evidence.doc_code,
            evidence.title,
            evidence.version,
            evidence.anchor,
            evidence.page,
        )
        if identity in seen_citations:
            continue
        seen_citations.add(identity)
        citations.append(
            KnowledgeCitation(
                doc_code=evidence.doc_code,
                title=evidence.title,
                version=evidence.version,
                section_anchor=evidence.anchor,
                page=evidence.page,
            )
        )

    return KnowledgeQueryResponse(
        status=draft.status,
        answer=draft.answer,
        citations=tuple(citations),
    )
