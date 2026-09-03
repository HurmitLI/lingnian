from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, Field

PACKAGE_MAGIC = b"LINGNIANPKG1"
MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 8 * 1024 * 1024 * 1024


class WorkerTask(BaseModel):
    id: str
    generation_type: str
    lease_token: str
    lease_expires_at: str
    package_url: str
    max_cost_cents: int = Field(ge=0)
    attempt_count: int = Field(ge=1)


@dataclass(frozen=True)
class Heartbeat:
    node_id: str
    accepted_capabilities: tuple[str, ...]
    poll_interval_seconds: int
    lease_seconds: int


class CloudClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None):
        base = base_url.rstrip("/") + "/"
        parsed = urlparse(base)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            raise ValueError("生产云端地址必须使用 HTTPS。")
        if len(token) < 32:
            raise ValueError("节点连接令牌格式无效。")
        self.base_url = base
        self._token = token
        self.client = httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "LingNian-RTX5080-Worker/0.2"},
            timeout=httpx.Timeout(60, connect=15),
            verify=True,
            follow_redirects=False,
            transport=transport,
        )

    def _url(self, path: str) -> str:
        url = urljoin(self.base_url, path.lstrip("/"))
        if urlparse(url).netloc != urlparse(self.base_url).netloc:
            raise ValueError("云端返回了不受信任的跨站地址。")
        return url

    def heartbeat(self, *, software_version: str, device_summary: str, capabilities: list[str]) -> Heartbeat:
        response = self.client.post("api/v1/generation-worker/heartbeat", json={"software_version":software_version,"device_summary":device_summary,"capabilities":capabilities})
        response.raise_for_status(); data=response.json()
        return Heartbeat(data["node_id"],tuple(data["accepted_capabilities"]),int(data["poll_interval_seconds"]),int(data["lease_seconds"]))

    def claim(self) -> WorkerTask | None:
        response=self.client.post("api/v1/generation-worker/tasks/claim")
        response.raise_for_status(); data=response.json()
        return WorkerTask.model_validate(data) if data else None

    def download_package(self, task: WorkerTask, target: Path) -> Path:
        target.parent.mkdir(parents=True,exist_ok=True); temporary=target.with_suffix(target.suffix+".part")
        size=0
        try:
            with self.client.stream("GET",self._url(task.package_url),headers={"X-Lingnian-Lease":task.lease_token}) as response:
                response.raise_for_status()
                if response.headers.get("X-Lingnian-Package-Version") != "1": raise ValueError("素材包版本不受支持。")
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(1024*1024):
                        size+=len(chunk)
                        if size>MAX_PACKAGE_BYTES: raise ValueError("加密素材包超过安全大小限制。")
                        output.write(chunk)
            os.replace(temporary,target); return target
        except Exception:
            temporary.unlink(missing_ok=True); raise

    def progress(self, task: WorkerTask, percent: int, stage: str) -> None:
        response=self.client.patch(f"api/v1/generation-worker/tasks/{task.id}/progress",headers={"X-Lingnian-Lease":task.lease_token},json={"progress_percent":percent,"progress_stage":stage[:80]})
        response.raise_for_status()

    def fail(self, task: WorkerTask, code: str, message: str, retryable: bool=True) -> None:
        response=self.client.post(f"api/v1/generation-worker/tasks/{task.id}/fail",headers={"X-Lingnian-Lease":task.lease_token},json={"error_code":code,"message":message[:500],"retryable":retryable})
        response.raise_for_status()

    def upload_result(self, task: WorkerTask, result: Path, *, actual_cost_cents: int=0) -> None:
        digest=file_sha256(result)
        with result.open("rb") as source:
            response=self.client.post(f"api/v1/generation-worker/tasks/{task.id}/result",headers={"X-Lingnian-Lease":task.lease_token,"X-Content-Sha256":digest},data={"actual_cost_cents":str(actual_cost_cents)},files={"result":(result.name,source,_mime(result))},timeout=300)
        response.raise_for_status()


def decrypt_package(source: Path, target_zip: Path, *, worker_token: str, request_id: str) -> Path:
    total=source.stat().st_size
    minimum=len(PACKAGE_MAGIC)+12+16
    if total<minimum: raise ValueError("加密素材包格式无效。")
    with source.open("rb") as readable:
        if readable.read(len(PACKAGE_MAGIC))!=PACKAGE_MAGIC: raise ValueError("加密素材包标识无效。")
        nonce=readable.read(12); readable.seek(total-16); tag=readable.read(16); readable.seek(len(PACKAGE_MAGIC)+12)
        key=HKDF(algorithm=hashes.SHA256(),length=32,salt=request_id.encode("ascii"),info=b"lingnian-generation-package-v1").derive(worker_token.encode("utf-8"))
        decryptor=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor(); decryptor.authenticate_additional_data(request_id.encode("ascii"))
        remaining=total-minimum; temporary=target_zip.with_suffix(".part")
        try:
            with temporary.open("wb") as output:
                while remaining:
                    chunk=readable.read(min(1024*1024,remaining)); remaining-=len(chunk); output.write(decryptor.update(chunk))
                output.write(decryptor.finalize())
            os.replace(temporary,target_zip); return target_zip
        except Exception:
            temporary.unlink(missing_ok=True); raise


def safe_extract(package: Path, target: Path) -> dict:
    target.mkdir(parents=True,exist_ok=False); total=0
    try:
        with zipfile.ZipFile(package) as archive:
            for info in archive.infolist():
                path=PurePosixPath(info.filename)
                if (info.external_attr >> 16) & 0o170000 == 0o120000: raise ValueError("素材包包含符号链接。")
                if path.is_absolute() or ".." in path.parts or info.is_dir():
                    if info.is_dir(): continue
                    raise ValueError("素材包包含不安全路径。")
                total+=info.file_size
                if total>MAX_EXTRACTED_BYTES: raise ValueError("素材包解压后超过安全大小限制。")
                destination=target.joinpath(*path.parts); destination.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(info) as source,destination.open("xb") as output: shutil.copyfileobj(source,output,1024*1024)
        manifest_path=target/"manifest.json"
        import json
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format")!="lingnian-generation-production-package" or manifest.get("version")!=1 or manifest.get("status")!="authorized_node_job": raise ValueError("素材包授权清单无效。")
        auth=manifest.get("authorization",{})
        if not auth.get("rights_confirmed") or not auth.get("no_impersonation") or not auth.get("external_upload_authorized"): raise PermissionError("素材包没有完整授权。")
        if manifest.get("generation_type")!="photo_restore" and not auth.get("subject_consent"): raise PermissionError("演绎任务缺少本人专项授权。")
        for media in manifest.get("media",[]):
            media_path=target.joinpath(*PurePosixPath(media["path"]).parts)
            if file_sha256(media_path)!=media["sha256"]: raise ValueError("素材完整性校验失败。")
        return manifest
    except Exception:
        shutil.rmtree(target,ignore_errors=True); raise


def file_sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as source:
        while chunk:=source.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()


def _mime(path: Path) -> str:
    return {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".mp4":"video/mp4"}.get(path.suffix.lower(),"application/octet-stream")
