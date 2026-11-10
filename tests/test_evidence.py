from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nightjar.core.enums import EventType, EvidenceKind
from nightjar.core.evidence import (
    canonical_json,
    content_hash,
    seal_event,
    seal_evidence,
    verify_event_chain,
    verify_evidence_chain,
)
from nightjar.core.models import EvidenceItem, TimelineEvent


def item(incident_id: str, summary: str, *, content: dict | None = None) -> EvidenceItem:
    return EvidenceItem(
        incident_id=incident_id,
        kind=EvidenceKind.HTTP_RESPONSE,
        source="test",
        summary=summary,
        content=content if content is not None else {"status": 200},
        observed_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def event(incident_id: str, seq: int, payload: dict | None = None) -> TimelineEvent:
    return TimelineEvent(
        incident_id=incident_id,
        seq=seq,
        event_type=EventType.NOTE,
        actor="tester",
        payload=payload if payload is not None else {"seq": seq},
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def chain(incident_id: str, summaries: list[str]) -> list[EvidenceItem]:
    sealed: list[EvidenceItem] = []
    previous = ""
    for summary in summaries:
        linked = seal_evidence(item(incident_id, summary), previous)
        sealed.append(linked)
        previous = linked.hash
    return sealed


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})


def test_evidence_chain_verifies() -> None:
    sealed = chain("incident-1", ["first", "second", "third"])
    assert [entry.prev_hash for entry in sealed[1:]] == [entry.hash for entry in sealed[:-1]]
    assert len({entry.content_hash for entry in sealed}) == 3
    verification = verify_evidence_chain(sealed)
    assert verification.valid is True
    assert verification.checked == 3


def test_evidence_content_tamper_is_detected() -> None:
    sealed = chain("incident-1", ["first", "second"])
    tampered = sealed[1].model_copy(update={"summary": "rewritten"})
    verification = verify_evidence_chain([sealed[0], tampered])
    assert verification.valid is False
    assert verification.broken_at == tampered.id
    assert verification.reason == "content hash mismatch"


def test_evidence_link_tamper_is_detected() -> None:
    sealed = chain("incident-1", ["first", "second"])
    tampered = sealed[1].model_copy(update={"prev_hash": "0" * 64})
    verification = verify_evidence_chain([sealed[0], tampered])
    assert verification.valid is False
    assert verification.reason == "chain link mismatch"


def test_evidence_reorder_is_detected() -> None:
    sealed = chain("incident-1", ["first", "second", "third"])
    verification = verify_evidence_chain([sealed[0], sealed[2], sealed[1]])
    assert verification.valid is False
    assert verification.broken_at == sealed[2].id


def test_empty_chain_is_valid() -> None:
    assert verify_evidence_chain([]).valid is True
    assert verify_event_chain([]).valid is True


def test_timeline_chain_verifies() -> None:
    sealed: list[TimelineEvent] = []
    previous = ""
    for seq in (1, 2, 3):
        linked = seal_event(event("incident-1", seq), previous)
        sealed.append(linked)
        previous = linked.hash
    assert verify_event_chain(sealed).valid is True

    tampered = sealed[2].model_copy(update={"payload": {"seq": "changed"}})
    verification = verify_event_chain([*sealed[:2], tampered])
    assert verification.valid is False
    assert verification.broken_at == tampered.id


def test_sealing_is_deterministic_for_the_same_payload() -> None:
    entry = item("incident-1", "same")
    first = seal_evidence(entry, "")
    second = seal_evidence(entry, "")
    # Sealing the same evidence twice must produce the same hash.
    assert first.hash == second.hash


def test_observed_at_is_normalized_to_utc() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    entry = EvidenceItem(
        incident_id="incident-1",
        kind=EvidenceKind.LOG,
        source="test",
        summary="log line",
        observed_at=naive,
    )
    first = seal_evidence(entry, "")
    # Naive timestamps are treated as UTC, not local time.
    utc_entry = entry.model_copy(update={"observed_at": naive.replace(tzinfo=UTC) + timedelta(0)})
    assert first.hash == seal_evidence(utc_entry, "").hash
