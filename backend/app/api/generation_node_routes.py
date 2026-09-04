from __future__ import annotations

import hmac
import os
import secrets
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.routes import (
    encrypt_asset_if_needed,
    family_master_key,
    protect_values,
    require,
    secure_value,
    story_detail_read,
    story_read,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import (
    GenerationNode,
    GenerativeMediaRequest,
    MediaAsset,
    UserAccount,
)
from app.services.archive.assets import (
    resolve_controlled_path,
    store_image_upload,
    store_video_upload,
    verify_asset_integrity,
)
from app.services.auth import current_auth, session_token_hash
from app.services.memory import ProductionMedia, build_production_package, file_sha256
from app.services.security import SecretStore, decrypt_media_file, get_secret_store, media_context


router = APIRouter(prefix="/api/v1", tags=["generation-control"])
WORKER_CAPABILITIES = {"photo_restore", "portrait_video", "scene_video"}
ONLINE_WINDOW = timedelta(seconds=90)
LEASE_WINDOW = timedelta(minutes=5)
MAX_ATTEMPTS = 3
PACKAGE_MAGIC = b"LINGNIANPKG1"
SAFE_WORKER_FAILURE_MESSAGES = {
    "COMFYUI_TEMPORARY_FAILURE": "家用生成服务暂时没有完成任务。",
    "GPU_OUT_OF_MEMORY": "家用显卡显存不足，节点会尝试更轻量的工作流。",
    "MODEL_MISSING": "家用生成节点缺少这项任务需要的模型。",
    "MODEL_LOAD_FAILED": "家用生成节点暂时无法加载模型。",
    "WORKFLOW_FAILED": "本地生成工作流没有完成。",
    "RESULT_UPLOAD_FAILED": "生成结果暂时没有上传成功。",
}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class GenerationNodeCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(min_length=1, max_length=3)


class GenerationNodeCreated(BaseModel):
    id: str
    display_name: str
    connection_token: str
    capabilities: list[str]
    created_at: datetime


class GenerationNodeRead(BaseModel):
    id: str
    display_name: str
    status: str
    connection_state: Literal["online", "offline", "revoked"]
    capabilities: list[str]
    software_version: str | None
    device_summary: str | None
    last_seen_at: datetime | None
    created_at: datetime


class GenerationControlOverview(BaseModel):
    node_count: int
    online_node_count: int
    queued_request_count: int
    processing_request_count: int
    review_request_count: int
    failed_request_count: int


class GenerationQueueItem(BaseModel):
    id: str
    generation_type: str
    status: str
    assigned_node_id: str | None
    progress_percent: int
    progress_stage: str | None
    attempt_count: int
    error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class WorkerHeartbeat(BaseModel):
    software_version: str = Field(min_length=1, max_length=80)
    device_summary: str = Field(min_length=1, max_length=240)
    capabilities: list[str] = Field(min_length=1, max_length=3)


class WorkerHeartbeatRead(BaseModel):
    node_id: str
    accepted_capabilities: list[str]
    poll_interval_seconds: int = 8
    lease_seconds: int = int(LEASE_WINDOW.total_seconds())


class WorkerTaskRead(BaseModel):
    id: str
    generation_type: str
    lease_token: str
    lease_expires_at: datetime
    package_url: str
    max_cost_cents: int
    attempt_count: int
    production_spec: dict
    resume_checkpoint: dict


class WorkerProgress(BaseModel):
    progress_percent: int = Field(ge=0, le=99)
    progress_stage: str = Field(min_length=1, max_length=80)
    completed_scene_count: int | None = Field(default=None, ge=0, le=10)
    total_scene_count: int | None = Field(default=None, ge=1, le=10)
    checkpoint_key: str | None = Field(default=None, min_length=1, max_length=120)


class WorkerFailure(BaseModel):
    error_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Z0-9_]+$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = True


class WorkerAck(BaseModel):
    request_id: str
    status: str


def validate_capabilities(values: list[str]) -> list[str]:
    normalized = sorted(set(values))
    if not normalized or any(item not in WORKER_CAPABILITIES for item in normalized):
        raise DomainError("GENERATION_CAPABILITY_INVALID", "生成节点能力配置无效。", 422)
    return normalized


def require_platform_admin(db: Session) -> UserAccount:
    context = current_auth.get()
    if context is None:
        raise DomainError("AUTH_REQUIRED", "请先登录。", 401)
    user = db.get(UserAccount, context.user_id)
    if user is None or user.platform_role != "admin":
        raise DomainError("PLATFORM_ADMIN_REQUIRED", "只有平台管理员可以管理生成节点。", 403)
    return user


def node_state(node: GenerationNode, *, now: datetime | None = None) -> Literal["online", "offline", "revoked"]:
    current = now or utc_now()
    if node.status == "revoked" or node.revoked_at is not None:
        return "revoked"
    if node.last_seen_at is not None and node.last_seen_at >= current - ONLINE_WINDOW:
        return "online"
    return "offline"


def node_read(node: GenerationNode) -> GenerationNodeRead:
    return GenerationNodeRead(
        id=node.id,
        display_name=node.display_name,
        status=node.status,
        connection_state=node_state(node),
        capabilities=list(node.capabilities or []),
        software_version=node.software_version,
        device_summary=node.device_summary,
        last_seen_at=node.last_seen_at,
        created_at=node.created_at,
    )


def bearer_value(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise DomainError("GENERATION_NODE_AUTH_REQUIRED", "生成节点连接密钥缺失。", 401)
    token = authorization.removeprefix("Bearer ").strip()
    if len(token) < 32:
        raise DomainError("GENERATION_NODE_AUTH_INVALID", "生成节点连接密钥无效。", 401)
    return token


def authenticate_node(db: Session, authorization: str | None) -> tuple[GenerationNode, str]:
    token = bearer_value(authorization)
    node = db.scalar(
        select(GenerationNode).where(GenerationNode.token_hash == session_token_hash(token))
    )
    if node is None or node.status != "active" or node.revoked_at is not None:
        raise DomainError("GENERATION_NODE_AUTH_INVALID", "生成节点连接密钥无效或已撤销。", 401)
    return node, token


def require_lease(
    db: Session,
    node: GenerationNode,
    request_id: str,
    lease_token: str | None,
) -> GenerativeMediaRequest:
    request = db.get(GenerativeMediaRequest, request_id)
    if request is None or request.assigned_node_id != node.id:
        raise DomainError("GENERATION_TASK_NOT_FOUND", "没有找到这项生成任务。", 404)
    if not lease_token or not request.lease_token_hash or not hmac.compare_digest(
        session_token_hash(lease_token), request.lease_token_hash
    ):
        raise DomainError("GENERATION_LEASE_INVALID", "任务租约已经失效，请重新领取。", 409)
    if request.status != "processing" or request.lease_expires_at is None or request.lease_expires_at <= utc_now():
        raise DomainError("GENERATION_LEASE_EXPIRED", "任务租约已经过期，请重新领取。", 409)
    return request


def queue_read(request: GenerativeMediaRequest) -> GenerationQueueItem:
    return GenerationQueueItem(
        id=request.id,
        generation_type=request.generation_type,
        status=request.status,
        assigned_node_id=request.assigned_node_id,
        progress_percent=request.progress_percent,
        progress_stage=request.progress_stage,
        attempt_count=request.attempt_count,
        error_code=request.error_code,
        last_error_message=request.last_error_message,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


@router.get("/generation-control/overview", response_model=GenerationControlOverview)
def generation_control_overview(db: Session = Depends(get_db)) -> GenerationControlOverview:
    require_platform_admin(db)
    nodes = db.scalars(select(GenerationNode)).all()
    counts = dict(
        db.execute(
            select(GenerativeMediaRequest.status, func.count(GenerativeMediaRequest.id)).group_by(
                GenerativeMediaRequest.status
            )
        ).all()
    )
    return GenerationControlOverview(
        node_count=sum(1 for node in nodes if node_state(node) != "revoked"),
        online_node_count=sum(1 for node in nodes if node_state(node) == "online"),
        queued_request_count=counts.get("queued", 0),
        processing_request_count=counts.get("processing", 0),
        review_request_count=counts.get("pending_human_review", 0),
        failed_request_count=counts.get("failed", 0),
    )


@router.get("/generation-control/nodes", response_model=list[GenerationNodeRead])
def list_generation_nodes(db: Session = Depends(get_db)) -> list[GenerationNodeRead]:
    require_platform_admin(db)
    nodes = db.scalars(select(GenerationNode).order_by(GenerationNode.created_at.desc())).all()
    return [node_read(node) for node in nodes]


@router.post("/generation-control/nodes", response_model=GenerationNodeCreated, status_code=201)
def create_generation_node(
    payload: GenerationNodeCreate,
    db: Session = Depends(get_db),
) -> GenerationNodeCreated:
    require_platform_admin(db)
    capabilities = validate_capabilities(payload.capabilities)
    connection_token = f"ln_node_{secrets.token_urlsafe(36)}"
    node = GenerationNode(
        display_name=payload.display_name.strip(),
        token_hash=session_token_hash(connection_token),
        capabilities=capabilities,
        status="active",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return GenerationNodeCreated(
        id=node.id,
        display_name=node.display_name,
        connection_token=connection_token,
        capabilities=capabilities,
        created_at=node.created_at,
    )


@router.delete("/generation-control/nodes/{node_id}", status_code=204)
def revoke_generation_node(node_id: str, db: Session = Depends(get_db)) -> None:
    require_platform_admin(db)
    node = db.get(GenerationNode, node_id)
    if node is None:
        raise DomainError("GENERATION_NODE_NOT_FOUND", "没有找到这个生成节点。", 404)
    now = utc_now()
    node.status = "revoked"
    node.revoked_at = now
    active = db.scalars(
        select(GenerativeMediaRequest).where(
            GenerativeMediaRequest.assigned_node_id == node.id,
            GenerativeMediaRequest.status == "processing",
        )
    ).all()
    for request in active:
        request.status = "queued" if request.attempt_count < MAX_ATTEMPTS else "failed"
        request.progress_stage = "节点已撤销，等待重新分配" if request.status == "queued" else "节点已撤销"
        request.assigned_node_id = None
        request.lease_token_hash = None
        request.lease_expires_at = None
        request.error_code = "GENERATION_NODE_REVOKED"
    db.commit()


@router.get("/generation-control/requests", response_model=list[GenerationQueueItem])
def list_generation_queue(db: Session = Depends(get_db)) -> list[GenerationQueueItem]:
    require_platform_admin(db)
    requests = db.scalars(
        select(GenerativeMediaRequest).order_by(GenerativeMediaRequest.created_at.desc()).limit(100)
    ).all()
    return [queue_read(request) for request in requests]


@router.post("/generation-control/requests/{request_id}/retry", response_model=GenerationQueueItem)
def retry_generation_request(request_id: str, db: Session = Depends(get_db)) -> GenerationQueueItem:
    require_platform_admin(db)
    request = db.get(GenerativeMediaRequest, request_id)
    if request is None:
        raise DomainError("GENERATION_REQUEST_NOT_FOUND", "没有找到这项生成任务。", 404)
    if request.status not in {"failed", "rejected"}:
        raise DomainError("GENERATION_REQUEST_NOT_RETRYABLE", "这项任务当前不需要重试。", 409)
    request.status = "queued"
    request.queued_at = utc_now()
    request.assigned_node_id = None
    request.lease_token_hash = None
    request.lease_expires_at = None
    has_checkpoint = bool((request.progress_detail or {}).get("checkpoint_key"))
    request.progress_percent = request.progress_percent if has_checkpoint else 0
    request.progress_stage = "等待节点从已完成镜头继续" if has_checkpoint else "等待家用生成节点"
    request.error_code = None
    request.last_error_message = None
    request.result_asset_id = None
    request.result_report = {}
    db.commit()
    db.refresh(request)
    return queue_read(request)


@router.post("/generative-media-requests/{request_id}/retry", response_model=GenerationQueueItem)
def retry_family_generation_request(
    request_id: str,
    db: Session = Depends(get_db),
) -> GenerationQueueItem:
    request = require(
        db,
        GenerativeMediaRequest,
        request_id,
        "GENERATION_REQUEST_NOT_FOUND",
        "没有找到这项生成任务。",
    )
    if request.status != "failed":
        raise DomainError("GENERATION_REQUEST_NOT_RETRYABLE", "只有生成失败的任务可以重新制作。", 409)
    if not request.allow_external_upload:
        raise DomainError("GENERATION_UPLOAD_NOT_AUTHORIZED", "这项任务没有获得素材发送授权。", 409)
    request.status = "queued"
    request.queued_at = utc_now()
    request.started_at = None
    request.completed_at = None
    request.assigned_node_id = None
    request.lease_token_hash = None
    request.lease_expires_at = None
    request.attempt_count = 0
    has_checkpoint = bool((request.progress_detail or {}).get("checkpoint_key"))
    request.progress_percent = request.progress_percent if has_checkpoint else 0
    request.progress_stage = "等待节点从已完成镜头继续" if has_checkpoint else "等待家用生成节点"
    request.actual_cost_cents = 0
    request.result_report = {}
    request.error_code = None
    request.last_error_message = None
    db.commit()
    db.refresh(request)
    return queue_read(request)


@router.post("/generative-media-requests/{request_id}/cancel", response_model=GenerationQueueItem)
def cancel_generation_request(
    request_id: str,
    db: Session = Depends(get_db),
) -> GenerationQueueItem:
    request = require(
        db,
        GenerativeMediaRequest,
        request_id,
        "GENERATION_REQUEST_NOT_FOUND",
        "没有找到这项生成任务。",
    )
    if request.status not in {"queued", "processing", "failed"}:
        raise DomainError("GENERATION_REQUEST_NOT_CANCELLABLE", "这项任务当前不能取消。", 409)
    request.status = "cancelled"
    request.progress_stage = "已由家庭成员取消"
    request.assigned_node_id = None
    request.lease_token_hash = None
    request.lease_expires_at = None
    request.error_code = "GENERATION_CANCELLED"
    request.last_error_message = None
    db.commit()
    db.refresh(request)
    return queue_read(request)


@router.post("/generation-worker/heartbeat", response_model=WorkerHeartbeatRead)
def worker_heartbeat(
    payload: WorkerHeartbeat,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> WorkerHeartbeatRead:
    node, _ = authenticate_node(db, authorization)
    capabilities = validate_capabilities(payload.capabilities)
    approved = sorted(set(node.capabilities or []).intersection(capabilities))
    if not approved:
        raise DomainError("GENERATION_NODE_CAPABILITY_MISMATCH", "节点没有可用的已授权能力。", 409)
    node.last_seen_at = utc_now()
    node.software_version = payload.software_version.strip()
    node.device_summary = payload.device_summary.strip()
    db.commit()
    return WorkerHeartbeatRead(node_id=node.id, accepted_capabilities=approved)


def release_expired_leases(db: Session, now: datetime) -> None:
    expired = db.scalars(
        select(GenerativeMediaRequest).where(
            GenerativeMediaRequest.status == "processing",
            GenerativeMediaRequest.lease_expires_at < now,
        )
    ).all()
    for request in expired:
        request.status = "queued" if request.attempt_count < MAX_ATTEMPTS else "failed"
        request.progress_stage = "节点中断，正在重新排队" if request.status == "queued" else "多次中断，等待人工处理"
        request.error_code = "GENERATION_LEASE_EXPIRED"
        request.last_error_message = "生成节点未在租约时间内继续报告进度。"
        request.assigned_node_id = None
        request.lease_token_hash = None
        request.lease_expires_at = None


@router.post("/generation-worker/tasks/claim", response_model=WorkerTaskRead | None)
def claim_worker_task(
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> WorkerTaskRead | None:
    node, _ = authenticate_node(db, authorization)
    now = utc_now()
    release_expired_leases(db, now)
    capabilities = sorted(set(node.capabilities or []).intersection(WORKER_CAPABILITIES))
    candidates = db.scalars(
        select(GenerativeMediaRequest)
        .where(
            GenerativeMediaRequest.status == "queued",
            GenerativeMediaRequest.generation_type.in_(capabilities),
            GenerativeMediaRequest.allow_external_upload.is_(True),
            GenerativeMediaRequest.rights_confirmed.is_(True),
            GenerativeMediaRequest.no_impersonation.is_(True),
        )
        .order_by(GenerativeMediaRequest.queued_at, GenerativeMediaRequest.created_at)
        .limit(5)
    ).all()
    for candidate in candidates:
        lease_token = secrets.token_urlsafe(32)
        lease_expires = now + LEASE_WINDOW
        result = db.execute(
            update(GenerativeMediaRequest)
            .where(
                GenerativeMediaRequest.id == candidate.id,
                GenerativeMediaRequest.status == "queued",
            )
            .values(
                status="processing",
                assigned_node_id=node.id,
                lease_token_hash=session_token_hash(lease_token),
                lease_expires_at=lease_expires,
                attempt_count=GenerativeMediaRequest.attempt_count + 1,
                progress_percent=1,
                progress_stage="节点已领取，准备下载素材",
                started_at=func.coalesce(GenerativeMediaRequest.started_at, now),
                error_code=None,
                last_error_message=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            db.rollback()
            continue
        node.last_seen_at = now
        db.commit()
        request = db.get(GenerativeMediaRequest, candidate.id)
        if request is None:
            return None
        return WorkerTaskRead(
            id=request.id,
            generation_type=request.generation_type,
            lease_token=lease_token,
            lease_expires_at=lease_expires,
            package_url=f"/api/v1/generation-worker/tasks/{request.id}/package",
            max_cost_cents=request.max_cost_cents,
            attempt_count=request.attempt_count,
            production_spec=request.production_spec or {},
            resume_checkpoint=request.progress_detail or {},
        )
    db.commit()
    response.headers["X-Lingnian-No-Snapshot"] = "1"
    return None


def build_authorized_package(
    db: Session,
    request: GenerativeMediaRequest,
    store: SecretStore,
    root: Path,
) -> Path:
    if request.story is None:
        raise DomainError("GENERATION_STORY_REQUIRED", "生成任务必须对应一篇已确认故事。", 409)
    if not request.allow_external_upload or not request.rights_confirmed or not request.no_impersonation:
        raise DomainError("GENERATION_UPLOAD_NOT_AUTHORIZED", "这项任务没有获得素材发送授权。", 409)
    if request.generation_type != "photo_restore" and not request.subject_consent:
        raise DomainError("SUBJECT_CONSENT_REQUIRED", "人物或故事演绎缺少本人专项授权。", 409)

    profile = request.elder
    story = request.story
    family = profile.person.family
    key = family_master_key(family, store)
    story_view = story_read(db, story, store)
    profile_view = secure_value(db, family, profile, "preferred_name", store, master_key=key)
    session = story.source_draft.session
    audio = db.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.session_id == session.id,
            MediaAsset.kind == "audio_original",
            MediaAsset.is_original.is_(True),
        )
        .order_by(MediaAsset.created_at.desc())
    )
    image = db.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.session_id == session.id,
            MediaAsset.kind.in_(["photo_original", "old_object_original"]),
            MediaAsset.is_original.is_(True),
        )
        .order_by(MediaAsset.created_at.desc())
    )
    if request.generation_type != "photo_restore" and not audio:
        raise DomainError("ORIGINAL_AUDIO_REQUIRED", "这篇故事没有可用的原始录音。", 409)
    if request.generation_type in {"photo_restore", "portrait_video"} and not image:
        raise DomainError("PORTRAIT_IMAGE_REQUIRED", "这项任务缺少已授权的原始照片。", 409)

    sources = root / "sources"
    sources.mkdir(mode=0o700)

    def materialize(asset: MediaAsset, name: str) -> Path:
        source_path = resolve_controlled_path(get_settings().resolved_asset_root, asset.relative_path)
        if not verify_asset_integrity(source_path, expected_size=asset.size_bytes, expected_sha256=asset.sha256):
            raise DomainError("ASSET_INTEGRITY_FAILED", "生成任务素材校验失败。", 409)
        target = sources / name
        if asset.encryption_version == 0:
            shutil.copyfile(source_path, target)
        elif asset.encryption_version == 1 and key is not None:
            decrypt_media_file(source_path, target, key, associated_data=media_context(family.id, asset.id))
        else:
            raise DomainError("ASSET_ENCRYPTION_UNSUPPORTED", "无法读取这份加密素材。", 409)
        target.chmod(0o600)
        return target

    audio_media = None
    if audio:
        extension = {"audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/webm": ".webm", "audio/ogg": ".ogg"}.get(audio.mime_type, ".audio")
        path = materialize(audio, f"original-audio{extension}")
        audio_media = ProductionMedia(path, f"sources/original-audio{extension}", audio.mime_type, file_sha256(path))
    image_media = None
    if image:
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(image.mime_type, ".image")
        path = materialize(image, f"authorized-image{extension}")
        image_media = ProductionMedia(path, f"sources/authorized-image{extension}", image.mime_type, file_sha256(path))
    detail = story_detail_read(db, story.detail, store) if story.detail else None
    output = root / f"lingnian-generation-{request.id}.zip"
    return build_production_package(
        output_path=output,
        generation_type=request.generation_type,
        storyteller_name=profile_view,
        story_id=story.id,
        title=story_view.title,
        body=story_view.body,
        life_stage=session.life_stage,
        place_name=detail.place_name if detail else None,
        event_year=detail.event_year if detail else None,
        theme_tags=detail.theme_tags if detail else [],
        actor_label=secure_value(db, family, request, "actor_label", store, master_key=key),
        subject_consent=request.subject_consent,
        rights_confirmed=request.rights_confirmed,
        no_impersonation=request.no_impersonation,
        audio=audio_media,
        image=image_media,
        production_spec=request.production_spec or {},
        external_upload_authorized=True,
        package_status="authorized_node_job",
    )


def encrypt_package(source: Path, target: Path, *, worker_token: str, request_id: str) -> None:
    nonce = os.urandom(12)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=request_id.encode("ascii"),
        info=b"lingnian-generation-package-v1",
    ).derive(worker_token.encode("utf-8"))
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(request_id.encode("ascii"))
    with source.open("rb") as readable, target.open("wb") as writable:
        writable.write(PACKAGE_MAGIC)
        writable.write(nonce)
        while chunk := readable.read(1024 * 1024):
            writable.write(encryptor.update(chunk))
        writable.write(encryptor.finalize())
        writable.write(encryptor.tag)
    target.chmod(0o600)


@router.get("/generation-worker/tasks/{request_id}/package")
def download_worker_package(
    request_id: str,
    authorization: str | None = Header(default=None),
    lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
    db: Session = Depends(get_db),
    store: SecretStore = Depends(get_secret_store),
) -> FileResponse:
    node, worker_token = authenticate_node(db, authorization)
    request = require_lease(db, node, request_id, lease_token)
    root = Path(tempfile.mkdtemp(prefix="lingnian-node-package-"))
    try:
        package = build_authorized_package(db, request, store, root)
        encrypted = root / f"{request.id}.lnpkg"
        encrypt_package(package, encrypted, worker_token=worker_token, request_id=request.id)
        request.progress_percent = max(request.progress_percent, 5)
        request.progress_stage = "素材包已加密发送"
        request.lease_expires_at = utc_now() + LEASE_WINDOW
        node.last_seen_at = utc_now()
        db.commit()
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return FileResponse(
        encrypted,
        media_type="application/vnd.lingnian.encrypted-package",
        filename=f"lingnian-{request.id}.lnpkg",
        headers={"X-Lingnian-Package-Version": "1"},
        background=BackgroundTask(shutil.rmtree, root, ignore_errors=True),
    )


@router.patch("/generation-worker/tasks/{request_id}/progress", response_model=WorkerAck)
def report_worker_progress(
    request_id: str,
    payload: WorkerProgress,
    authorization: str | None = Header(default=None),
    lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
    db: Session = Depends(get_db),
) -> WorkerAck:
    node, _ = authenticate_node(db, authorization)
    request = require_lease(db, node, request_id, lease_token)
    if (
        payload.completed_scene_count is not None
        and payload.total_scene_count is not None
        and payload.completed_scene_count > payload.total_scene_count
    ):
        raise DomainError("GENERATION_PROGRESS_INVALID", "已完成镜头数不能超过总镜头数。", 422)
    request.progress_percent = max(request.progress_percent, payload.progress_percent)
    request.progress_stage = payload.progress_stage.strip()
    if payload.total_scene_count is not None:
        request.progress_detail = {
            "completed_scene_count": payload.completed_scene_count or 0,
            "total_scene_count": payload.total_scene_count,
            "checkpoint_key": payload.checkpoint_key,
        }
    request.lease_expires_at = utc_now() + LEASE_WINDOW
    node.last_seen_at = utc_now()
    db.commit()
    return WorkerAck(request_id=request.id, status=request.status)


@router.post("/generation-worker/tasks/{request_id}/fail", response_model=WorkerAck)
def fail_worker_task(
    request_id: str,
    payload: WorkerFailure,
    authorization: str | None = Header(default=None),
    lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
    db: Session = Depends(get_db),
) -> WorkerAck:
    node, _ = authenticate_node(db, authorization)
    request = require_lease(db, node, request_id, lease_token)
    retrying = payload.retryable and request.attempt_count < MAX_ATTEMPTS
    request.status = "queued" if retrying else "failed"
    has_checkpoint = bool((request.progress_detail or {}).get("checkpoint_key"))
    if retrying and has_checkpoint:
        request.progress_stage = "生成中断，将从已完成镜头继续"
    elif retrying:
        request.progress_percent = 0
        request.progress_stage = "生成中断，正在自动重试"
    else:
        request.progress_stage = "生成失败，等待人工处理"
    request.error_code = payload.error_code
    request.last_error_message = SAFE_WORKER_FAILURE_MESSAGES.get(
        payload.error_code,
        "家用生成节点没有完成这项任务。",
    )
    request.assigned_node_id = None
    request.lease_token_hash = None
    request.lease_expires_at = None
    node.last_seen_at = utc_now()
    db.commit()
    return WorkerAck(request_id=request.id, status=request.status)


@router.post("/generation-worker/tasks/{request_id}/result", response_model=WorkerAck)
async def upload_worker_result(
    request_id: str,
    result: UploadFile = File(...),
    actual_cost_cents: int = Form(default=0),
    rendered_scene_count: int = Form(default=0),
    duration_seconds: float = Form(default=0),
    width: int = Form(default=0),
    height: int = Form(default=0),
    authorization: str | None = Header(default=None),
    lease_token: str | None = Header(default=None, alias="X-Lingnian-Lease"),
    content_sha256: str | None = Header(default=None, alias="X-Content-Sha256"),
    db: Session = Depends(get_db),
    store: SecretStore = Depends(get_secret_store),
) -> WorkerAck:
    node, _ = authenticate_node(db, authorization)
    request = require_lease(db, node, request_id, lease_token)
    if request.story is None:
        raise DomainError("GENERATION_STORY_REQUIRED", "生成任务缺少已确认故事。", 409)
    if actual_cost_cents < 0 or actual_cost_cents > request.max_cost_cents:
        raise DomainError("GENERATION_COST_LIMIT_EXCEEDED", "生成费用超过了本次授权上限。", 409)
    session_id = request.story.source_draft.session_id
    if request.generation_type == "photo_restore":
        stored = await store_image_upload(result, session_id, get_settings(), storage_class="generated")
        # Image dimensions belong to MediaLink metadata. Generated review assets
        # have no MediaLink yet, so do not pass these transient validation values
        # to the MediaAsset ORM constructor.
        stored.pop("width", None)
        stored.pop("height", None)
    else:
        stored = await store_video_upload(result, session_id, get_settings())
    if content_sha256 and not hmac.compare_digest(content_sha256.lower(), stored["sha256"].lower()):
        resolve_controlled_path(get_settings().resolved_asset_root, stored["relative_path"]).unlink(missing_ok=True)
        raise DomainError("GENERATION_RESULT_HASH_MISMATCH", "生成结果传输校验失败。", 409)
    asset = MediaAsset(
        session_id=session_id,
        kind=f"generated_{request.generation_type}",
        status="pending_human_review",
        is_original=False,
        **stored,
    )
    db.add(asset)
    db.flush()
    family = request.elder.person.family
    protect_values(db, family, asset, {"original_filename": asset.original_filename}, store)
    encrypt_asset_if_needed(db, asset, family, store)
    request.result_asset_id = asset.id
    request.provider_key = f"home_comfyui:{node.id}"
    request.actual_cost_cents = actual_cost_cents
    request.result_report = {
        "rendered_scene_count": max(0, rendered_scene_count),
        "duration_seconds": max(0, round(duration_seconds, 3)),
        "width": max(0, width),
        "height": max(0, height),
    }
    request.status = "pending_human_review"
    request.progress_percent = 100
    request.progress_stage = "生成完成，等待家人验收"
    request.completed_at = utc_now()
    request.error_code = None
    request.last_error_message = None
    request.lease_token_hash = None
    request.lease_expires_at = None
    node.last_seen_at = utc_now()
    db.commit()
    return WorkerAck(request_id=request.id, status=request.status)
