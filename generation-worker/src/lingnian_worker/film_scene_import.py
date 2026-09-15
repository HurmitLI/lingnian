"""Import a checked-source scene reference; review remains a separate action."""
import argparse
import json
from pathlib import Path

from .film_executor import _read, stage_scene_reference


def main():
    parser = argparse.ArgumentParser(description="导入本机场景参考，不生成、不上传、不自动批准")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = stage_scene_reference(_read(args.plan), scene_id=args.scene, image=args.image, work_dir=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
