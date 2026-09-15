"""Recover one known local ComfyUI result without cloud credentials or generation."""
import argparse
from pathlib import Path

from .comfyui import ComfyUiClient
from .models import WorkerError


def main():
    parser = argparse.ArgumentParser(description="只恢复已核对的本机视频，不重新生成")
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    client = ComfyUiClient("http://127.0.0.1:8188", Path("unused-plan.json"), timeout_seconds=60)
    try:
        path = client.recover_completed_video(args.prompt_id, expected_prefix=args.prefix,
                                             expected_sha256=args.sha256, output_path=args.output)
        print(f"已恢复并核对视频；未重新生成，不代表画面验收通过：{path}")
        return 0
    except WorkerError as exc:
        print(f"恢复未完成：{exc}")
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
