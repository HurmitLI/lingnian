from __future__ import annotations

import getpass
import sys

SERVICE = "LingNianLocalNode"


def get_token(node_id: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE, node_id)
    except Exception:
        return None


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "set-token":
        print("用法：python -m lingnian_node.credentials set-token")
        return 2
    node_id = input("节点编号：").strip()
    token = getpass.getpass("节点令牌（不会显示）：")
    import keyring
    keyring.set_password(SERVICE, node_id, token)
    print("令牌已安全保存到 Windows 凭据管理器。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
