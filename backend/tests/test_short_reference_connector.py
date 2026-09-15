"""Full isolated HTTP/crypto/receiver/executor/result contract; ComfyUI alone is simulated."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app.models import ShortSceneReferenceJob
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_jobs import queued_case, TOKEN  # noqa: F401
from test_short_scene_reference_jobs import prepare, submit


@pytest.mark.parametrize("mode", ["illustrative", "user_photo"])
def test_reference_connector_full_contract_with_only_comfy_simulated(client,db,queued_case,tmp_path,monkeypatch,mode):
    made=submit(client,prepare(client,queued_case,mode)); assert made.status_code==200
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"generation-worker"/"src"))
    from lingnian_worker import short_reference_service as service
    calls=[]; photos=[]
    class ComfyFixture:
        def __init__(self,base_url,*args,**kwargs): self.base_url=base_url
        def close(self): pass
        def check_reference_graph(self,graph): assert graph["10"]["inputs"]["steps"]==4
        def upload_image(self,path): photos.append(path.read_bytes()); return path.name
        def run_workflow(self,graph,*,output_path,journal_path,on_wait):
            calls.append(graph); on_wait()
            Image.new("RGB",(1280,704),"blue").save(output_path)
            journal_path.write_text(json.dumps({"stage":"downloaded","prompt_id":"33333333-3333-4333-8333-333333333333",
                "output_sha256":hashlib.sha256(output_path.read_bytes()).hexdigest()}))
            return output_path
    monkeypatch.setattr(service,"ComfyUiClient",ComfyFixture)
    api=service.ReferenceApi("http://testserver",TOKEN)
    api._client.close(); api._client=client
    client.headers["Authorization"]=f"Bearer {TOKEN}"
    config=SimpleNamespace(work_dir=tmp_path/"node",node_token=TOKEN,comfyui_url="http://127.0.0.1:8188",comfy_timeout_seconds=20)
    try:
        state=service.run_once(config,api)
        assert state["status"]=="awaiting_reference_review" and state["visual_accepted"] is False
        job=db.get(ShortSceneReferenceJob,made.json()["id"])
        assert job.result_asset_id and job.status=="awaiting_reference_review"
        assert len(calls)==1 and bool(photos)==(mode=="user_photo")
        if photos: assert photos==[queued_case.image]
        assert service.run_once(config,api)["status"]=="idle" and len(calls)==1
        assert not list(config.work_dir.rglob("*.mp4")) and not list(config.work_dir.rglob("input-review.json"))
        for path in config.work_dir.rglob("*.json"):
            assert TOKEN not in path.read_text(encoding="utf-8")
    finally:
        client.headers.pop("Authorization",None)
