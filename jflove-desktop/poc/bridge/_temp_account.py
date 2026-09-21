"""
P0-2 临时测试账号的创建 / 删除

**必须用 server 的 venv 运行**（需要 bcrypt；desktop venv 没装）：

    jflove-server\\venv-win\\Scripts\\python.exe _temp_account.py create
    jflove-server\\venv-win\\Scripts\\python.exe _temp_account.py drop

为什么要它：P0-2 要验证「JS → 桥 → http_client.py → 真实加密调用」，
而除 `/health`、`key-exchange`、`admin-exists` 之外的接口都需要 JWT，
dev 库里现有账号的口令是 bcrypt 哈希、无法反推，所以需要一个已知口令的临时账号。
用户已批准在 **dev 库**（`jflove-db/jflove-dev.db`，AGENTS.md §5.1 允许开发期操作）里建它。

安全约束：
  - 口令随机生成，**只写进凭据文件，不打印到控制台**
  - 只增删这一个用户名，绝不触碰其他行
  - `drop` 会连历史软删除的同名行一起清掉，保证不留痕
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# jflove-desktop/poc/bridge/_temp_account.py → parents[3] = 仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "jflove-db" / "jflove-dev.db"
CRED_PATH = Path(__file__).resolve().parent / ".tmp_account.json"

DEFAULT_USERNAME = "poc_bridge_probe"


def _now() -> str:
    """与服务端 user_repository._now() 保持同一格式（UTC ISO）"""
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"找不到 dev 库：{DB_PATH}")
    return sqlite3.connect(str(DB_PATH))


def _active_count(con: sqlite3.Connection, username: str) -> int:
    cur = con.execute(
        "SELECT COUNT(*) FROM users WHERE username = ? AND deleted_at IS NULL",
        (username,),
    )
    return int(cur.fetchone()[0])


def cmd_create(args: argparse.Namespace) -> int:
    """创建临时账号并写出凭据文件"""
    try:
        import bcrypt
    except ImportError:
        raise SystemExit(
            "缺少 bcrypt —— 本脚本必须用 server 的 venv 运行：\n"
            "  jflove-server\\venv-win\\Scripts\\python.exe "
            "jflove-desktop\\poc\\bridge\\_temp_account.py create"
        )

    password = secrets.token_urlsafe(18)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    con = _connect()
    try:
        existing = _active_count(con, args.username)
        if existing and not args.force:
            raise SystemExit(
                f"已存在活跃用户 {args.username!r}（{existing} 行）；"
                "换名字或加 --force（--force 只会新增一行，不会改已有行）"
            )
        now = _now()
        cur = con.execute(
            "INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (args.username, password_hash, args.role, now, now),
        )
        con.commit()
        user_id = cur.lastrowid
    finally:
        con.close()

    CRED_PATH.write_text(
        json.dumps(
            {
                "username": args.username,
                "password": password,
                "role": args.role,
                "user_id": user_id,
                "created_at": _now(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(CRED_PATH, 0o600)
    except OSError:
        pass

    print(f"[OK] 已创建临时账号：id={user_id} username={args.username} role={args.role}")
    print(f"     口令已写入凭据文件（不打印）：{CRED_PATH}")
    print(f"     用完请执行 drop 清理：drop --username {args.username}")
    return 0


def cmd_drop(args: argparse.Namespace) -> int:
    """删除临时账号（含历史软删除同名行）并清理凭据文件"""
    con = _connect()
    try:
        names = [args.username]
        if CRED_PATH.exists() and not args.keep_cred:
            try:
                names.append(json.loads(CRED_PATH.read_text(encoding="utf-8"))["username"])
            except (OSError, ValueError, KeyError):
                pass
        removed = 0
        for name in dict.fromkeys(names):
            cur = con.execute("DELETE FROM users WHERE username = ?", (name,))
            if cur.rowcount:
                print(f"[OK] 已删除 username={name} 的行数：{cur.rowcount}")
            removed += cur.rowcount
        con.commit()
        print(f"[OK] 共删除 {removed} 行")
    finally:
        con.close()

    if CRED_PATH.exists():
        CRED_PATH.unlink()
        print(f"[OK] 已删除凭据文件 {CRED_PATH}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """查看临时账号与凭据文件状态（不打印口令）"""
    con = _connect()
    try:
        rows = list(
            con.execute(
                "SELECT id, username, role, enabled, deleted_at FROM users"
                " WHERE username LIKE 'poc_%' ORDER BY id"
            )
        )
    finally:
        con.close()
    print("库中 poc_* 账号：", rows or "（无）")
    print("凭据文件：", "存在" if CRED_PATH.exists() else "不存在", CRED_PATH)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-2 临时测试账号管理（需 server venv）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="创建临时账号")
    p_create.add_argument("--username", default=DEFAULT_USERNAME)
    p_create.add_argument("--role", default="admin", choices=["admin", "user"])
    p_create.add_argument("--force", action="store_true", help="同名用户已存在时仍新增一行")
    p_create.set_defaults(func=cmd_create)

    p_drop = sub.add_parser("drop", help="删除临时账号并清理凭据文件")
    p_drop.add_argument("--username", default=DEFAULT_USERNAME)
    p_drop.add_argument("--keep-cred", action="store_true", help="保留凭据文件")
    p_drop.set_defaults(func=cmd_drop)

    p_status = sub.add_parser("status", help="查看状态")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
