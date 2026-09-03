from __future__ import annotations

import getpass
import sys

SERVICE = "LingNianLocalNode"
ACCOUNT = "production-worker"


def get_token(node_id: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE, node_id)
    except Exception:
        return None


def require_token() -> str:
    token=get_token(ACCOUNT)
    if not token:
        raise RuntimeError("尚未录入生产节点令牌，请运行安全连接设置。")
    return token


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "set-token":
        print("用法：python -m lingnian_node.credentials set-token")
        return 2
    node_id = ACCOUNT
    token = getpass.getpass("LINGNIAN_NODE_TOKEN（输入不会显示）：")
    if len(token)<32:
        print("令牌格式无效，未保存。")
        return 2
    import keyring
    keyring.set_password(SERVICE, node_id, token)
    print("令牌已安全保存到 Windows 凭据管理器。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
