"""Store source-bound reference results privately; bytes/schema are not visual approval."""
import hashlib
import hmac
from pathlib import Path
import shutil
import tempfile
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.generation_node_routes import utc_now
from app.api.routes import family_master_key, protect_values
from app.api.short_scene_reference_job_routes import durable, view
from app.api.short_scene_reference_worker_routes import reference_node
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import FamilyArchive, MediaAsset, ShortSceneReferenceJob
from app.services.archive.assets import resolve_controlled_path, verify_asset_integrity
from app.services.auth import session_token_hash
from app.services.generation_capacity import lock_node_for_claim
from app.services.security import SecretStore, encrypt_media_file, get_secret_store, media_context

router = APIRouter(prefix="/api/v1/generation-worker/short-reference", tags=["short-reference-result"])
IMAGE_LIMIT = 8 * 1024**2


class ResultEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_id: UUID


def result_job(db, node, job_id, token, report):
    job = db.get(ShortSceneReferenceJob, job_id)
    if (not job or job.assigned_node_id != node.id or not token or not job.lease_token_hash
            or not hmac.compare_digest(job.lease_token_hash, session_token_hash(token))):
        raise DomainError("SHORT_REFERENCE_LEASE_INVALID", "不能为其他任务回传参考图。", 409)
    if job.brief_sha256 != report["brief_sha256"]:
        raise DomainError("SHORT_REFERENCE_RESULT_CONFLICT", "参考结果与本次授权依据不同。", 409)
    if job.status == "awaiting_reference_review":
        if job.result_report != report or not job.result_asset_id:
            raise DomainError("SHORT_REFERENCE_RESULT_CONFLICT", "原任务已有不同结果，不能覆盖。", 409)
    elif job.status != "generating" or job.lease_expires_at is None or job.lease_expires_at <= utc_now():
        raise DomainError("SHORT_REFERENCE_LEASE_INVALID", "任务未在生成或已取消中断，不能发布结果。", 409)
    return job


def existing_result(db, job):
    asset = db.get(MediaAsset, job.result_asset_id)
    if (not asset or asset.session_id != job.session_id or asset.kind != "generated_short_reference"
            or asset.encryption_version != 1 or asset.plaintext_sha256 != job.result_report.get("image_sha256")
            or not verify_asset_integrity(resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path),
                expected_size=asset.size_bytes, expected_sha256=asset.sha256)):
        raise DomainError("SHORT_REFERENCE_RESULT_UNAVAILABLE", "原参考图尚不可用，请保留节点产物，不要重新生成。", 409)
    durable()
    return {**view(job), "result_report": job.result_report, "result_verified": "bytes_and_binding_only"}


@router.post("/tasks/{job_id}/result")
def upload_reference_result(job_id: str, response: Response, evidence: str = Form(..., max_length=2048), result: UploadFile = File(...),
        authorization: str | None = Header(default=None),
        lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
        db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    response.headers["Cache-Control"] = "no-store"
    node, _ = reference_node(db, authorization)
    try:
        report = ResultEvidence.model_validate_json(evidence).model_dump(mode="json")
    except ValidationError as exc:
        raise DomainError("SHORT_REFERENCE_RESULT_INVALID", "参考结果依据格式不完整。", 422) from exc
    result_job(db, node, job_id, lease_token, report)
    if result.size is None or not 0 < result.size <= IMAGE_LIMIT:
        raise DomainError("SHORT_REFERENCE_RESULT_TOO_LARGE", "参考图必须在8MiB以内。", 413)
    root = Path(tempfile.mkdtemp(prefix="lingnian-reference-result-"))
    stored = None
    committed = False
    try:
        plain = root / "reference.png"
        digest, size = hashlib.sha256(), 0
        with plain.open("xb") as target:
            plain.chmod(0o600)
            while chunk := result.file.read(1024 * 1024):
                size += len(chunk)
                if size > IMAGE_LIMIT:
                    raise DomainError("SHORT_REFERENCE_RESULT_TOO_LARGE", "参考图过大。", 413)
                digest.update(chunk); target.write(chunk)
        if digest.hexdigest() != report["image_sha256"]:
            raise DomainError("SHORT_REFERENCE_RESULT_INVALID", "参考图内容摘要不一致。", 409)
        with Image.open(plain) as picture:
            if picture.format != "PNG" or picture.size != (1280, 704) or getattr(picture, "n_frames", 1) != 1:
                raise DomainError("SHORT_REFERENCE_RESULT_INVALID", "需要单张1280×704 PNG参考，不接受动画或其他尺寸。", 409)
            picture.verify()
        with Image.open(plain) as picture:
            picture.load()
        # Serialize competing uploads for the same node after bounded decoding.
        lock_node_for_claim(db, node.id, utc_now())
        db.expire_all()
        node, _ = reference_node(db, authorization)
        job = result_job(db, node, job_id, lease_token, report)
        if job.status == "awaiting_reference_review":
            saved_result = existing_result(db, job)
            db.commit(); committed = True
            return saved_result
        family = db.get(FamilyArchive, job.family_id)
        key = family_master_key(family, store) if family else None
        if key is None:
            raise DomainError("SHORT_REFERENCE_ENCRYPTION_REQUIRED", "参考结果必须以家庭密钥加密保存。", 409)
        asset_id = str(uuid4())
        relative = f"assets/derived/{job.session_id}/{asset_id}.lnrefimage"
        stored = resolve_controlled_path(get_settings().resolved_asset_root, relative)
        stored.parent.mkdir(parents=True, exist_ok=True)
        encrypted = encrypt_media_file(plain, stored, key, associated_data=media_context(job.family_id, asset_id))
        if encrypted.plaintext_sha256 != report["image_sha256"]:
            raise DomainError("SHORT_REFERENCE_RESULT_INVALID", "参考图保存期间发生变化。", 409)
        asset = MediaAsset(id=asset_id, session_id=job.session_id, kind="generated_short_reference",
            relative_path=relative, original_filename="场景参考.png", mime_type="image/png",
            size_bytes=encrypted.ciphertext_size, sha256=encrypted.ciphertext_sha256,
            plaintext_size_bytes=encrypted.plaintext_size, plaintext_sha256=encrypted.plaintext_sha256,
            encryption_version=1, status="pending_human_review", is_original=False)
        db.add(asset); db.flush()
        # The shared family media reader resolves this field through encrypted
        # metadata even for a fixed, non-personal filename.
        protect_values(db, family, asset, {"original_filename": asset.original_filename}, store)
        changed = db.execute(update(ShortSceneReferenceJob).where(ShortSceneReferenceJob.id == job.id,
            ShortSceneReferenceJob.status == "generating", ShortSceneReferenceJob.lease_token_hash == session_token_hash(lease_token),
            ShortSceneReferenceJob.lease_expires_at > utc_now()).values(status="awaiting_reference_review",
            result_asset_id=asset.id, result_report=report, progress_percent=100, lease_expires_at=None))
        if changed.rowcount != 1:
            raise DomainError("SHORT_REFERENCE_LEASE_INVALID", "原任务状态已变化，未覆盖取消或中断记录。", 409)
        # Keep only the original lease HASH for idempotent result replay; all
        # package/progress APIs reject this completed state regardless of hash.
        db.commit(); committed = True; db.refresh(job); durable()
        return {**view(job), "result_report": job.result_report, "result_verified": "bytes_and_binding_only"}
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("SHORT_REFERENCE_RESULT_INVALID", "参考图未能安全保存，请保留原产物并查询原任务。", 409) from exc
    finally:
        if not committed:
            db.rollback()
            if stored is not None:
                stored.unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)


@router.get("/tasks/{job_id}/result")
def recover_reference_result(job_id: str, response: Response, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    node, _ = reference_node(db, authorization)
    job = db.get(ShortSceneReferenceJob, job_id)
    if not job or job.assigned_node_id != node.id:
        raise DomainError("SHORT_REFERENCE_JOB_NOT_FOUND", "没有此节点的参考任务。", 404)
    if job.status == "awaiting_reference_review":
        return existing_result(db, job)
    return {**view(job), "result_report": None, "result_verified": None}
