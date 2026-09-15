"""Purpose-bound reference delivery; no API call here executes ComfyUI or approves imagery."""
import hmac
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.generation_node_routes import authenticate_node, encrypt_package, utc_now, LEASE_WINDOW
from app.api.routes import family_master_key
from app.api.short_scene_reference_job_routes import durable, view
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import FamilyArchive, MediaAsset, ShortSceneReferenceJob
from app.services.archive.assets import resolve_controlled_path, verify_asset_integrity
from app.services.auth import session_token_hash
from app.services.generation_capacity import lock_node_for_claim, node_has_unresolved_work
from app.services.security import SecretStore, decrypt_media_file, get_secret_store, media_context

router = APIRouter(prefix="/api/v1/generation-worker/short-reference", tags=["short-reference-worker"])
ACTIVE = ("preparing", "generating")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str | None = Field(default=None, min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    protocol: Literal["short-reference-v1"]


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["preparing", "generating", "failed"]
    percent: int = Field(ge=0, le=99)
    error_code: Literal["INPUT_INVALID", "GENERATION_FAILED", "OUTCOME_UNKNOWN"] | None = None


def reference_node(db, authorization):
    node, token = authenticate_node(db, authorization)
    if "scene_video" not in (node.capabilities or []):
        raise DomainError("SHORT_REFERENCE_CAPABILITY_REQUIRED", "节点没有场景生成授权。", 403)
    return node, token


def lease(db, node, job_id, token):
    job = db.get(ShortSceneReferenceJob, job_id)
    if (not job or job.assigned_node_id != node.id or job.status not in ACTIVE
            or not token or not job.lease_token_hash
            or not hmac.compare_digest(session_token_hash(token), job.lease_token_hash)):
        raise DomainError("SHORT_REFERENCE_LEASE_INVALID", "参考任务未由此节点持有有效租约。", 409)
    if job.lease_expires_at is None or job.lease_expires_at <= utc_now():
        raise DomainError("SHORT_REFERENCE_LEASE_EXPIRED", "参考任务已中断，请先核对原节点日志。", 409)
    return job


@router.post("/tasks/claim")
def claim(payload: Claim, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    node, node_secret = reference_node(db, authorization)
    lock_node_for_claim(db, node.id, utc_now())
    if payload.request_key:
        previous = db.scalar(select(ShortSceneReferenceJob).where(ShortSceneReferenceJob.claim_request_key == payload.request_key))
        if previous:
            token = hmac.new(node_secret.encode(), ("/api/v1/generation-worker/short-reference/tasks:" + payload.request_key + ":" + previous.id).encode(), "sha256").hexdigest()
            lease(db, node, previous.id, token)
            db.commit(); durable()
            return claim_view(previous, payload.protocol, token, previous.lease_expires_at)
    if node_has_unresolved_work(db, node.id, utc_now()):
        db.commit(); durable()
        return None
    job = db.scalar(select(ShortSceneReferenceJob).where(ShortSceneReferenceJob.status == "queued",
        ShortSceneReferenceJob.assigned_node_id.is_(None)).order_by(ShortSceneReferenceJob.created_at))
    if job is None:
        db.commit()
        return None
    token = hmac.new(node_secret.encode(), ("/api/v1/generation-worker/short-reference/tasks:" + payload.request_key + ":" + job.id).encode(), "sha256").hexdigest() if payload.request_key else secrets.token_urlsafe(32)
    expiry = utc_now() + LEASE_WINDOW
    changed = db.execute(update(ShortSceneReferenceJob).where(ShortSceneReferenceJob.id == job.id,
        ShortSceneReferenceJob.status == "queued", ShortSceneReferenceJob.assigned_node_id.is_(None)).values(
        status="preparing", assigned_node_id=node.id, lease_token_hash=session_token_hash(token),
        lease_expires_at=expiry, progress_percent=1, claim_request_key=payload.request_key))
    if changed.rowcount != 1:
        db.rollback()
        return None
    db.commit(); durable()
    return claim_view(job, payload.protocol, token, expiry)


def claim_view(job, protocol, token, expiry):
    return {"id": job.id, "protocol": protocol, "lease_token": token, "lease_expires_at": expiry,
        "brief_sha256": job.brief_sha256, "package_sha256": job.bundle_sha256,
        "package_url": f"/api/v1/generation-worker/short-reference/tasks/{job.id}/package",
        "purpose": "short_scene_reference_generation", "automatic_retry": False, "visual_accepted": False}


@router.get("/tasks/{job_id}/package")
def package(job_id: str, authorization: str | None = Header(default=None),
            lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
            db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    node, token = reference_node(db, authorization)
    job = lease(db, node, job_id, lease_token)
    asset, family = db.get(MediaAsset, job.package_asset_id), db.get(FamilyArchive, job.family_id)
    root = Path(tempfile.mkdtemp(prefix="lingnian-reference-delivery-"))
    try:
        if not asset or not family or asset.session_id != job.session_id or asset.kind != "short_reference_package":
            raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID", "参考素材关联不正确。", 409)
        path = resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path)
        if asset.encryption_version != 1 or not verify_asset_integrity(path, expected_size=asset.size_bytes, expected_sha256=asset.sha256):
            raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID", "参考素材完整性校验未通过。", 409)
        plain = root / "reference.zip"
        evidence = decrypt_media_file(path, plain, family_master_key(family, store), associated_data=media_context(family.id, asset.id))
        plain.chmod(0o600)
        if evidence.plaintext_sha256 != job.bundle_sha256 or evidence.plaintext_size != asset.plaintext_size_bytes:
            raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID", "参考素材内容已变化。", 409)
        encrypted = root / "reference.lnpkg"
        encrypt_package(plain, encrypted, worker_token=token, request_id=job.id)
        encrypted.chmod(0o600)
        # A slow decrypt must not turn an expired or concurrently cancelled lease into delivery.
        db.expire_all()
        node, _ = reference_node(db, authorization)
        lease(db, node, job_id, lease_token)
        return FileResponse(encrypted, media_type="application/vnd.lingnian.encrypted-package",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(shutil.rmtree, root, ignore_errors=True))
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        if isinstance(exc, DomainError):
            raise
        raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID", "参考素材暂时无法安全投递，未调用生成。", 409) from exc


@router.patch("/tasks/{job_id}/progress")
def progress(job_id: str, payload: Progress, authorization: str | None = Header(default=None),
             lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"), db: Session = Depends(get_db)):
    node, _ = reference_node(db, authorization)
    job = lease(db, node, job_id, lease_token)
    if (job.status == "generating" and payload.stage == "preparing") or ((payload.stage == "failed") != (payload.error_code is not None)):
        raise DomainError("SHORT_REFERENCE_PROGRESS_INVALID", "参考任务状态不能倒退或夹带不一致的错误信息。", 409)
    values = {"status": payload.stage, "progress_percent": max(job.progress_percent, payload.percent),
              "error_code": payload.error_code, "lease_expires_at": utc_now() + LEASE_WINDOW}
    if payload.stage == "failed":
        values.update(lease_token_hash=None, lease_expires_at=None)
    changed = db.execute(update(ShortSceneReferenceJob).where(ShortSceneReferenceJob.id == job.id,
        ShortSceneReferenceJob.status == job.status, ShortSceneReferenceJob.lease_token_hash == session_token_hash(lease_token),
        ShortSceneReferenceJob.lease_expires_at > utc_now()).values(**values))
    if changed.rowcount != 1:
        raise DomainError("SHORT_REFERENCE_LEASE_INVALID", "任务已取消或中断，未覆盖原状态。", 409)
    node.last_seen_at = utc_now()
    db.commit(); durable(); db.refresh(job)
    return view(job)
