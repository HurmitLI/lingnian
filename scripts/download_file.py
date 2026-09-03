from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("url"); parser.add_argument("target")
    args=parser.parse_args(); target=Path(args.target); target.parent.mkdir(parents=True,exist_ok=True)
    done=target.stat().st_size if target.exists() else 0
    request=urllib.request.Request(args.url,headers={"Range":f"bytes={done}-","User-Agent":"LingNian-Local-Node/0.1"} if done else {"User-Agent":"LingNian-Local-Node/0.1"})
    with urllib.request.urlopen(request,timeout=60) as response, target.open("ab" if done and response.status==206 else "wb") as output:
        while chunk:=response.read(8*1024*1024): output.write(chunk)
    return 0


if __name__=="__main__": raise SystemExit(main())
