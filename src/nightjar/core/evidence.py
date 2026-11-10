from __future__ import annotations

import hashlib
import json
from typing import Any

from nightjar.core.ids import ensure_utc
from nightjar.core.models import ChainVerification, EvidenceItem, TimelineEvent


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def link_hash(prev_hash: str, payload: Any) -> str:
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("ascii"))
    digest.update(b"\n")
    digest.update(canonical_json(payload))
    return digest.hexdigest()


def evidence_payload(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "incident_id": item.incident_id,
        "kind": item.kind.value,
        "source": item.source,
        "summary": item.summary,
        "content": item.content,
        "observed_at": ensure_utc(item.observed_at).isoformat(timespec="microseconds"),
        "collected_by": item.collected_by,
    }


def seal_evidence(item: EvidenceItem, prev_hash: str) -> EvidenceItem:
    payload = evidence_payload(item)
    digest = content_hash(payload)
    sealed = item.model_copy(
        update={
            "content_hash": digest,
            "prev_hash": prev_hash,
            "hash": link_hash(prev_hash, {"content_hash": digest, "payload": payload}),
        }
    )
    return sealed


def verify_evidence_chain(items: list[EvidenceItem]) -> ChainVerification:
    prev = ""
    for index, item in enumerate(items):
        payload = evidence_payload(item)
        expected_content = content_hash(payload)
        if item.content_hash != expected_content:
            return ChainVerification(
                valid=False,
                checked=index,
                broken_at=item.id,
                reason="content hash mismatch",
            )
        expected_link = link_hash(prev, {"content_hash": expected_content, "payload": payload})
        if item.hash != expected_link or item.prev_hash != prev:
            return ChainVerification(
                valid=False,
                checked=index,
                broken_at=item.id,
                reason="chain link mismatch",
            )
        prev = item.hash
    return ChainVerification(valid=True, checked=len(items))


def event_payload(event: TimelineEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "incident_id": event.incident_id,
        "seq": event.seq,
        "event_type": event.event_type.value,
        "actor": event.actor,
        "payload": event.payload,
        "created_at": ensure_utc(event.created_at).isoformat(timespec="microseconds"),
    }


def seal_event(event: TimelineEvent, prev_hash: str) -> TimelineEvent:
    payload = event_payload(event)
    return event.model_copy(
        update={
            "prev_hash": prev_hash,
            "hash": link_hash(prev_hash, payload),
        }
    )


def verify_event_chain(events: list[TimelineEvent]) -> ChainVerification:
    prev = ""
    for index, event in enumerate(events):
        expected = link_hash(prev, event_payload(event))
        if event.hash != expected or event.prev_hash != prev:
            return ChainVerification(
                valid=False,
                checked=index,
                broken_at=event.id,
                reason="timeline chain mismatch",
            )
        prev = event.hash
    return ChainVerification(valid=True, checked=len(events))
