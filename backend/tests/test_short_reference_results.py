"""Synthetic PNG uploads, source binding and idempotent storage; no visual acceptance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import io
import json
from threading import Barrier

from PIL import Image
import pytest
from sqlalchemy import func, select

from app.api import short_scene_reference_result_routes as results
from app.api.routes import family_master_key, secure_value
from app.core.config import get_settings
from app.core.errors import DomainError
from app.main import app
from app.models import FamilyArchive, GenerationNode, MediaAsset, ShortSceneReferenceJob
from app.services.auth import session_token_hash
from app.services.security import decrypt_media_file, get_secret_store, media_context
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_jobs import queued_case, TOKEN  # noqa: F401
from test_short_scene_reference_jobs import prepare, submit
from test_short_reference_worker import claim, headers


def png(size=(1280,704), color="blue", fmt="PNG"):
    out=io.BytesIO(); Image.new("RGB",size,color).save(out,fmt); return out.getvalue()


def setup(client,s,mode="illustrative"):
    made=submit(client,prepare(client,s,mode)); assert made.status_code == 200
    task=claim(client).json()
    updated=client.patch(task["package_url"].replace("/package","/progress"),headers=headers(task),
                        json={"stage":"generating","percent":20})
    assert updated.status_code == 200
    image=png()
    report={"brief_sha256":task["brief_sha256"],"image_sha256":hashlib.sha256(image).hexdigest(),
            "reference_binding":"a"*64,"graph_sha256":"b"*64,"prompt_id":"11111111-1111-4111-8111-111111111111"}
    return task,image,report


def upload(client,task,image,report,**kwargs):
    return client.post(task["package_url"].replace("/package","/result"),
        headers=kwargs.get("headers",headers(task)), data={"evidence":json.dumps(report)},
        files={"result":("../../private-name.png",image,"image/png")})


@pytest.mark.parametrize("mode",["illustrative","user_photo"])
def test_result_is_family_encrypted_pending_review_and_replay_safe(client,db,queued_case,tmp_path,mode):
    s=queued_case; task,image,report=setup(client,s,mode)
    result=upload(client,task,image,report); assert result.status_code == 200,result.text
    assert result.headers["cache-control"] == "no-store"
    body=result.json()
    assert body["status"] == "awaiting_reference_review" and body["visual_accepted"] is False
    assert body["result_verified"] == "bytes_and_binding_only" and body["result_report"] == report
    job=db.get(ShortSceneReferenceJob,task["id"]); asset=db.get(MediaAsset,job.result_asset_id)
    assert asset.encryption_version == 1 and asset.status == "pending_human_review"
    assert asset.plaintext_sha256 == report["image_sha256"]
    path=get_settings().resolved_asset_root/asset.relative_path
    assert path.read_bytes().startswith(b"NNMEDIA1")
    store=app.dependency_overrides[get_secret_store]()
    assert secure_value(db,db.get(FamilyArchive,s.family_id),asset,"original_filename",store)=="场景参考.png"
    key=family_master_key(db.get(FamilyArchive,s.family_id),store)
    plain=tmp_path/"checked.png"
    decrypt_media_file(path,plain,key,associated_data=media_context(s.family_id,asset.id))
    assert plain.read_bytes() == image
    assert upload(client,task,image,report).json()["result_asset_id"] == asset.id
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind=="generated_short_reference")) == 1
    assert client.get(task["package_url"],headers=headers(task)).status_code == 409
    recovered=client.get(task["package_url"].replace("/package","/result"),headers={"Authorization":f"Bearer {TOKEN}"})
    assert recovered.status_code == 200 and recovered.json()["result_report"] == report
    assert "lease_token" not in recovered.text and "relative_path" not in recovered.text
    assert all(not p.exists() for p in s.roots)


@pytest.mark.parametrize("damage",["brief","hash","graph","fake_approval","dimensions","jpeg","corrupt"])
def test_invalid_result_never_creates_asset(client,db,queued_case,damage):
    s=queued_case; task,image,report=setup(client,s)
    if damage=="brief": report["brief_sha256"]="f"*64
    elif damage=="hash": report["image_sha256"]="f"*64
    elif damage=="graph": report["graph_sha256"]="not a digest"
    elif damage=="fake_approval": report["visual_accepted"]=True
    else:
        image=png(size=(704,1280)) if damage=="dimensions" else png(fmt="JPEG") if damage=="jpeg" else b"broken private image"
        report["image_sha256"]=hashlib.sha256(image).hexdigest()
    result=upload(client,task,image,report)
    assert result.status_code in {409,422},result.text
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind=="generated_short_reference")) == 0
    assert db.get(ShortSceneReferenceJob,task["id"]).status == "generating"
    assert all(not p.exists() for p in s.roots)


def test_conflicting_replay_preserves_original_image_and_report(client,db,queued_case):
    task,image,report=setup(client,queued_case)
    original=upload(client,task,image,report).json()
    changed=png(color="red")
    assert upload(client,task,changed,{**report,"image_sha256":hashlib.sha256(changed).hexdigest()}).status_code == 409
    assert upload(client,task,image,{**report,"graph_sha256":"f"*64}).status_code == 409
    assert upload(client,task,image,report,headers={**headers(task),"X-Lingnian-Lease":"wrong"}).status_code == 409
    job=db.get(ShortSceneReferenceJob,task["id"])
    assert job.result_asset_id == original["result_asset_id"] and job.result_report == report


@pytest.mark.parametrize("damage",["cancelled","expired","revoked","other_node"])
def test_result_requires_original_active_node_and_task(client,db,queued_case,damage):
    s=queued_case; task,image,report=setup(client,s)
    sent_headers=headers(task)
    if damage=="cancelled": client.post(f"/api/v1/short-reference-jobs/{task['id']}/cancel")
    elif damage=="expired":
        db.get(ShortSceneReferenceJob,task["id"]).lease_expires_at=results.utc_now()-timedelta(seconds=1); db.commit()
    elif damage=="revoked": s.node.status="revoked"; db.commit()
    else:
        other=TOKEN+"other"; db.add(GenerationNode(display_name="other",token_hash=session_token_hash(other),
            status="active",capabilities=["scene_video"])); db.commit(); sent_headers=headers(task,other)
        assert client.get(task["package_url"].replace("/package","/result"),headers=sent_headers).status_code == 404
    assert upload(client,task,image,report,headers=sent_headers).status_code in {401,409}
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind=="generated_short_reference")) == 0


def test_storage_failure_cleans_own_ciphertext_and_keeps_task(client,db,queued_case,monkeypatch):
    s=queued_case; task,image,report=setup(client,s)
    original=results.encrypt_media_file; written=[]
    def fail(source,target,*args,**kwargs):
        original(source,target,*args,**kwargs); written.append(target)
        raise RuntimeError("PRIVATE-ERROR-DETAIL")
    monkeypatch.setattr(results,"encrypt_media_file",fail)
    result=upload(client,task,image,report)
    assert result.status_code == 409 and "PRIVATE-ERROR-DETAIL" not in result.text
    assert written and all(not p.exists() for p in written)
    assert all(not p.exists() for p in s.roots)
    assert db.get(ShortSceneReferenceJob,task["id"]).result_asset_id is None


def test_snapshot_failure_preserves_committed_result_for_read_only_recovery(client,db,queued_case,monkeypatch):
    task,image,report=setup(client,queued_case)
    original=results.durable
    def fail(): raise DomainError("SHORT_REFERENCE_DURABILITY_FAILED","保存尚未确认。",503)
    monkeypatch.setattr(results,"durable",fail)
    assert upload(client,task,image,report).status_code == 503
    job=db.get(ShortSceneReferenceJob,task["id"]); asset_id=job.result_asset_id
    assert asset_id and job.status == "awaiting_reference_review"
    monkeypatch.setattr(results,"durable",original)
    recovered=client.get(task["package_url"].replace("/package","/result"),headers=headers(task))
    assert recovered.status_code == 200 and recovered.json()["result_asset_id"] == asset_id
    assert upload(client,task,image,report).json()["result_asset_id"] == asset_id


def test_concurrent_same_result_persists_exactly_one_asset(client,db,queued_case):
    task,image,report=setup(client,queued_case)
    gate=Barrier(2)
    def run():
        gate.wait(timeout=10)
        response=upload(client,task,image,report)
        assert response.status_code == 200,response.text
        return response.json()["result_asset_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run) for _ in range(2)]
        ids=[f.result(timeout=20) for f in futures]
    assert ids[0] == ids[1]
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind=="generated_short_reference")) == 1


def test_conflicting_concurrent_results_do_not_overwrite_winner(client,db,queued_case):
    task,image,report=setup(client,queued_case)
    other=png(color="red")
    gate=Barrier(2)
    def run(data):
        bound={**report,"image_sha256":hashlib.sha256(data).hexdigest()}
        gate.wait(timeout=10)
        response=upload(client,task,data,bound)
        return response.status_code,bound
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,data) for data in (image,other)]
        responses=[f.result(timeout=20) for f in futures]
    assert sorted(code for code,_ in responses) == [200,409]
    winning=next(report for code,report in responses if code==200)
    assert db.get(ShortSceneReferenceJob,task["id"]).result_report == winning
    assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.kind=="generated_short_reference")) == 1


def test_expiry_during_encrypt_does_not_publish_result(client,db,queued_case,monkeypatch):
    task,image,report=setup(client,queued_case)
    original_encrypt, original_now=results.encrypt_media_file,results.utc_now
    ended=False
    paths=[]
    def encrypt(source,target,*args,**kwargs):
        nonlocal ended
        output=original_encrypt(source,target,*args,**kwargs)
        paths.append(target); ended=True
        return output
    monkeypatch.setattr(results,"encrypt_media_file",encrypt)
    monkeypatch.setattr(results,"utc_now",lambda:original_now()+timedelta(minutes=10) if ended else original_now())
    assert upload(client,task,image,report).status_code == 409
    assert paths and all(not p.exists() for p in paths)
    assert db.get(ShortSceneReferenceJob,task["id"]).result_asset_id is None
