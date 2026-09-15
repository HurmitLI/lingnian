"""Family-only source/image comparison. Reading is never approval or video authorization."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal
from app.models import ModelConsentEvent
from app.models.entities import now_utc
from app.services.auth import current_auth
from app.api.routes import protect_values
from app.api.short_scene_reference_job_routes import durable

from app.api.routes import require, family_master_key
from app.api.short_scene_routes import prepare
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import DomainError
from app.models import MediaAsset, MemorySession, ShortScenePlan, ShortSceneReferenceJob
from app.services.archive.assets import resolve_controlled_path, verify_asset_integrity
from app.services.security import SecretStore, decrypt_media_file, get_secret_store, media_context

router=APIRouter(prefix="/api/v1",tags=["short-reference-review"])
HEADERS={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff", "X-Lingnian-No-Snapshot":"1"}
CHECKS=["whole_interview_context", "subject_and_event_age", "scene_and_era", "face_and_eyes",
        "hands_and_limbs", "wardrobe_and_props", "opening_pose", "photo_or_illustrative_identity",
        "complete_sentence", "one_scene_one_action", "source_audio_matches"]


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def read_bound_reference(db,job,store,*,include_photo=False):
    session=require(db,MemorySession,job.session_id,"SESSION_NOT_FOUND","没有找到采访。")
    family=session.elder.person.family
    asset=db.get(MediaAsset,job.package_asset_id)
    if (family.id!=job.family_id or not asset or asset.session_id!=job.session_id or asset.kind!="short_reference_package"
            or asset.encryption_version!=1 or not asset.plaintext_size_bytes or not 0<asset.plaintext_size_bytes<=34*1024**2):
        raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID","参考依据关联不完整。",409)
    root=Path(tempfile.mkdtemp(prefix="lingnian-reference-review-"))
    try:
        source=resolve_controlled_path(get_settings().resolved_asset_root,asset.relative_path)
        if not verify_asset_integrity(source,expected_size=asset.size_bytes,expected_sha256=asset.sha256):
            raise ValueError("ciphertext")
        plain=root/"reference.zip"
        decoded=decrypt_media_file(source,plain,family_master_key(family,store),associated_data=media_context(family.id,asset.id))
        if decoded.plaintext_sha256!=job.bundle_sha256 or decoded.plaintext_size!=asset.plaintext_size_bytes:
            raise ValueError("plaintext")
        with zipfile.ZipFile(plain) as archive:
            infos=archive.infolist(); names=[i.filename for i in infos]
            if len(names)!=len(set(names)) or set(names) not in (
                {"manifest.json","brief.json"},{"manifest.json","brief.json","source.png"},{"manifest.json","brief.json","source.jpg"}):
                raise ValueError("members")
            caps={"manifest.json":16*1024,"brief.json":512*1024,"source.png":32*1024**2,"source.jpg":32*1024**2}
            if any(i.compress_type!=zipfile.ZIP_STORED or not 0<i.file_size<=caps[i.filename] for i in infos):
                raise ValueError("member size")
            envelope=json.loads(archive.read("brief.json"))
            body=envelope["brief"]
            if (envelope["brief_sha256"]!=job.brief_sha256 or sha(body)!=job.brief_sha256
                    or body.get("format")!="lingnian-short-reference" or body.get("generation_authorized") is not False
                    or body.get("visual_accepted") is not False):
                raise ValueError("brief binding")
            photo_name=next((n for n in names if n in {"source.png","source.jpg"}),None)
            anchor=body.get("source_photo")
            if body.get("reference_mode")=="user_photo":
                if (not photo_name or not isinstance(anchor,dict) or anchor.get("usage_authorized") is not True
                        or body.get("identity_claim")!="photo_reference_not_historical_footage"):
                    raise ValueError("photo binding")
            elif (body.get("reference_mode")!="illustrative" or photo_name or anchor is not None
                    or body.get("identity_claim")!="illustrative_not_verified_likeness"):
                raise ValueError("illustrative binding")
            photo=archive.read(photo_name) if include_photo and photo_name else None
            if photo is not None and hashlib.sha256(photo).hexdigest()!=anchor.get("sha256"):
                raise ValueError("photo hash")
        return session,body,photo,"image/jpeg" if photo_name=="source.jpg" else "image/png"
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID","参考依据无法安全读取，未批准生成。",409) from exc
    finally:
        shutil.rmtree(root,ignore_errors=True)


def review_context(db,job_id,store):
    job=require(db,ShortSceneReferenceJob,job_id,"SHORT_REFERENCE_JOB_NOT_FOUND","没有找到参考任务。")
    if job.status!="awaiting_reference_review" or not job.result_asset_id:
        raise DomainError("SHORT_REFERENCE_NOT_READY","参考图尚未回传，暂不能核对。",409)
    asset=db.get(MediaAsset,job.result_asset_id)
    if (not asset or asset.session_id!=job.session_id or asset.kind!="generated_short_reference" or asset.encryption_version!=1
            or asset.status!="pending_human_review"
            or asset.plaintext_sha256!=job.result_report.get("image_sha256")
            or not verify_asset_integrity(resolve_controlled_path(get_settings().resolved_asset_root,asset.relative_path),
                expected_size=asset.size_bytes,expected_sha256=asset.sha256)):
        raise DomainError("SHORT_REFERENCE_RESULT_UNAVAILABLE","参考图缺失或已变化，请保留原任务。",409)
    session,body,_,_=read_bound_reference(db,job,store)
    plan=require(db,ShortScenePlan,job.plan_id,"SHORT_SCENE_PLAN_NOT_FOUND","没有找到原场景方案。")
    if plan.session_id!=session.id:
        raise DomainError("SHORT_REFERENCE_PACKAGE_INVALID","参考任务与原采访关联不一致。",409)
    source_current=False
    try:
        current=prepare(db,session,store,plan.reference_mode)
        source_current=(current["input_sha256"]==plan.input_sha256==body["selection_input_sha256"])
    except DomainError:
        # Retain the old source for comparison, never pretend it matches a changed/deleted interview.
        pass
    audio=db.scalar(select(MediaAsset).where(MediaAsset.session_id==session.id,
        MediaAsset.kind=="audio_original", MediaAsset.is_original.is_(True), MediaAsset.status=="ready")
        .order_by(MediaAsset.created_at.desc()))
    if not audio or not verify_asset_integrity(resolve_controlled_path(get_settings().resolved_asset_root,audio.relative_path),
            expected_size=audio.size_bytes,expected_sha256=audio.sha256):
        raise DomainError("SHORT_REFERENCE_AUDIO_UNAVAILABLE","原声缺失或已变化，暂不能确认。",409)
    binding={"audio_asset_id":audio.id,"audio_sha256":audio.plaintext_sha256 or audio.sha256,
        "selection_input_sha256":body["selection_input_sha256"],"job_id":job.id,"brief_sha256":job.brief_sha256,"image_sha256":asset.plaintext_sha256,
        "result_asset_id":asset.id,"reference_binding":job.result_report.get("reference_binding")}
    return job,body,{**binding,"review_input_sha256":sha(binding),"source_current":source_current}


@router.get("/short-reference-jobs/{job_id}/review-input")
def reference_review_input(job_id:str,db:Session=Depends(get_db),store:SecretStore=Depends(get_secret_store)):
    job,body,binding=review_context(db,job_id,store)
    return JSONResponse({**binding,"status":"awaiting_reference_review","visual_accepted":False,"video_authorized":False,
        "review_submission_available":True, "review_decision":saved_decision(db,job,binding),"required_checks":CHECKS,
        "reference_mode":body["reference_mode"],"identity_claim":body["identity_claim"],
        "source_audio_url":f"/api/v1/media-assets/{binding['audio_asset_id']}/content",
        "candidate":body["candidate"],"scene":body["scene"],"answers":body["answers"],
        "interview_context":body["interview_context"],
        "reference_image_url":f"/api/v1/media-assets/{job.result_asset_id}/content",
        "source_photo_url":f"/api/v1/short-reference-jobs/{job.id}/source-photo" if body["reference_mode"]=="user_photo" else None},headers=HEADERS)


@router.get("/short-reference-jobs/{job_id}/source-photo")
def reference_source_photo(job_id:str,db:Session=Depends(get_db),store:SecretStore=Depends(get_secret_store)):
    job=require(db,ShortSceneReferenceJob,job_id,"SHORT_REFERENCE_JOB_NOT_FOUND","没有找到参考任务。")
    _,_,photo,mime=read_bound_reference(db,job,store,include_photo=True)
    if photo is None:
        raise DomainError("SHORT_REFERENCE_NO_PHOTO","此任务是无照片示意模式，没有真人照片。",404)
    return Response(photo,media_type=mime,headers=HEADERS)


class ReferenceDecision(BaseModel):
    model_config=ConfigDict(extra="forbid")
    review_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accepted", "rejected"]
    checks: dict[str, StrictBool]


def saved_decision(db,job,binding):
    event=db.scalar(select(ModelConsentEvent).where(ModelConsentEvent.family_id==job.family_id,
        ModelConsentEvent.session_id==job.session_id,ModelConsentEvent.purpose=="short_scene_reference_review",
        ModelConsentEvent.input_sha256==binding["review_input_sha256"])
        .order_by(ModelConsentEvent.created_at.desc()))
    return event.decision if event and not event.revoked_at and binding["source_current"] else None


@router.post("/short-reference-jobs/{job_id}/review")
def save_reference_review(job_id:str,payload:ReferenceDecision,db:Session=Depends(get_db),store:SecretStore=Depends(get_secret_store)):
    job,body,binding=review_context(db,job_id,store)
    if not binding["source_current"] or payload.review_input_sha256!=binding["review_input_sha256"]:
        raise DomainError("SHORT_REFERENCE_REVIEW_STALE","素材或采访已变化，请重新查看后确认。",409)
    if payload.decision=="accepted" and (set(payload.checks)!=set(CHECKS) or not all(payload.checks.values())):
        raise DomainError("SHORT_REFERENCE_REVIEW_INCOMPLETE","请先听完整原声并核对参考图与场景。",409)
    if saved_decision(db,job,binding)!=payload.decision:
        auth=current_auth.get()
        family=db.get(MemorySession,job.session_id).elder.person.family
        event=ModelConsentEvent(family_id=job.family_id,session_id=job.session_id,
            actor_label=f"user:{auth.user_id}" if auth else "本机用户",purpose="short_scene_reference_review",
            decision=payload.decision,one_time=True,used_at=now_utc(),input_sha256=binding["review_input_sha256"],
            data_classification=family.data_classification)
        db.add(event);db.flush();protect_values(db,family,event,{"actor_label":event.actor_label},store);db.commit()
    durable()
    return {"review_input_sha256":binding["review_input_sha256"],"decision":payload.decision,"video_authorized":False}
