"""Synthetic reference deliveries through real family/node crypto, never a GPU."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api import short_scene_reference_job_routes as intents
from app.api import short_scene_reference_worker_routes as worker
from app.core.config import get_settings
from app.models import GenerationNode, GenerativeMediaRequest, MediaAsset, ShortSceneJob, ShortSceneReferenceJob
from app.services.auth import session_token_hash
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_jobs import queued_case, create, claim as short_claim, TOKEN  # noqa: F401
from test_short_scene_reference_jobs import prepare, submit
from test_generation_capacity import legacy_job, legacy_claim

URL="/api/v1/generation-worker/short-reference/tasks/claim"


def claim(client, token=TOKEN, payload=None):
    return client.post(URL, json=payload or {"protocol":"short-reference-v1"},
                       headers={"Authorization":f"Bearer {token}"})


def headers(task, token=TOKEN):
    return {"Authorization":f"Bearer {token}", "X-Lingnian-Lease":task["lease_token"]}


@pytest.mark.parametrize("mode", ["illustrative","user_photo"])
def test_family_ciphertext_to_node_ciphertext_to_strict_receiver(client, db, queued_case, tmp_path, monkeypatch, mode):
    prepared=prepare(client,queued_case,mode)
    made=submit(client,prepared); assert made.status_code == 200
    response=claim(client); assert response.status_code == 200, response.text
    task=response.json(); assert task["id"] == made.json()["id"]
    assert task["purpose"] == "short_scene_reference_generation" and task["visual_accepted"] is False
    assert claim(client).json() is None
    result=client.get(task["package_url"],headers=headers(task))
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.content.startswith(b"LINGNIANPKG1")
    assert all(not p.exists() for p in queued_case.roots)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"generation-worker"/"src"))
    from lingnian_worker.package import decrypt_package
    from lingnian_worker.short_scene_reference_bundle import receive_reference_bundle
    plain=decrypt_package(result.content,token=TOKEN,request_id=task["id"])
    assert hashlib.sha256(plain).hexdigest() == task["package_sha256"]
    path=tmp_path/"reference.zip"; path.write_bytes(plain)
    receipt=receive_reference_bundle(path,expected_sha256=task["package_sha256"],
        expected_brief_sha256=task["brief_sha256"],output_dir=tmp_path/"received")
    assert receipt["comfyui_called"] is False and receipt["generation_authorized"] is False
    assert (receipt["photo_path"] is None) == (mode == "illustrative")
    if mode == "user_photo": assert Path(receipt["photo_path"]).read_bytes() == queued_case.image
    with pytest.raises(Exception): decrypt_package(result.content,token=TOKEN+"wrong",request_id=task["id"])
    recovered=client.get(prepared[0]+"/by-request/reference-trial-key").json()
    assert recovered["status"] == "preparing" and recovered["generation_submitted"] is None
    assert "lease_token" not in recovered and "package_asset_id" not in recovered


def test_reference_progress_does_not_approve_image_and_rejects_regression(client,queued_case):
    submit(client,prepare(client,queued_case)); task=claim(client).json()
    url=task["package_url"].replace("/package","/progress")
    result=client.patch(url,headers=headers(task),json={"stage":"generating","percent":30})
    assert result.status_code == 200 and result.json()["visual_accepted"] is False
    result=client.patch(url,headers=headers(task),json={"stage":"generating","percent":5})
    assert result.json()["progress_percent"] == 30
    assert client.patch(url,headers=headers(task),json={"stage":"preparing","percent":10}).status_code == 409
    assert client.patch(url,headers=headers(task),json={"stage":"failed","percent":30}).status_code == 409
    assert client.patch(url,headers=headers(task),json={"stage":"complete","percent":99}).status_code == 422
    assert client.patch(url,headers=headers(task),json={"stage":"generating","percent":30,"visual_accepted":True}).status_code == 422


@pytest.mark.parametrize("damage", ["expired","missing","unknown"])
def test_reference_uncertain_outcome_blocks_all_claims_without_resubmit(client,db,queued_case,damage):
    s=queued_case; submit(client,prepare(client,s)); task=claim(client).json()
    job=db.get(ShortSceneReferenceJob,task["id"])
    if damage == "unknown":
        result=client.patch(task["package_url"].replace("/package","/progress"),headers=headers(task),
            json={"stage":"failed","percent":10,"error_code":"OUTCOME_UNKNOWN"})
        assert result.status_code == 200
    else:
        job.lease_expires_at=worker.utc_now()-timedelta(seconds=1) if damage == "expired" else None
        db.commit()
    create(client,s); legacy_job(db,s)
    for poll in (legacy_claim,short_claim,claim): assert poll(client).json() is None
    db.refresh(job)
    assert job.assigned_node_id == s.node.id
    assert job.status == ("failed" if damage == "unknown" else "interrupted")
    assert client.get(task["package_url"],headers=headers(task)).status_code == 409


@pytest.mark.parametrize("active_queue", ["legacy","short"])
def test_reference_cannot_overlap_other_queue(client,db,queued_case,active_queue):
    s=queued_case; submit(client,prepare(client,s))
    if active_queue == "legacy":
        legacy_job(db,s); taken=legacy_claim(client)
    else:
        create(client,s); taken=short_claim(client)
    assert taken.json() is not None
    assert claim(client).json() is None


def test_reference_cancel_revokes_lease_and_never_requeues(client,queued_case):
    prepared=prepare(client,queued_case); submit(client,prepared); task=claim(client).json()
    result=client.post(f"/api/v1/short-reference-jobs/{task['id']}/cancel")
    assert result.status_code == 200 and result.json()["status"] == "cancelled"
    assert client.get(task["package_url"],headers=headers(task)).status_code == 409
    assert client.patch(task["package_url"].replace("/package","/progress"),headers=headers(task),
        json={"stage":"generating","percent":20}).status_code == 409
    assert submit(client,prepared).json()["status"] == "cancelled"
    assert claim(client).json() is None


def test_node_auth_capability_and_task_lease_are_independent(client,db,queued_case):
    submit(client,prepare(client,queued_case))
    assert client.post(URL,json={"protocol":"short-reference-v1"}).status_code == 401
    assert claim(client,payload={"protocol":"native-short-scene-v1"}).status_code == 422
    task=claim(client).json()
    assert client.get(task["package_url"],headers={"Authorization":f"Bearer {TOKEN}"}).status_code == 409
    other=TOKEN+"other"
    db.add(GenerationNode(display_name="other",token_hash=session_token_hash(other),status="active",capabilities=["scene_video"]))
    db.commit()
    assert client.get(task["package_url"],headers=headers(task,other)).status_code == 409
    queued_case.node.capabilities=["photo_restore"]; db.commit()
    assert claim(client).status_code == 403
    queued_case.node.status="revoked"; db.commit()
    assert client.get(task["package_url"],headers=headers(task)).status_code == 401


def test_tampered_ciphertext_is_rejected_without_private_error(client,db,queued_case):
    submit(client,prepare(client,queued_case)); task=claim(client).json()
    asset=db.get(MediaAsset,db.get(ShortSceneReferenceJob,task["id"]).package_asset_id)
    path=get_settings().resolved_asset_root/asset.relative_path
    path.write_bytes(b"private damaged fixture")
    result=client.get(task["package_url"],headers=headers(task))
    assert result.status_code == 409 and str(path) not in result.text


def test_cancel_during_package_preparation_prevents_delivery(client,queued_case,monkeypatch):
    submit(client,prepare(client,queued_case)); task=claim(client).json()
    original=worker.encrypt_package
    def cancel(*args,**kwargs):
        original(*args,**kwargs)
        assert client.post(f"/api/v1/short-reference-jobs/{task['id']}/cancel").status_code == 200
    monkeypatch.setattr(worker,"encrypt_package",cancel)
    assert client.get(task["package_url"],headers=headers(task)).status_code == 409
    assert all(not p.exists() for p in queued_case.roots)


def test_node_revoked_during_package_preparation_cannot_receive_data(client,db,queued_case,monkeypatch):
    submit(client,prepare(client,queued_case)); task=claim(client).json()
    original=worker.encrypt_package
    def revoke(*args,**kwargs):
        original(*args,**kwargs)
        queued_case.node.status="revoked"; db.commit()
    monkeypatch.setattr(worker,"encrypt_package",revoke)
    assert client.get(task["package_url"],headers=headers(task)).status_code == 401
    assert all(not p.exists() for p in queued_case.roots)


def test_snapshot_failure_keeps_original_lease_but_does_not_return_it(client,db,queued_case,monkeypatch):
    submit(client,prepare(client,queued_case))
    settings=get_settings()
    monkeypatch.setattr(intents,"get_settings",lambda:SimpleNamespace(formal_auth_required=True,resolved_asset_root=settings.resolved_asset_root))
    monkeypatch.setattr(intents,"persist_configured_database_snapshot",lambda _:None)
    response=claim(client)
    assert response.status_code == 503 and "lease_token" not in response.text
    job=db.scalar(select(ShortSceneReferenceJob))
    assert job.status == "preparing" and job.assigned_node_id == queued_case.node.id
    assert claim(client).status_code == 503


def test_three_queues_simultaneously_claim_one_node_only_once(client,db,queued_case):
    s=queued_case; submit(client,prepare(client,s)); create(client,s); legacy_job(db,s)
    gate=Barrier(3)
    def run(fn):
        gate.wait(timeout=10)
        response=fn(client); assert response.status_code == 200,response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run,fn) for fn in (claim,legacy_claim,short_claim)]
        results=[f.result(timeout=20) for f in futures]
    assert sum(r is not None for r in results) == 1
    db.expire_all()
    assert sum(len(db.scalars(select(model).where(model.status.in_(("preparing","processing")))).all())
               for model in (GenerativeMediaRequest,ShortSceneJob,ShortSceneReferenceJob)) == 1
