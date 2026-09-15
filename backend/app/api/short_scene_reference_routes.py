"""Preview exact reference input within a family; no model/GPU call or queue write."""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy.orm import Session

from app.api.routes import require, secure_value
from app.api.short_scene_routes import prepare
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import MemorySession, ShortScenePlan
from app.services.memory.short_scene_reference import prepare_reference_brief
from app.services.security import SecretStore, get_secret_store
from app.services.security.secure_fields import is_encrypted_family

router = APIRouter(prefix="/api/v1", tags=["short-scene-reference"])
PHOTO_LIMIT = 32 * 1024**2


class ReferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    photo_use_authorized: StrictBool = False
    subject_consent: StrictBool = False


@router.post("/memory-sessions/{session_id}/short-scene-plans/{plan_id}/reference-input")
def reference_input(session_id: str, plan_id: str, options: str = Form(...),
                    photo: UploadFile | None = File(default=None),
                    db: Session = Depends(get_db), store: SecretStore = Depends(get_secret_store)):
    session = require(db, MemorySession, session_id, "SESSION_NOT_FOUND", "没有找到采访。")
    plan = require(db, ShortScenePlan, plan_id, "SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。")
    if plan.session_id != session.id:
        raise DomainError("SHORT_SCENE_PLAN_NOT_FOUND", "没有找到场景方案。", 404)
    try:
        payload = ReferenceInput.model_validate_json(options)
    except ValidationError as exc:
        raise DomainError("SHORT_REFERENCE_INPUT_INVALID", "参考准备参数不完整。", 422) from exc
    if plan.status != "awaiting_scene_context_review":
        raise DomainError("SHORT_REFERENCE_PLAN_NOT_READY", "当前选景方案尚不可准备参考画面。", 409)
    family = session.elder.person.family
    if family.data_classification != "test" and not is_encrypted_family(family):
        raise DomainError("SHORT_REFERENCE_ENCRYPTION_REQUIRED", "真实家庭档案需要先启用加密。", 409)
    request = prepare(db, session, store, plan.reference_mode)
    if not (request["input_sha256"] == plan.input_sha256 == payload.input_sha256):
        raise DomainError("SHORT_REFERENCE_INPUT_CHANGED", "采访依据已变化，请重新选择场景。", 409)
    if plan.reference_mode == "user_photo":
        if photo is None or not payload.photo_use_authorized or not payload.subject_consent:
            raise DomainError("SHORT_REFERENCE_PHOTO_CONSENT_REQUIRED", "使用照片需要原图、使用权及人物同意。", 409)
    elif photo is not None or payload.photo_use_authorized or payload.subject_consent:
        raise DomainError("SHORT_REFERENCE_UNEXPECTED_PHOTO", "示意人物模式不接收真人照片或照片授权。", 409)
    result = secure_value(db, family, plan, "result_payload", store)
    # Uploaded photo is only used inside a private temporary directory. No public
    # asset, generation grant or persistent raw-photo copy is created by preview.
    root = Path(tempfile.mkdtemp(prefix="lingnian-reference-preview-"))
    try:
        source = None
        if photo is not None:
            source = root / "source-image"
            total = 0
            with source.open("xb") as target:
                source.chmod(0o600)
                while chunk := photo.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > PHOTO_LIMIT:
                        raise DomainError("SHORT_REFERENCE_PHOTO_TOO_LARGE", "照片不能超过32MiB。", 413)
                    target.write(chunk)
        envelope = prepare_reference_brief(request, result.get("selection", {}),
            input_sha256=payload.input_sha256, photo=source,
            photo_use_authorized=payload.photo_use_authorized)
        return JSONResponse({**envelope, "session_id": session.id, "plan_id": plan.id,
            "purpose": "short_scene_reference_generation", "external_call_ready": False,
            "generation_submitted": False, "photo_persisted": False,
            "notice": "仅准备本次参考画面依据，尚未授权节点生成。后续将发送整场采访文字和所选照片，不发送录音。"},
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Lingnian-No-Snapshot": "1"})
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("SHORT_REFERENCE_PREPARATION_FAILED", "参考依据或照片格式校验失败，没有提交生成。", 409) from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)
