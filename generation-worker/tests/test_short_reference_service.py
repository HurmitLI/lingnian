import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from lingnian_worker import short_reference_service as service
from lingnian_worker.models import PackageError, TemporaryWorkerError
from lingnian_worker.short_scene_reference import prepare_reference
from test_short_scene_reference import brief, FakeClient  # noqa: F401

JOB="11111111-1111-4111-8111-111111111111"
ASSET="22222222-2222-4222-8222-222222222222"


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(work_dir=tmp_path,node_token="PRIVATE_NODE_TOKEN_FOR_TESTS_ONLY",
        comfyui_url="http://127.0.0.1:8188",comfy_timeout_seconds=30)


class FakeApi:
    def __init__(self):
        self.claims=0; self.uploads=0; self.queries=0; self.progress=[]; self.saved=None
        self.task={"id":JOB,"brief_sha256":"a"*64,"package_sha256":"b"*64,"lease_token":"PRIVATE_LEASE_TOKEN"}
    def claim_reference(self): self.claims+=1; return self.task
    def receive(self,task,token,root):
        path=root/"brief.json"; path.write_text(json.dumps({"brief_sha256":task["brief_sha256"]}))
        return {"brief_path":str(path),"photo_path":None}
    def reference_progress(self,task,stage,percent): self.progress.append((stage,percent))
    def reference_result(self,task,image,report):
        self.uploads+=1
        self.saved={"id":task["id"],"brief_sha256":task["brief_sha256"],"status":"awaiting_reference_review",
            "visual_accepted":False,"result_asset_id":ASSET,"result_report":report,"result_verified":"bytes_and_binding_only"}
        return self.saved
    def recover_reference(self,job_id): self.queries+=1; return self.saved or {"status":"generating"}


def synthetic_runner(envelope, *, work_dir, on_generating, **kwargs):
    on_generating()
    folder=work_dir/"synthetic"; folder.mkdir(parents=True)
    image=folder/"reference.png"; Image.new("RGB",(1280,704),"blue").save(image)
    sha=hashlib.sha256(image.read_bytes()).hexdigest()
    (folder/"actual-api.json").write_text('{}')
    (folder/"job.json").write_text(json.dumps({"stage":"downloaded","prompt_id":JOB,"output_sha256":sha}))
    return {"reference_path":str(image),"sha256":sha,"binding":"c"*64,"brief_sha256":envelope["brief_sha256"],
        "visual_accepted":False,"generation_ready":False}


def test_reference_loop_uploads_once_without_approving_or_saving_tokens(config):
    api=FakeApi()
    result=service.run_once(config,api,runner=synthetic_runner)
    assert result["status"]=="awaiting_reference_review" and result["visual_accepted"] is False
    assert api.claims==1 and api.uploads==1 and ("generating",20) in api.progress
    assert not (config.work_dir/"short-reference/active.json").exists()
    for path in config.work_dir.rglob("*.json"):
        assert "PRIVATE_" not in path.read_text()
    assert not list(config.work_dir.rglob("*.mp4"))


def test_lost_success_response_recovers_without_second_upload_or_generation(config):
    api=FakeApi(); original=api.reference_result
    def lost(*args): original(*args); raise TemporaryWorkerError("lost response")
    api.reference_result=lost
    result=service.run_once(config,api,runner=synthetic_runner)
    assert result["status"]=="awaiting_reference_review"
    assert (api.claims,api.uploads,api.queries)==(1,1,1)


def test_unknown_upload_survives_restart_and_only_queries_original_result(config):
    api=FakeApi(); original=api.reference_result
    def lost(*args): raise TemporaryWorkerError("unknown")
    api.reference_result=lost
    with pytest.raises(PackageError): service.run_once(config,api,runner=synthetic_runner)
    result=service.run_once(config,api,runner=lambda *a,**kw:pytest.fail("No rerender"))
    assert result["status"]=="interrupted" and api.claims==1
    active=json.loads((config.work_dir/"short-reference/active.json").read_text())
    original(api.task,None,active["result_report"])
    result=service.run_once(config,api,runner=lambda *a,**kw:pytest.fail("No rerender"))
    assert result["status"]=="awaiting_reference_review" and api.claims==1
    saved=json.loads((config.work_dir/f"short-reference/{JOB}/delivery.json").read_text())
    assert saved["status"]=="awaiting_reference_review"


def test_unknown_claim_is_not_retried_after_restart(config):
    api=FakeApi()
    def lost(): api.claims+=1; raise TemporaryWorkerError("unknown claim")
    api.claim_reference=lost
    with pytest.raises(TemporaryWorkerError): service.run_once(config,api)
    result=service.run_once(config,api)
    assert result["status"]=="claim_outcome_unknown" and api.claims==1


def test_unknown_gpu_result_only_recovers_original_task(config):
    api=FakeApi(); starts=[]
    def interrupted(*args,**kwargs):
        kwargs["on_generating"](); starts.append(1)
        raise TemporaryWorkerError("Unknown GPU outcome")
    with pytest.raises(TemporaryWorkerError): service.run_once(config,api,runner=interrupted)
    result=service.run_once(config,api,runner=interrupted)
    assert result["status"]=="interrupted" and starts==[1] and api.claims==1 and api.uploads==0


def test_existing_task_directory_is_not_modified_or_generated_again(config):
    root=config.work_dir/f"short-reference/{JOB}"; root.mkdir(parents=True)
    (root/"delivery.json").write_text("original evidence")
    api=FakeApi()
    with pytest.raises(PackageError): service.run_once(config,api,runner=lambda *a,**kw:pytest.fail("No rerender"))
    assert (root/"delivery.json").read_text()=="original evidence"
    assert not (root/"interrupted.json").exists()
    active=json.loads((root.parent/"active.json").read_text())
    assert active["job_id"]==JOB and api.claims==1


def test_stale_lock_preserved_no_claim(config):
    workspace=config.work_dir/"short-reference"; workspace.mkdir()
    lock=workspace/"connector.lock"; lock.write_text("existing process")
    api=FakeApi()
    with pytest.raises(TemporaryWorkerError): service.run_once(config,api)
    assert api.claims==0 and lock.read_text()=="existing process"


def test_idle_does_not_leave_unknown_claim_or_call_runner(config):
    api=FakeApi(); api.task=None
    assert service.run_once(config,api,runner=lambda *a,**kw:pytest.fail("no GPU"))["status"]=="idle"
    assert not (config.work_dir/"short-reference/active.json").exists()


@pytest.mark.parametrize("change",[{"id":"../../outside"},{"package_url":"https://attacker.invalid"},
    {"brief_sha256":"wrong"},{"protocol":"legacy"},{"purpose":"video"},{"visual_accepted":True}])
def test_claim_protocol_rejects_unsafe_or_wrong_purpose_payload(change):
    task={"id":JOB,"protocol":"short-reference-v1","purpose":"short_scene_reference_generation","package_sha256":"a"*64,
        "brief_sha256":"b"*64,"lease_token":"c"*40,"package_url":service.PREFIX+f"/{JOB}/package",
        "automatic_retry":False,"visual_accepted":False}
    api=service.ReferenceApi("https://example.invalid","synthetic-token")
    api._json=lambda *a,**kw:{**task,**change}
    try:
        with pytest.raises(PackageError): api.claim_reference()
    finally: api.close()


def test_reference_executor_renews_lease_before_and_during_wait(brief,tmp_path):
    client=FakeClient(); original=client.run_workflow; pulses=[]
    def run(graph,*,on_wait,**kwargs):
        on_wait(); return original(graph,**kwargs)
    client.run_workflow=run
    prepare_reference(brief,photo=None,work_dir=tmp_path/"render",client=client,
        authorized_brief_sha256=brief["brief_sha256"],on_generating=lambda:pulses.append(1))
    assert pulses==[1,1] and len(client.calls)==1
