import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from lingnian_node.cloud import PACKAGE_MAGIC, CloudClient, WorkerTask, decrypt_package, safe_extract

TOKEN="ln_node_"+"x"*40
TASK_ID="12345678abcdef"


def encrypted_package(payload: bytes) -> bytes:
    nonce=b"n"*12
    key=HKDF(algorithm=hashes.SHA256(),length=32,salt=TASK_ID.encode("ascii"),info=b"lingnian-generation-package-v1").derive(TOKEN.encode())
    return PACKAGE_MAGIC+nonce+AESGCM(key).encrypt(nonce,payload,TASK_ID.encode("ascii"))


def authorized_zip() -> bytes:
    output=io.BytesIO()
    manifest={"format":"lingnian-generation-production-package","version":1,"status":"authorized_node_job","generation_type":"photo_restore","authorization":{"rights_confirmed":True,"no_impersonation":True,"external_upload_authorized":True,"subject_consent":False},"media":[]}
    with zipfile.ZipFile(output,"w") as archive: archive.writestr("manifest.json",json.dumps(manifest))
    return output.getvalue()


def test_requires_https():
    with pytest.raises(ValueError,match="HTTPS"): CloudClient("http://cloud.example",TOKEN)


def test_heartbeat_claim_download_and_upload(tmp_path):
    seen=[]
    def handler(request: httpx.Request):
        assert request.headers["authorization"]==f"Bearer {TOKEN}"
        seen.append((request.method,request.url.path))
        if request.url.path.endswith("heartbeat"): return httpx.Response(200,json={"node_id":"node-1","accepted_capabilities":["photo_restore"],"poll_interval_seconds":8,"lease_seconds":300})
        if request.url.path.endswith("claim"): return httpx.Response(200,json={"id":TASK_ID,"generation_type":"photo_restore","lease_token":"lease-secret","lease_expires_at":"2026-09-04T00:00:00","package_url":f"/api/v1/generation-worker/tasks/{TASK_ID}/package","max_cost_cents":0,"attempt_count":1})
        if request.url.path.endswith("package"): return httpx.Response(200,headers={"X-Lingnian-Package-Version":"1"},content=encrypted_package(authorized_zip()))
        if request.url.path.endswith("result"):
            assert len(request.headers["x-content-sha256"])==64
            return httpx.Response(200,json={"request_id":TASK_ID,"status":"pending_human_review"})
        return httpx.Response(404)
    client=CloudClient("https://cloud.example",TOKEN,transport=httpx.MockTransport(handler))
    assert client.heartbeat(software_version="0.2",device_summary="RTX 5080",capabilities=["photo_restore"]).node_id=="node-1"
    task=client.claim(); assert task and task.id==TASK_ID
    encrypted=client.download_package(task,tmp_path/"job.lnpkg")
    package=decrypt_package(encrypted,tmp_path/"job.zip",worker_token=TOKEN,request_id=TASK_ID)
    assert safe_extract(package,tmp_path/"plain")["generation_type"]=="photo_restore"
    result=tmp_path/"result.png"; result.write_bytes(b"png-test")
    client.upload_result(task,result)
    assert len(seen)==4


def test_rejects_zip_slip(tmp_path):
    package=tmp_path/"bad.zip"
    with zipfile.ZipFile(package,"w") as archive: archive.writestr("../secret.txt","bad")
    with pytest.raises(ValueError,match="不安全路径"): safe_extract(package,tmp_path/"plain")
