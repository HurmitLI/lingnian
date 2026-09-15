"""Real isolated HTTP/DB claims across queues; no external workers or GPU."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from app.api.generation_node_routes import utc_now
from app.models import GenerationNode, GenerativeMediaRequest, MemorySession, ShortSceneJob
from app.services.auth import session_token_hash
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_jobs import queued_case, create, claim, TOKEN  # noqa: F401


def legacy_claim(client, token=TOKEN):
    return client.post("/api/v1/generation-worker/tasks/claim", headers={"Authorization": f"Bearer {token}"})


def legacy_job(db, s, *, status="queued", node_id=None, expiry=None):
    job = GenerativeMediaRequest(elder_id=db.get(MemorySession, s.sid).elder_id,
        generation_type="scene_video", provider_key="home_node", actor_label="合成测试",
        request_sha256="b" * 64, status=status, assigned_node_id=node_id,
        lease_expires_at=expiry, lease_token_hash="c" * 64 if node_id else None,
        allow_external_upload=True, subject_consent=True, rights_confirmed=True,
        no_impersonation=True, attempt_count=1 if node_id else 0)
    s.node.software_version="99.0.0"
    db.add(job); db.commit()
    return job


@pytest.mark.parametrize("first", ["legacy", "short"])
def test_active_claim_blocks_both_queues(client, db, queued_case, first):
    s=queued_case
    assert create(client,s).status_code == 200
    old=legacy_job(db,s)
    taken=(legacy_claim if first == "legacy" else claim)(client)
    assert taken.status_code == 200 and taken.json() is not None
    assert legacy_claim(client).json() is None
    assert claim(client).json() is None
    db.refresh(old)
    short=db.scalar(select(ShortSceneJob))
    assert (old.status,short.status) == (("processing","queued") if first == "legacy" else ("queued","preparing"))


@pytest.mark.parametrize("first_poll", ["legacy", "short"])
@pytest.mark.parametrize("expiry", ["expired", "missing"])
def test_unknown_legacy_outcome_never_requeues_or_frees_node(client, db, queued_case, first_poll, expiry):
    s=queued_case; create(client,s)
    old=legacy_job(db,s,status="processing",node_id=s.node.id,
        expiry=utc_now()-timedelta(seconds=1) if expiry == "expired" else None)
    for poll in ((legacy_claim if first_poll == "legacy" else claim), legacy_claim, claim):
        result=poll(client); assert result.status_code == 200 and result.json() is None
    db.refresh(old)
    assert old.status == "failed" and old.error_code == "GENERATION_LEASE_EXPIRED"
    assert old.assigned_node_id == s.node.id and old.attempt_count == 1 and old.lease_token_hash is None
    assert db.scalar(select(ShortSceneJob)).status == "queued"


@pytest.mark.parametrize("state", ["expired", "missing", "unknown_failure"])
def test_legacy_poll_preserves_unresolved_short_task(client, db, queued_case, state):
    s=queued_case; created=create(client,s).json()
    task=claim(client).json()
    short=db.get(ShortSceneJob,created["id"])
    if state == "unknown_failure":
        progress=client.patch(task["package_url"].replace("/package","/progress"),
            headers={"Authorization":f"Bearer {TOKEN}","X-Lingnian-Lease":task["lease_token"]},
            json={"stage":"failed","percent":3,"error_code":"OUTCOME_UNKNOWN"})
        assert progress.status_code == 200
    else:
        short.lease_expires_at=utc_now()-timedelta(seconds=1) if state == "expired" else None
        db.commit()
    old=legacy_job(db,s)
    assert legacy_claim(client).json() is None
    assert claim(client).json() is None
    db.refresh(short); db.refresh(old)
    assert short.status == ("failed" if state == "unknown_failure" else "interrupted")
    assert short.assigned_node_id == s.node.id and old.status == "queued"


def test_other_node_can_work_without_reclaiming_unknown_task(client, db, queued_case):
    s=queued_case; create(client,s)
    old=legacy_job(db,s,status="processing",node_id=s.node.id,expiry=utc_now()-timedelta(seconds=1))
    second=TOKEN+"-second"
    db.add(GenerationNode(display_name="第二个测试节点",token_hash=session_token_hash(second),
        status="active",capabilities=["scene_video"],software_version="99.0.0")); db.commit()
    assert claim(client,token=second).json() is not None
    assert legacy_claim(client,token=second).json() is None
    db.refresh(old)
    assert old.assigned_node_id == s.node.id and old.attempt_count == 1


@pytest.mark.parametrize("queues", [("legacy","short"),("legacy","legacy"),("short","short")])
def test_concurrent_claims_only_lease_one_task(client, db, queued_case, queues):
    s=queued_case
    create(client,s); create(client,s,idempotency_key="second-short-job")
    legacy_job(db,s); legacy_job(db,s)
    gate=Barrier(2)
    def poll(fn):
        gate.wait(timeout=10)
        result=fn(client)
        assert result.status_code == 200, result.text
        return result.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(poll,legacy_claim if queue == "legacy" else claim) for queue in queues]
        results=[f.result(timeout=20) for f in futures]
    assert sum(r is not None for r in results) == 1
    db.expire_all()
    running_old=db.scalars(select(GenerativeMediaRequest).where(GenerativeMediaRequest.status=="processing")).all()
    running_short=db.scalars(select(ShortSceneJob).where(ShortSceneJob.status=="preparing")).all()
    assert len(running_old)+len(running_short) == 1


@pytest.mark.parametrize("status", ["pending_human_review","cancelled","failed"])
def test_finished_legacy_task_does_not_hold_node_forever(client, db, queued_case, status):
    s=queued_case; create(client,s)
    old=legacy_job(db,s,status=status,node_id=s.node.id)
    assert claim(client).json() is not None
    db.refresh(old)
    assert old.status == status and old.error_code is None
