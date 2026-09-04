from __future__ import annotations

import argparse
import getpass
import logging
import sys

import keyring

from .config import KEYRING_SERVICE, KEYRING_USERNAME, WorkerConfig
from .models import WorkerError
from .service import WorkerService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="聆年家用生成节点")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("run", help="持续领取并执行已授权任务")
    subcommands.add_parser("once", help="只检查并处理一个任务")
    subcommands.add_parser("doctor", help="检查云端、ComfyUI、工作流和媒体工具")
    subcommands.add_parser("credential-set", help="把节点连接密钥写入系统凭据库")
    subcommands.add_parser("credential-status", help="只检查系统凭据库中是否已有节点密钥")
    return parser


def _save_credential() -> int:
    token = getpass.getpass("粘贴一次性家用节点连接密钥（输入不会显示）：").strip()
    if len(token) < 32:
        print("连接密钥格式不正确。", file=sys.stderr)
        return 2
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)
    print("连接密钥已写入当前用户的系统凭据库，不会保存到项目文件。")
    return 0


def main() -> int:
    args = _parser().parse_args()
    if args.command == "credential-set":
        return _save_credential()
    if args.command == "credential-status":
        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or ""
        if len(token) < 32:
            print("系统凭据库中没有有效的节点连接密钥。", file=sys.stderr)
            return 2
        print("系统凭据库中的节点连接密钥已就绪。")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service: WorkerService | None = None
    try:
        config = WorkerConfig.from_env(once=args.command == "once")
        service = WorkerService(config)
        if args.command == "doctor":
            print(service.doctor())
        elif args.command == "once":
            print("已处理一项任务。" if service.run_once() else "当前没有待处理任务。")
        else:
            service.run_forever()
        return 0
    except WorkerError as exc:
        print(f"节点未就绪：{exc}", file=sys.stderr)
        return 2
    finally:
        if service:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
