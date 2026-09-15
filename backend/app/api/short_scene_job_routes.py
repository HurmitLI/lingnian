"""Dedicated native-10s queue, with encrypted delivery and no automatic visual approval."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import re
import subprocess
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.generation_node_routes import authenticate_node, encrypt_package, utc_now, LEASE_WINDOW
from app.api.routes import encrypt_asset_if_needed, family_master_key, protect_values, require
from app.api.short_scene_export_routes import ExportConsent, export_short_scene_package
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import FamilyArchive, MediaAsset, MemorySession, ModelConsentEvent, ShortSceneJob, ShortScenePlan
from app.models.entities import now_utc
from app.services.archive.assets import resolve_controlled_path, verify_asset_integrity, store_video_upload
from app.services.asr.audio import get_ffmpeg_binary
from app.services.auth import current_auth, session_token_hash
from app.services.database_snapshot import persist_configured_database_snapshot
from app.services.generation_capacity import lock_node_for_claim, node_has_unresolved_work
from app.services.memory.short_scene_bundle import LIMITS
from app.services.security import SecretStore, decrypt_media_file, get_secret_store, media_context

router = APIRouter(prefix="/api/v1", tags=["short-scene-jobs"])
ACTIVE = ("preparing", "generating")


class JobConsent(ExportConsent):
    authorize_node_delivery: StrictBool
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str | None = Field(default=None, min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    protocol: Literal["native-short-scene-v1"]


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorize_resume: StrictBool
    node_checked_no_gpu_submission: StrictBool = False


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["preparing", "generating", "awaiting_input_review", "failed"]
    percent: int = Field(ge=0, le=99)
    error_code: Literal["INPUT_INVALID", "GENERATION_FAILED", "OUTCOME_UNKNOWN"] | None = None


def durable():
    if get_settings().formal_auth_required:
        try:
            if persist_configured_database_snapshot(get_settings()) is None:
                raise RuntimeError()
        except Exception as exc:
            raise DomainError("SHORT_JOB_DURABILITY_FAILED", "任务状态尚未安全保存，暂不继续发送。", 503) from exc


def view(job):
    status = job.status
    if status in ACTIVE and (not job.lease_expires_at or job.lease_expires_at < utc_now()):
        status = "interrupted"
    return {"id": job.id, "session_id": job.session_id, "plan_id": job.plan_id, "status": status,
            "progress_percent": job.progress_percent, "error_code": job.error_code,
            "automatic_retry": False, "visual_accepted": False,
            "result_asset_id": job.result_asset_id, "created_at": job.created_at,
            "input_review_available": bool(job.input_review)}


@router.post("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/jobs")
def create_job(session_id: str, plan_id: str, consent: str = Form(...), reference: UploadFile = File(...),
               db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    return create_job_internal(session_id, plan_id, consent, reference, db, store)


def create_job_internal(session_id, plan_id, consent, reference, db, store, *, input_review=None):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    family = session.elder.person.family
    try:
        options = JobConsent.model_validate_json(consent)
    except ValidationError as exc:
        raise DomainError("SHORT_JOB_CONSENT_INVALID", "制作任务授权格式不完整。", 422) from exc
    if not all((options.authorize_node_delivery, options.authorize_material_export, options.reference_rights_confirmed,
                options.subject_consent, options.no_impersonation)):
        raise DomainError("SHORT_JOB_CONSENT_REQUIRED", "需要明确授权本次把原声和参考图交给生成节点。", 409)
    digest = hashlib.sha256()
    count = 0
    while chunk := reference.file.read(1024 * 1024):
        count += len(chunk)
        if count > LIMITS["reference.png"]:
            raise DomainError("SHORT_JOB_REFERENCE_TOO_LARGE", "参考图过大。", 413)
        digest.update(chunk)
    reference.file.seek(0)
    fingerprint = hashlib.sha256(json.dumps({"session": session_id, "plan": plan_id,
        "input": options.input_sha256, "reference": digest.hexdigest()}, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(ShortSceneJob).where(ShortSceneJob.family_id == family.id,
                                                    ShortSceneJob.idempotency_key == options.idempotency_key))
    if existing:
        if existing.input_sha256 != fingerprint:
            raise DomainError("SHORT_JOB_KEY_CONFLICT", "本次请求已绑定其他素材，不能覆盖。", 409)
        durable()
        return view(existing)
    if family_master_key(family, store) is None:
        raise DomainError("SHORT_JOB_ENCRYPTION_REQUIRED", "节点制作包必须先启用家庭加密。", 409)
    exported = export_short_scene_package(session_id, plan_id,
        json.dumps(options.model_dump(exclude={"authorize_node_delivery", "idempotency_key"})), reference, db, store)
    temporary = Path(exported.path).parent
    stored_path = None
    committed = False
    try:
        asset_id = str(uuid4())
        relative = f"assets/derived/{session_id}/{asset_id}.lnpkg"
        stored_path = resolve_controlled_path(get_settings().resolved_asset_root, relative)
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        # Never persist a cleartext package outside the mode-700 temporary directory.
        from app.services.security import encrypt_media_file
        evidence = encrypt_media_file(Path(exported.path), stored_path, family_master_key(family, store),
                                      associated_data=media_context(family.id, asset_id))
        asset = MediaAsset(id=asset_id, session_id=session_id, kind="short_scene_package", relative_path=relative,
            original_filename="short-scene.lnpkg", mime_type="application/vnd.lingnian.encrypted-package",
            size_bytes=evidence.ciphertext_size, sha256=evidence.ciphertext_sha256, status="internal_only", is_original=False,
            encryption_version=1, plaintext_size_bytes=evidence.plaintext_size, plaintext_sha256=evidence.plaintext_sha256)
        context = current_auth.get()
        event = ModelConsentEvent(family_id=family.id, session_id=session_id, purpose="short_scene_node_delivery",
            actor_label=f"user:{context.user_id}" if context else "本机用户", decision="granted", one_time=True,
            used_at=now_utc(), input_sha256=evidence.plaintext_sha256, data_classification=family.data_classification)
        db.add_all([asset, event]); db.flush()
        protect_values(db, family, event, {"actor_label": event.actor_label}, store)
        job = ShortSceneJob(family_id=family.id, session_id=session_id, plan_id=plan_id, package_asset_id=asset.id,
            consent_id=event.id, idempotency_key=options.idempotency_key, input_sha256=fingerprint,
            bundle_sha256=evidence.plaintext_sha256, status="queued", input_review=input_review or {},
            reference_review_sha256=input_review["review_input_sha256"] if input_review else None)
        db.add(job); db.commit(); committed = True
        durable()
        return view(job)
    except IntegrityError:
        db.rollback()
        winner = db.scalar(select(ShortSceneJob).where(ShortSceneJob.family_id == family.id,
                                                      ShortSceneJob.idempotency_key == options.idempotency_key))
        if winner is None and input_review:
            winner = db.scalar(select(ShortSceneJob).where(ShortSceneJob.family_id == family.id,
                ShortSceneJob.reference_review_sha256 == input_review["review_input_sha256"]))
        if winner and winner.input_sha256 == fingerprint:
            durable()
            return view(winner)
        raise DomainError("SHORT_JOB_KEY_CONFLICT", "这次请求已存在，请查看原任务。", 409)
    finally:
        if not committed and stored_path is not None:
            stored_path.unlink(missing_ok=True)
        shutil.rmtree(temporary, ignore_errors=True)


@router.get("/memory-sessions/{session_id}/short-scene-jobs")
def list_jobs(session_id: str, db: Session = Depends(get_db)):
    require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    return [view(j) for j in db.scalars(select(ShortSceneJob).where(ShortSceneJob.session_id == session_id)
                                      .order_by(ShortSceneJob.created_at.desc()).limit(30))]


@router.get("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/jobs/by-request/{request_key}")
def recover_job(session_id: str, plan_id: str, request_key: str, db: Session = Depends(get_db)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    plan = require(db, ShortScenePlan, plan_id, "SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。")
    if plan.session_id != session.id or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
        raise DomainError("SHORT_JOB_REQUEST_INVALID", "制作请求依据不正确。", 422)
    job = db.scalar(select(ShortSceneJob).where(ShortSceneJob.family_id == session.elder.person.family_id,
        ShortSceneJob.session_id == session_id, ShortSceneJob.plan_id == plan_id,
        ShortSceneJob.idempotency_key == request_key))
    # Absence is not proof that an earlier concurrent POST cannot still commit.
    return view(job) if job else None


@router.post("/short-scene-jobs/{job_id}/resume")
def resume(job_id: str, payload: Resume, db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    job = require(db, ShortSceneJob, job_id, "SHORT_JOB_NOT_FOUND", "没有找到任务。")
    if payload.authorize_resume is not True:
        raise DomainError("SHORT_JOB_NOT_RESUMABLE", "需要明确继续原任务。", 409)
    if job.status == "awaiting_input_review" and job.assigned_node_id:
        verified_input_review(db, job, store)
        # Keep the original node so its exact reference review and GPU journal are reused.
        changed = db.execute(update(ShortSceneJob).where(ShortSceneJob.id == job.id,
            ShortSceneJob.status == "awaiting_input_review", ShortSceneJob.assigned_node_id == job.assigned_node_id)
            .values(status="queued"))
    else:
        expired_active = (job.status in ACTIVE and job.lease_expires_at is not None
                          and job.lease_expires_at <= utc_now())
        if payload.node_checked_no_gpu_submission is not True:
            raise DomainError("SHORT_JOB_RESUME_CHECK_REQUIRED", "需要先核对家庭节点没有提交显卡计算。", 409)
        if not (job.status in {"interrupted", "failed"} or expired_active) or job.result_asset_id is not None:
            raise DomainError("SHORT_JOB_NOT_RESUMABLE", "只能重新排队已中断且确认未提交显卡的原任务。", 409)
        changed = db.execute(update(ShortSceneJob).where(
            ShortSceneJob.id == job.id, ShortSceneJob.status == job.status,
        ).values(status="queued", error_code=None, assigned_node_id=None, claim_request_key=None,
                 lease_token_hash=None, lease_expires_at=None, progress_percent=0))
    if changed.rowcount != 1:
        raise DomainError("SHORT_JOB_NOT_RESUMABLE", "任务状态已变化，没有重新排队。", 409)
    db.commit(); durable(); db.refresh(job)
    return view(job)


@router.post("/short-scene-jobs/{job_id}/cancel")
def cancel(job_id: str, db: Session = Depends(get_db)):
    job = require(db, ShortSceneJob, job_id, "SHORT_JOB_NOT_FOUND", "没有找到任务。")
    if job.status not in ("queued", *ACTIVE, "awaiting_input_review", "interrupted", "failed"):
        raise DomainError("SHORT_JOB_NOT_CANCELLABLE", "这个任务当前不能取消。", 409)
    changed = db.execute(update(ShortSceneJob).where(ShortSceneJob.id == job.id,
        ShortSceneJob.status.in_(("queued", *ACTIVE, "awaiting_input_review", "interrupted", "failed")))
        .values(status="cancelled", lease_token_hash=None, lease_expires_at=None))
    if changed.rowcount != 1:
        raise DomainError("SHORT_JOB_NOT_CANCELLABLE", "任务状态已变化，请刷新查看。", 409)
    db.commit(); durable()
    return view(job)


def node_for_short(db, authorization):
    node, token = authenticate_node(db, authorization)
    if "scene_video" not in (node.capabilities or []):
        raise DomainError("SHORT_JOB_CAPABILITY_REQUIRED", "节点没有场景视频能力授权。", 403)
    return node, token


def lease(db, node, job_id, token):
    job = db.get(ShortSceneJob, job_id)
    if not job or job.assigned_node_id != node.id or job.status not in ACTIVE:
        raise DomainError("SHORT_JOB_LEASE_INVALID", "任务未由此节点持有。", 409)
    if not token or not job.lease_token_hash or not hmac.compare_digest(session_token_hash(token), job.lease_token_hash):
        raise DomainError("SHORT_JOB_LEASE_INVALID", "任务租约不正确。", 409)
    if not job.lease_expires_at or job.lease_expires_at < utc_now():
        raise DomainError("SHORT_JOB_LEASE_EXPIRED", "任务已中断，需核对原生成记录，不能重复提交。", 409)
    return job


@router.post("/generation-worker/short-scene/tasks/claim")
def claim(payload: Claim, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    node, node_secret = node_for_short(db, authorization)
    lock_node_for_claim(db, node.id, utc_now())
    if payload.request_key:
        previous = db.scalar(select(ShortSceneJob).where(ShortSceneJob.claim_request_key == payload.request_key))
        if previous:
            token = hmac.new(node_secret.encode(), ("/api/v1/generation-worker/short-scene/tasks:" + payload.request_key + ":" + previous.id).encode(), "sha256").hexdigest()
            lease(db, node, previous.id, token)
            db.commit(); durable()
            return claim_view(previous, payload.protocol, token, previous.lease_expires_at)
    if node_has_unresolved_work(db, node.id, utc_now()):
        db.commit()
        durable()
        return None
    candidate = db.scalar(select(ShortSceneJob).where(ShortSceneJob.status == "queued",
        or_(ShortSceneJob.assigned_node_id.is_(None), ShortSceneJob.assigned_node_id == node.id)).order_by(ShortSceneJob.created_at))
    if candidate is None:
        db.commit()
        return None
    token = hmac.new(node_secret.encode(), ("/api/v1/generation-worker/short-scene/tasks:" + payload.request_key + ":" + candidate.id).encode(), "sha256").hexdigest() if payload.request_key else secrets.token_urlsafe(32)
    expiry = utc_now() + LEASE_WINDOW
    result = db.execute(update(ShortSceneJob).where(ShortSceneJob.id == candidate.id, ShortSceneJob.status == "queued")
        .values(status="preparing", assigned_node_id=node.id, lease_token_hash=session_token_hash(token),
                lease_expires_at=expiry, progress_percent=1, claim_request_key=payload.request_key))
    if result.rowcount != 1:
        db.rollback()
        return None
    node.last_seen_at = utc_now()
    db.commit(); durable()
    return claim_view(candidate, payload.protocol, token, expiry)


def claim_view(job, protocol, token, expiry):
    return {"id": job.id, "protocol": protocol, "lease_token": token, "lease_expires_at": expiry,
            "package_sha256": job.bundle_sha256, "package_url": f"/api/v1/generation-worker/short-scene/tasks/{job.id}/package",
            "automatic_retry": False, "duration_seconds": 10}


@router.get("/generation-worker/short-scene/tasks/{job_id}/package")
def package(job_id: str, authorization: str | None = Header(default=None),
            lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
            db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    node, token = node_for_short(db, authorization)
    job = lease(db, node, job_id, lease_token)
    asset = db.get(MediaAsset, job.package_asset_id)
    family = db.get(FamilyArchive, job.family_id)
    root = Path(tempfile.mkdtemp(prefix="lingnian-short-delivery-"))
    try:
        path = resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path)
        if asset.encryption_version != 1 or not verify_asset_integrity(path, expected_size=asset.size_bytes, expected_sha256=asset.sha256):
            raise DomainError("SHORT_JOB_PACKAGE_INVALID", "制作包校验未通过。", 409)
        plain = root / "package.zip"
        decoded = decrypt_media_file(path, plain, family_master_key(family, store), associated_data=media_context(family.id, asset.id))
        if decoded.plaintext_sha256 != job.bundle_sha256 or decoded.plaintext_size != asset.plaintext_size_bytes:
            raise DomainError("SHORT_JOB_PACKAGE_INVALID", "制作包内容已变化。", 409)
        target = root / "package.lnpkg"
        encrypt_package(plain, target, worker_token=token, request_id=job.id)
        return FileResponse(target, media_type="application/vnd.lingnian.encrypted-package",
            headers={"Cache-Control": "no-store"}, background=BackgroundTask(shutil.rmtree, root, ignore_errors=True))
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


@router.patch("/generation-worker/short-scene/tasks/{job_id}/progress")
def progress(job_id: str, payload: Progress, authorization: str | None = Header(default=None),
             lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"), db: Session = Depends(get_db)):
    node, _ = node_for_short(db, authorization)
    job = lease(db, node, job_id, lease_token)
    if job.status == "generating" and payload.stage == "preparing":
        raise DomainError("SHORT_JOB_STAGE_REGRESSION", "生成中的任务不能退回准备中。", 409)
    values = {"status": payload.stage, "progress_percent": max(job.progress_percent, payload.percent),
              "error_code": payload.error_code, "lease_expires_at": utc_now() + LEASE_WINDOW}
    if payload.stage in {"awaiting_input_review", "failed"}:
        values.update(lease_token_hash=None, lease_expires_at=None)
    changed = db.execute(update(ShortSceneJob).where(ShortSceneJob.id == job.id, ShortSceneJob.status == job.status,
        ShortSceneJob.lease_token_hash == session_token_hash(lease_token), ShortSceneJob.lease_expires_at >= utc_now()).values(**values))
    if changed.rowcount != 1:
        raise DomainError("SHORT_JOB_LEASE_INVALID", "任务状态已变化，未覆盖取消或中断记录。", 409)
    node.last_seen_at = utc_now()
    db.commit(); durable()
    return view(job)


def verify_candidate(path):
    completed = subprocess.run([get_ffmpeg_binary(), "-hide_banner", "-nostdin", "-v", "info", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0", "-t", "10.5", "-f", "null", "-"],
        capture_output=True, text=True, timeout=40)
    diagnostic = completed.stderr
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", diagnostic)
    seconds = (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) if duration else 0
    frames = re.findall(r"frame=\s*(\d+)", diagnostic)
    if (completed.returncode != 0 or abs(seconds - 10) > 0.1 or not frames or int(frames[-1]) != 240
            or not re.search(r"Video: [^\n]*\b1280x720\b[^\n]*\b24 fps\b", diagnostic)):
        raise DomainError("SHORT_JOB_RESULT_INVALID", "回传文件不是完整的10秒、24帧含声视频。", 409)


@router.post("/generation-worker/short-scene/tasks/{job_id}/result")
async def upload_result(job_id: str, result: UploadFile = File(...),
    authorization: str | None = Header(default=None), lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
    content_sha256: str = Header(alias="X-Content-Sha256", pattern=r"^[0-9a-f]{64}$"),
    db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    node, _ = node_for_short(db, authorization)
    job = lease(db, node, job_id, lease_token)
    if job.status != "generating":
        raise DomainError("SHORT_JOB_RESULT_STATE_INVALID", "任务尚未进入生成阶段。", 409)
    # Restrict uploaded result before the common media store buffers it to disk.
    if result.size is None or not 0 < result.size <= 15 * 1024**2:
        raise DomainError("SHORT_JOB_RESULT_TOO_LARGE", "10秒候选必须在15MiB以内。", 413)
    stored = await store_video_upload(result, job.session_id, get_settings())
    path = resolve_controlled_path(get_settings().resolved_asset_root, stored["relative_path"])
    committed = False
    try:
        if not hmac.compare_digest(stored["sha256"], content_sha256):
            raise DomainError("SHORT_JOB_RESULT_HASH_INVALID", "回传文件校验失败。", 409)
        verify_candidate(path)
        # Recheck lease after decoding: cancellation/expiry cannot be bypassed by a slow upload.
        db.refresh(job)
        lease(db, node, job_id, lease_token)
        asset = MediaAsset(session_id=job.session_id, kind="generated_short_scene", is_original=False,
                           status="pending_human_review", **stored)
        family = db.get(FamilyArchive, job.family_id)
        db.add(asset); db.flush()
        protect_values(db, family, asset, {"original_filename": asset.original_filename}, store)
        encrypt_asset_if_needed(db, asset, family, store)
        changed = db.execute(update(ShortSceneJob).where(ShortSceneJob.id == job.id, ShortSceneJob.status == "generating",
            ShortSceneJob.lease_token_hash == session_token_hash(lease_token), ShortSceneJob.lease_expires_at >= utc_now())
            .values(result_asset_id=asset.id, status="awaiting_full_playback_review", progress_percent=100,
                    lease_token_hash=None, lease_expires_at=None))
        if changed.rowcount != 1:
            raise DomainError("SHORT_JOB_LEASE_INVALID", "任务已取消或中断，未发布候选。", 409)
        db.commit(); committed = True; durable()
        return view(job)
    finally:
        if not committed:
            db.rollback()
            path.unlink(missing_ok=True)


class ReviewedJobConsent(JobConsent):
    review_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/short-reference-jobs/{reference_job_id}/video-job")
def create_reviewed_job(reference_job_id: str, payload: ReviewedJobConsent,
                        db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    from app.api.short_scene_reference_review_routes import review_context, saved_decision
    reference_job, _, binding = review_context(db, reference_job_id, store)
    if (not binding["source_current"] or binding["review_input_sha256"] != payload.review_input_sha256
            or saved_decision(db, reference_job, binding) != "accepted"):
        raise DomainError("SHORT_JOB_REVIEW_REQUIRED", "请先核对当前原声、参考图和场景，再确认制作。", 409)
    if payload.input_sha256 != binding["selection_input_sha256"]:
        raise DomainError("SHORT_JOB_REVIEW_STALE", "采访依据已变化，请重新核对。", 409)
    existing = db.scalar(select(ShortSceneJob).where(ShortSceneJob.family_id == reference_job.family_id,
        ShortSceneJob.reference_review_sha256 == payload.review_input_sha256))
    if existing:
        durable(); return view(existing)
    asset = db.get(MediaAsset, reference_job.result_asset_id)
    family = db.get(FamilyArchive, reference_job.family_id)
    root = Path(tempfile.mkdtemp(prefix="lingnian-approved-reference-"))
    try:
        image = root / "reference.png"
        decoded = decrypt_media_file(resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path),
            image, family_master_key(family, store), associated_data=media_context(family.id, asset.id))
        if decoded.plaintext_sha256 != binding["image_sha256"] or decoded.plaintext_size != asset.plaintext_size_bytes:
            raise DomainError("SHORT_JOB_REFERENCE_CHANGED", "参考图片校验失败，未创建视频任务。", 409)
        with image.open("rb") as handle:
            return create_job_internal(reference_job.session_id, reference_job.plan_id,
                json.dumps(payload.model_dump(exclude={"review_input_sha256"})),
                UploadFile(file=handle, filename="reference.png"), db, store, input_review=binding)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def verified_input_review(db, job, store):
    from app.api.short_scene_reference_review_routes import review_context, saved_decision
    recorded = job.input_review or {}
    if not recorded.get("job_id"):
        raise DomainError("SHORT_JOB_REVIEW_REQUIRED", "此任务尚无网页素材确认，请保留原任务核对。", 409)
    ref, _, current = review_context(db, recorded["job_id"], store)
    if (ref.family_id != job.family_id or ref.session_id != job.session_id or ref.plan_id != job.plan_id
            or not current["source_current"] or current != recorded or saved_decision(db, ref, current) != "accepted"):
        raise DomainError("SHORT_JOB_REVIEW_STALE", "素材或确认已变化，暂停生成并保留原任务。", 409)
    return current


@router.get("/generation-worker/short-scene/tasks/{job_id}/input-authorization")
def input_authorization(job_id: str, authorization: str | None = Header(default=None),
        lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
        db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    node, _ = node_for_short(db, authorization)
    job = lease(db, node, job_id, lease_token)
    if not job.input_review:
        return {"job_id":job.id,"package_sha256":job.bundle_sha256,"decision":"pending","full_playback_accepted":False}
    binding = verified_input_review(db, job, store)
    return {"job_id":job.id,"package_sha256":job.bundle_sha256,"decision":"accepted",
        "review_input_sha256":binding["review_input_sha256"], "audio_sha256":binding["audio_sha256"],
        "image_sha256":binding["image_sha256"],"selection_input_sha256":binding["selection_input_sha256"],
        "full_playback_accepted":False}


@router.get("/generation-worker/short-scene/tasks/{job_id}/result")
def recover_video_result(job_id: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    node, _ = node_for_short(db, authorization)
    job = db.get(ShortSceneJob, job_id)
    if not job or job.assigned_node_id != node.id:
        raise DomainError("SHORT_JOB_NOT_FOUND", "没有找到此节点的任务。", 404)
    asset = db.get(MediaAsset, job.result_asset_id) if job.result_asset_id else None
    return {**view(job),"package_sha256":job.bundle_sha256,
        "result_sha256":(asset.plaintext_sha256 or asset.sha256) if asset else None}
