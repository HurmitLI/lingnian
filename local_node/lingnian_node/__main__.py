from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .core import Node, Settings, STATUS_ZH
from .credentials import require_token
from .worker import ProductionWorker


def main() -> int:
    parser=argparse.ArgumentParser(description="聆年本地影像生成节点")
    parser.add_argument("--root",default=str(Path(__file__).parents[1]/"runtime"))
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--health",action="store_true")
    parser.add_argument("--production",action="store_true")
    parser.add_argument("--api-base",default=os.getenv("LINGNIAN_API_BASE"))
    args=parser.parse_args()
    if args.production:
        if not args.api_base: raise SystemExit("缺少 LINGNIAN_API_BASE 云端地址。")
        worker=ProductionWorker(api_base=args.api_base,token=require_token(),root=Path(args.root),comfy_root=Path(r"E:\LingNianAI\ComfyUI"))
        if args.health:
            beat=worker.heartbeat(); print(json.dumps({"cloud":"online","node_id":beat.node_id,"capabilities":beat.accepted_capabilities},ensure_ascii=False)); return 0
        if args.once:
            worker.heartbeat(); worker.once(); return 0
        worker.run_forever(); return 0
    node=Node(Settings(Path(args.root)))
    if args.health:
        print(json.dumps(node.comfy.health(),ensure_ascii=False,indent=2)); return 0
    while True:
        try:
            result=node.once()
            print(f"[{STATUS_ZH['succeeded']}] {result}" if result else f"[{STATUS_ZH['idle']}] 暂无任务")
        except Exception as exc: print(f"[{STATUS_ZH['failed']}] {exc}")
        if args.once: return 0
        time.sleep(10)


if __name__ == "__main__": raise SystemExit(main())
