#!/usr/bin/env python3
"""从后端 OpenAPI 生成官网接口文档数据（唯一数据源）。

设计原则：
    **不手抄接口清单。** OpenAPI 由 FastAPI 从代码自动生成（`app.openapi()`），
    本脚本只做「取出来 + 归一化 + 落盘」，官网页面读 `website/content/api-openapi.json`。

    为什么不用在线 `/openapi.json`：官网是静态站，构建期不能依赖后端在线；
    且官网要对**当前版本**负责，应该锁在当前代码生成的结果上。

用法：
    python website/scripts/gen_api_docs.py            # 生成
    python website/scripts/gen_api_docs.py --check    # 只比对，不一致退出 1（门禁用）

退出码：
    0 = 成功（--check 时表示与磁盘一致）
    1 = 生成失败或 --check 发现不一致
    2 = 环境错误（缺依赖 / 缺源码）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

WEBSITE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WEBSITE_DIR.parent
SERVER_DIR = REPO_ROOT / "jflove-server"
CONTENT_DIR = WEBSITE_DIR / "content"
OUT_FILE = "api-openapi.json"

# 明文白名单（与 main.py::_PLAIN_PATHS 对应；这里只用于给接口打标记）
PLAIN_PATHS = ("/health", "/api/v1/auth/key-exchange", "/api/v1/auth/admin-exists")

HTTP_METHODS = ("get", "post", "put", "delete", "patch")


def _load_schema() -> dict:
    """导入后端 app 并取 OpenAPI。不启服务、不连数据库。"""
    sys.path.insert(0, str(SERVER_DIR))
    try:
        from src.main import app  # noqa: PLC0415 - 必须在 sys.path 之后导入
    except Exception as exc:  # noqa: BLE001 - 依赖缺失要报清楚
        print(f"[api-docs][FATAL] 无法导入后端 app：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("提示：需在装有后端依赖的解释器下运行（如 jflove-server/venv-linux/bin/python）", file=sys.stderr)
        sys.exit(2)
    return app.openapi()


def build_payload(schema: dict) -> dict:
    """把 OpenAPI 归一化成官网要用的结构。"""
    groups: dict[str, dict] = {}
    operations: list[dict] = []

    for path, item in sorted(schema.get("paths", {}).items()):
        for method in HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            tags = op.get("tags") or ["未分组"]
            tag = tags[0]
            op_id = op.get("operationId", "")
            record = {
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary", ""),
                "description": op.get("description", ""),
                "tag": tag,
                "operationId": op_id,
                "plain": path in PLAIN_PATHS,
                "hasRequestBody": "requestBody" in op,
            }
            operations.append(record)
            bucket = groups.setdefault(tag, {"tag": tag, "count": 0, "operations": []})
            bucket["count"] += 1
            bucket["operations"].append(record)

    # 组内按路径排序，组间按数量降序（数量相同按 tag 名）
    for bucket in groups.values():
        bucket["operations"].sort(key=lambda r: (r["path"], r["method"]))
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], g["tag"]))

    info = schema.get("info", {})
    payload = {
        "source": "jflove-server app.openapi()",
        "openapi": schema.get("openapi", ""),
        "info": {"title": info.get("title", ""), "version": info.get("version", "")},
        "total": len(operations),
        "groupCount": len(ordered),
        "groups": ordered,
        "plainPaths": list(PLAIN_PATHS),
    }
    return payload


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="从 OpenAPI 生成官网接口文档数据")
    parser.add_argument("--check", action="store_true", help="只比对不写盘")
    args = parser.parse_args()

    if not (SERVER_DIR / "src" / "main.py").is_file():
        print(f"[api-docs][FATAL] 缺少后端源码：{SERVER_DIR / 'src' / 'main.py'}", file=sys.stderr)
        return 2

    schema = _load_schema()
    payload = build_payload(schema)
    target = CONTENT_DIR / OUT_FILE
    text = _dump(payload)

    if args.check:
        if not target.is_file():
            print(f"[api-docs][FAIL] 缺少 {target.relative_to(REPO_ROOT)}（先跑一次生成）", file=sys.stderr)
            return 1
        existing = target.read_text(encoding="utf-8")
        if existing != text:
            print(
                f"[api-docs][FAIL] {target.relative_to(REPO_ROOT)} 与当前 OpenAPI 不一致"
                "（接口有增删改，官网未跟上）",
                file=sys.stderr,
            )
            return 1
        print("[api-docs] 接口文档数据与当前 OpenAPI 一致")
        return 0

    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"[api-docs] 已写入 {target.relative_to(REPO_ROOT)}")
    print(
        f"[api-docs] OpenAPI {payload['openapi']} · "
        f"{payload['total']} 个操作 · {payload['groupCount']} 个分组 · "
        f"version={payload['info']['version']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
