"""Persist purpose-authorized encrypted reference inputs, separate from video jobs.

Node dispatch lives in the dedicated worker router. A queued intent is not
evidence of GPU activity, and no endpoint auto-approves the resulting image.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4
import zipfile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes import family_master_key, protect_values, require
from app.api.generation_node_routes import utc_now
from app.api.short_scene_reference_routes import ReferenceInput, PHOTO_LIMIT, reference_input
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import MediaAsset, MemorySession, ModelConsentEvent, ShortScenePlan, ShortSceneReferenceJob
from app.models.entities import now_utc
from app.services.archive.assets import resolve_controlled_path
from app.services.auth import current_auth
from app.services.database_snapshot import persist_configured_database_snapshot
from app.services.security import SecretStore, encrypt_media_file, get_secret_store, media_context

router = APIRouter(prefix="/api/v1", tags=["short-scene-reference-jobs"])


class ReferenceJobConsent(ReferenceInput):
    brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    authorize_reference_generation: StrictBool
    authorize_node_delivery: StrictBool
    no_impersonation: StrictBool


class ReferenceResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorize_resume: StrictBool
    node_checked_no_gpu_submission: StrictBool


def durable():
    if get_settings().formal_auth_required:
        try:
            if persist_configured_database_snapshot(get_settings()) is None:
                raise RuntimeError("NO_SNAPSHOT")
        except Exception as exc:
            raise DomainError("SHORT_REFERENCE_DURABILITY_FAILED", "参考任务尚未安全保存，请恢复查询原请求，不要重复提交。", 503) from exc


def view(item):
    status = item.status
    if status in {"preparing", "generating"} and (item.lease_expires_at is None or item.lease_expires_at <= utc_now()):
        status = "interrupted"
    return {"id": item.id, "session_id": item.session_id, "plan_id": item.plan_id,
            "brief_sha256": item.brief_sha256, "status": status, "error_code": item.error_code,
            "progress_percent": item.progress_percent, "result_asset_id": item.result_asset_id,
            "created_at": item.created_at, "automatic_retry": False, "visual_accepted": False,
            "generation_submitted": None if item.assigned_node_id else False, "node_dispatch_available": True}


@router.post("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/reference-jobs")
def create_reference_job(session_id: str, plan_id: str, consent: str = Form(...),
                         photo: UploadFile | None = File(default=None),
                         db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    plan = require(db, ShortScenePlan, plan_id, "SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。")
    if plan.session_id != session.id:
        raise DomainError("SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。", 404)
    try:
        options = ReferenceJobConsent.model_validate_json(consent)
    except ValidationError as exc:
        raise DomainError("SHORT_REFERENCE_CONSENT_INVALID", "请提供完整的参考生成与节点投递授权。", 422) from exc
    if not all((options.authorize_reference_generation, options.authorize_node_delivery, options.no_impersonation)):
        raise DomainError("SHORT_REFERENCE_CONSENT_REQUIRED", "需要同意本次生成参考画面及发送文字和照片到家庭节点。", 409)
    preview = reference_input(session_id, plan_id,
        json.dumps(options.model_dump(include=set(ReferenceInput.model_fields))), photo, db, store)
    prepared = json.loads(preview.body)
    if prepared["brief_sha256"] != options.brief_sha256:
        raise DomainError("SHORT_REFERENCE_BRIEF_CHANGED", "本次照片或参考依据已经变化，请重新查看后授权。", 409)
    family = session.elder.person.family
    same_key = db.scalar(select(ShortSceneReferenceJob).where(
        ShortSceneReferenceJob.family_id == family.id, ShortSceneReferenceJob.idempotency_key == options.idempotency_key))
    if same_key:
        if same_key.plan_id != plan.id or same_key.brief_sha256 != options.brief_sha256:
            raise DomainError("SHORT_REFERENCE_KEY_CONFLICT", "此请求已绑定另一份素材，不能覆盖。", 409)
        durable(); return view(same_key)
    same_brief = db.scalar(select(ShortSceneReferenceJob).where(
        ShortSceneReferenceJob.plan_id == plan.id, ShortSceneReferenceJob.brief_sha256 == options.brief_sha256))
    if same_brief:
        durable(); return view(same_brief)
    key = family_master_key(family, store)
    if key is None:
        raise DomainError("SHORT_REFERENCE_ENCRYPTION_REQUIRED", "参考投递素材必须先启用家庭加密。", 409)
    root = Path(tempfile.mkdtemp(prefix="lingnian-reference-intent-"))
    stored = None
    committed = False
    try:
        brief = {k: prepared[k] for k in ("brief", "brief_sha256", "status", "generation_ready")}
        data = json.dumps(brief, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        if len(data) > 512 * 1024:
            raise DomainError("SHORT_REFERENCE_INPUT_TOO_LARGE", "参考文字依据过大。", 413)
        files = {"brief.json": {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}}
        package = root / "reference.zip"
        with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_STORED) as archive:
            package.chmod(0o600)
            archive.writestr("brief.json", data)
            if photo is not None:
                photo.file.seek(0)
                with Image.open(photo.file) as picture:
                    photo_name = "source.png" if picture.format == "PNG" else "source.jpg"
                photo.file.seek(0)
                digest, size = hashlib.sha256(), 0
                with archive.open(photo_name, "w") as target:
                    while block := photo.file.read(1024 * 1024):
                        size += len(block)
                        if size > PHOTO_LIMIT:
                            raise DomainError("SHORT_REFERENCE_PHOTO_TOO_LARGE", "照片不能超过32MiB。", 413)
                        digest.update(block); target.write(block)
                if digest.hexdigest() != brief["brief"]["source_photo"]["sha256"]:
                    raise DomainError("SHORT_REFERENCE_PHOTO_CHANGED", "照片在准备期间变化，未投递。", 409)
                files[photo_name] = {"sha256": digest.hexdigest(), "size_bytes": size}
            archive.writestr("manifest.json", json.dumps({"format": "lingnian-reference-bundle", "version": 1,
                "brief_sha256": options.brief_sha256, "files": files}, sort_keys=True))
        asset_id = str(uuid4())
        relative = f"assets/derived/{session.id}/{asset_id}.lnref"
        stored = resolve_controlled_path(get_settings().resolved_asset_root, relative)
        stored.parent.mkdir(parents=True, exist_ok=True)
        evidence = encrypt_media_file(package, stored, key, associated_data=media_context(family.id, asset_id))
        asset = MediaAsset(id=asset_id, session_id=session.id, kind="short_reference_package", relative_path=relative,
            original_filename="reference.lnref", mime_type="application/vnd.lingnian.encrypted-package",
            size_bytes=evidence.ciphertext_size, sha256=evidence.ciphertext_sha256, status="internal_only", is_original=False,
            encryption_version=1, plaintext_size_bytes=evidence.plaintext_size, plaintext_sha256=evidence.plaintext_sha256)
        auth = current_auth.get()
        event = ModelConsentEvent(family_id=family.id, session_id=session.id, purpose="short_scene_reference_generation",
            actor_label=f"user:{auth.user_id}" if auth else "本机用户", decision="granted", one_time=True,
            used_at=now_utc(), input_sha256=options.brief_sha256, data_classification=family.data_classification)
        db.add_all([asset, event]); db.flush()
        protect_values(db, family, event, {"actor_label": event.actor_label}, store)
        job = ShortSceneReferenceJob(family_id=family.id, session_id=session.id, plan_id=plan.id,
            package_asset_id=asset.id, consent_id=event.id, idempotency_key=options.idempotency_key,
            brief_sha256=options.brief_sha256, bundle_sha256=evidence.plaintext_sha256, status="queued")
        db.add(job); db.commit(); committed = True
        durable()
        return view(job)
    except IntegrityError:
        db.rollback()
        winner = db.scalar(select(ShortSceneReferenceJob).where(
            ShortSceneReferenceJob.family_id == family.id, ShortSceneReferenceJob.idempotency_key == options.idempotency_key))
        if winner is None:
            winner = db.scalar(select(ShortSceneReferenceJob).where(
                ShortSceneReferenceJob.plan_id == plan.id, ShortSceneReferenceJob.brief_sha256 == options.brief_sha256))
        if winner and winner.plan_id == plan.id and winner.brief_sha256 == options.brief_sha256:
            durable(); return view(winner)
        raise DomainError("SHORT_REFERENCE_KEY_CONFLICT", "已有不同的参考请求，请查看原任务。", 409)
    except DomainError:
        raise
    except Exception as exc:
        if not committed:
            db.rollback()
        raise DomainError("SHORT_REFERENCE_STORAGE_FAILED", "参考素材保存失败，没有发送到节点。", 503) from exc
    finally:
        if not committed and stored is not None:
            stored.unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)


@router.get("/memory-sessions/{session_id}/short-reference-jobs")
def list_reference_jobs(session_id: str, db: Session = Depends(get_db)):
    require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    return [view(j) for j in db.scalars(select(ShortSceneReferenceJob).where(
        ShortSceneReferenceJob.session_id == session_id).order_by(ShortSceneReferenceJob.created_at.desc()).limit(30))]


@router.get("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/reference-jobs/by-request/{request_key}")
def recover_reference_job(session_id: str, plan_id: str, request_key: str, db: Session = Depends(get_db)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    plan = require(db, ShortScenePlan, plan_id, "SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。")
    if plan.session_id != session.id or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
        raise DomainError("SHORT_REFERENCE_REQUEST_INVALID", "参考请求依据不正确。", 422)
    job = db.scalar(select(ShortSceneReferenceJob).where(ShortSceneReferenceJob.family_id == session.elder.person.family_id,
        ShortSceneReferenceJob.session_id == session.id, ShortSceneReferenceJob.plan_id == plan.id,
        ShortSceneReferenceJob.idempotency_key == request_key))
    if job: durable()
    return view(job) if job else None


@router.post("/short-reference-jobs/{job_id}/cancel")
def cancel_reference_job(job_id: str, db: Session = Depends(get_db)):
    job = require(db, ShortSceneReferenceJob, job_id, "SHORT_REFERENCE_JOB_NOT_FOUND", "没有找到参考任务。")
    if job.status == "cancelled":
        durable(); return view(job)
    changed = db.execute(update(ShortSceneReferenceJob).where(ShortSceneReferenceJob.id == job.id,
        ShortSceneReferenceJob.status.in_(("queued", "preparing", "generating", "interrupted", "failed")))
        .values(status="cancelled", lease_token_hash=None, lease_expires_at=None))
    if changed.rowcount != 1:
        raise DomainError("SHORT_REFERENCE_NOT_CANCELLABLE", "当前任务状态不允许取消。", 409)
    db.commit(); durable(); db.refresh(job)
    return view(job)


@router.post("/short-reference-jobs/{job_id}/resume")
def resume_reference_job(job_id: str, payload: ReferenceResume, db: Session = Depends(get_db)):
    job = require(db, ShortSceneReferenceJob, job_id, "SHORT_REFERENCE_JOB_NOT_FOUND", "没有找到参考任务。")
    if payload.authorize_resume is not True or payload.node_checked_no_gpu_submission is not True:
        raise DomainError("SHORT_REFERENCE_RESUME_CHECK_REQUIRED", "需要先核对家庭节点没有提交显卡计算。", 409)
    expired_active = (job.status in {"preparing", "generating"} and job.lease_expires_at is not None
                      and job.lease_expires_at <= utc_now())
    if not (job.status in {"interrupted", "failed"} or expired_active) or job.result_asset_id is not None:
        raise DomainError("SHORT_REFERENCE_NOT_RESUMABLE", "只能重新排队已中断且确认未提交显卡的原参考任务。", 409)
    changed = db.execute(update(ShortSceneReferenceJob).where(
        ShortSceneReferenceJob.id == job.id, ShortSceneReferenceJob.status == job.status,
    ).values(status="queued", error_code=None, assigned_node_id=None, claim_request_key=None,
             lease_token_hash=None, lease_expires_at=None, progress_percent=0))
    if changed.rowcount != 1:
        raise DomainError("SHORT_REFERENCE_NOT_RESUMABLE", "参考任务状态已变化，没有重新排队。", 409)
    db.commit(); durable(); db.refresh(job)
    return view(job)


@router.get("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/reference-jobs/by-brief/{brief_sha256}")
def recover_reference_brief(session_id: str, plan_id: str, brief_sha256: str, db: Session = Depends(get_db)):
    # Different browser request keys may coalesce to one unique source-bound job.
    # Recover that exact input after a lost response; never interpret absence as
    # permission to submit again while the earlier POST may still be committing.
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    plan = require(db, ShortScenePlan, plan_id, "SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。")
    if plan.session_id != session.id or not re.fullmatch(r"[0-9a-f]{64}", brief_sha256):
        raise DomainError("SHORT_REFERENCE_REQUEST_INVALID", "参考请求依据不正确。", 422)
    job = db.scalar(select(ShortSceneReferenceJob).where(
        ShortSceneReferenceJob.family_id == session.elder.person.family_id,
        ShortSceneReferenceJob.plan_id == plan.id, ShortSceneReferenceJob.brief_sha256 == brief_sha256))
    if job: durable()
    return view(job) if job else None
