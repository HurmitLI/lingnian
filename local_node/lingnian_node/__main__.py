from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .core import Node, Settings, STATUS_ZH


def main() -> int:
    parser=argparse.ArgumentParser(description="聆年本地影像生成节点")
    parser.add_argument("--root",default=str(Path(__file__).parents[1]/"runtime"))
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--health",action="store_true")
    args=parser.parse_args(); node=Node(Settings(Path(args.root)))
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
