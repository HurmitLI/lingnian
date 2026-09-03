from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, Field, field_validator


STATUS_ZH = {
    "idle": "空闲", "claiming": "领取任务", "downloading": "下载素材",
    "generating": "生成中", "uploading": "上传中", "succeeded": "成功", "failed": "失败",
}


class Task(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    kind: str
    authorized: bool
    subject_consent: bool = False
    purpose: str = Field(min_length=2, max_length=500)
    cost_limit_cents: int = Field(ge=0, le=100000)
    estimated_cost_cents: int = Field(ge=0, le=100000)
    workflow: dict
    assets: list[dict] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        allowed = {"photo_restore", "story_image", "portrait_motion", "lip_sync", "short_video", "film_compose"}
        if value not in allowed:
            raise ValueError("不支持的任务类型")
        return value


@dataclass
class Settings:
    root: Path
    comfy_url: str = "http://127.0.0.1:8188"
    max_cost_cents: int = 100
    timeout_seconds: int = 900


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,updated REAL NOT NULL)")
        self.db.commit()

    def status(self, task_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row[0] if row else None

    def set(self, task_id: str, status: str, error: str | None = None) -> None:
        self.db.execute("INSERT INTO tasks(id,status,attempts,error,updated) VALUES(?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,attempts=tasks.attempts+1,error=excluded.error,updated=excluded.updated", (task_id,status,error,time.time()))
        self.db.commit()

    def recover(self) -> int:
        cur = self.db.execute("UPDATE tasks SET status='retry',updated=? WHERE status IN ('claiming','downloading','generating','uploading')", (time.time(),))
        self.db.commit()
        return cur.rowcount


class ComfyClient:
    def __init__(self, base_url: str, timeout: int):
        if base_url.rstrip("/") != "http://127.0.0.1:8188":
            raise ValueError("ComfyUI 地址必须是本机 127.0.0.1:8188。")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        return httpx.get(f"{self.base_url}/system_stats", timeout=10).json()

    def run(self, workflow: dict) -> dict:
        client_id = str(uuid.uuid4())
        response = httpx.post(f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
        response.raise_for_status()
        prompt_id = response.json()["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            history = httpx.get(f"{self.base_url}/history/{prompt_id}", timeout=15).json()
            if prompt_id in history:
                item = history[prompt_id]
                if item.get("status", {}).get("status_str") == "error":
                    raise RuntimeError("ComfyUI 工作流执行失败。")
                return item
            time.sleep(1)
        raise TimeoutError("ComfyUI 生成超时。")


class MockCloud:
    def __init__(self, root: Path):
        self.inbox = root / "inbox"; self.claimed = root / "claimed"; self.uploaded = root / "uploaded"
        for path in (self.inbox, self.claimed, self.uploaded): path.mkdir(parents=True, exist_ok=True)

    def claim(self) -> tuple[Task, Path] | None:
        candidates = sorted(self.inbox.glob("*.json"))
        if not candidates: return None
        source = candidates[0]; target = self.claimed / source.name
        source.replace(target)
        return Task.model_validate_json(target.read_text(encoding="utf-8")), target

    def upload(self, task: Task, payload: bytes, key: bytes) -> Path:
        nonce = os.urandom(12)
        encrypted = b"LNNODE1" + nonce + AESGCM(key).encrypt(nonce, payload, task.id.encode())
        target = self.uploaded / f"{task.id}.nnresult"
        target.write_bytes(encrypted)
        (self.uploaded / f"{task.id}.json").write_text(json.dumps({"task_id":task.id,"sha256":hashlib.sha256(encrypted).hexdigest(),"size":len(encrypted)},ensure_ascii=False,indent=2),encoding="utf-8")
        return target


class Node:
    def __init__(self, settings: Settings):
        self.settings=settings; self.store=Store(settings.root/"node.db")
        self.comfy=ComfyClient(settings.comfy_url,settings.timeout_seconds)
        self.cloud=MockCloud(settings.root/"mock-cloud")
        self.key_path=settings.root/"mock-result.key"
        if not self.key_path.exists(): self.key_path.write_bytes(AESGCM.generate_key(bit_length=256))
        self.store.recover()

    def validate(self, task: Task) -> None:
        if not task.authorized: raise PermissionError("任务没有明确用户授权，已拒绝。")
        if task.kind in {"portrait_motion","lip_sync"} and not task.subject_consent: raise PermissionError("人物任务缺少本人专项授权，已拒绝。")
        if task.estimated_cost_cents > min(task.cost_limit_cents,self.settings.max_cost_cents): raise PermissionError("任务预计成本超过上限，已拒绝。")

    def once(self) -> Path | None:
        claimed=self.cloud.claim()
        if not claimed: return None
        task,_=claimed
        if self.store.status(task.id)=="succeeded": return None
        try:
            self.store.set(task.id,"claiming"); self.validate(task)
            self.store.set(task.id,"generating"); result=self.comfy.run(task.workflow)
            self.store.set(task.id,"uploading")
            payload=json.dumps(result,ensure_ascii=False).encode()
            target=self.cloud.upload(task,payload,self.key_path.read_bytes())
            self.store.set(task.id,"succeeded"); return target
        except Exception as exc:
            message=str(exc).replace(os.environ.get("USERNAME","__none__"),"***")[:500]
            self.store.set(task.id,"failed",message)
            raise
