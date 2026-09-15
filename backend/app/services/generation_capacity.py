"""Serialize server-side claims across queues; this is not a GPU utilization probe."""
from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models import GenerationNode, GenerativeMediaRequest, ShortSceneJob, ShortSceneReferenceJob


def lock_node_for_claim(db: Session, node_id: str, now: datetime) -> None:
    # Hold this row's write lock until the caller commits its claim/no-work result.
    held = db.execute(update(GenerationNode).where(
        GenerationNode.id == node_id, GenerationNode.status == "active",
        GenerationNode.revoked_at.is_(None)).values(last_seen_at=now))
    if held.rowcount != 1:
        raise DomainError("GENERATION_NODE_AUTH_INVALID", "生成节点已撤销。", 401)


def node_has_unresolved_work(db: Session, node_id: str, now: datetime) -> bool:
    # Losing a heartbeat does not prove the remote prompt has stopped. Preserve
    # node ownership and require deliberate recovery, never automatic requeue.
    db.execute(update(GenerativeMediaRequest).where(
        GenerativeMediaRequest.assigned_node_id == node_id,
        GenerativeMediaRequest.status == "processing",
        or_(GenerativeMediaRequest.lease_expires_at.is_(None),
            GenerativeMediaRequest.lease_expires_at <= now)).values(
        status="failed", lease_token_hash=None, error_code="GENERATION_LEASE_EXPIRED",
        progress_stage="节点结果未确认，暂停领取新任务",
        last_error_message="租约已过期；请先核对节点上的原任务，不会自动重新生成。"))
    db.execute(update(ShortSceneJob).where(
        ShortSceneJob.assigned_node_id == node_id,
        ShortSceneJob.status.in_(("preparing", "generating")),
        or_(ShortSceneJob.lease_expires_at.is_(None), ShortSceneJob.lease_expires_at <= now)).values(
        status="interrupted", error_code="LEASE_EXPIRED", lease_token_hash=None))
    legacy = db.scalar(select(GenerativeMediaRequest.id).where(
        GenerativeMediaRequest.assigned_node_id == node_id,
        or_(GenerativeMediaRequest.status == "processing",
            and_(GenerativeMediaRequest.status == "failed",
                 GenerativeMediaRequest.error_code == "GENERATION_LEASE_EXPIRED"))))
    short = db.scalar(select(ShortSceneJob.id).where(
        ShortSceneJob.assigned_node_id == node_id,
        or_(ShortSceneJob.status.in_(("preparing", "generating", "interrupted")),
            and_(ShortSceneJob.status == "failed", ShortSceneJob.error_code == "OUTCOME_UNKNOWN"))))
    db.execute(update(ShortSceneReferenceJob).where(
        ShortSceneReferenceJob.assigned_node_id == node_id,
        ShortSceneReferenceJob.status.in_(("preparing", "generating")),
        or_(ShortSceneReferenceJob.lease_expires_at.is_(None), ShortSceneReferenceJob.lease_expires_at <= now)).values(
        status="interrupted", error_code="LEASE_EXPIRED", lease_token_hash=None))
    reference = db.scalar(select(ShortSceneReferenceJob.id).where(
        ShortSceneReferenceJob.assigned_node_id == node_id,
        or_(ShortSceneReferenceJob.status.in_(("preparing", "generating", "interrupted")),
            and_(ShortSceneReferenceJob.status == "failed", ShortSceneReferenceJob.error_code == "OUTCOME_UNKNOWN"))))
    return legacy is not None or short is not None or reference is not None
