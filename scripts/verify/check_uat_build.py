#!/usr/bin/env python3
"""
UAT 构建物代码一致性核验（Phase 6.5，强制）

背景：UAT 用 `workflow_dispatch` 触发构建；如果 dispatch 前忘记推最新 commit，
或看错 run，很容易拿到**旧代码**的构建物。本脚本把「构建物是不是指定 commit 产出的」
变成可执行判据：

  1. 按仓库 + commit SHA 找到 `build-*` 的 workflow_dispatch run；
  2. 要求 run 已 `completed` 且 `conclusion == success`；
  3. 打印 run 链接、artifact 名称/大小，便于人工下载与回填记录。

用法：
    python scripts/verify/check_uat_build.py --sha <commit>
    python scripts/verify/check_uat_build.py --sha <commit> --workflow build-desktop
    GITHUB_TOKEN=... python ...   # 可选，避免匿名 API 限额

判据：退出码 0 才允许把这批构建物写进人工体验记录；否则废弃并重新 dispatch。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

API = "https://api.github.com"

#: 默认核验的四条构建流水线（与 Phase 6.5 的构建物清单一致）
DEFAULT_WORKFLOWS = ("build-server", "build-web", "build-desktop", "build-app")


def api_get(path: str, token: str | None) -> dict:
    """请求 GitHub API（公开仓库可匿名；有 token 时提高限额）。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jflove-uat-build-check",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    parser = argparse.ArgumentParser(description="核验 UAT 构建物是否来自指定 commit")
    parser.add_argument("--repo", default="js1688/jflove", help="owner/repo")
    parser.add_argument("--sha", required=True, help="期望的 commit SHA（完整或前 7 位）")
    parser.add_argument(
        "--workflow",
        action="append",
        default=None,
        help="只核验指定 workflow（可重复）；默认四条 build-*",
    )
    parser.add_argument("--branch", default="main", help="分支（默认 main）")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    workflows = args.workflow or list(DEFAULT_WORKFLOWS)
    expected = args.sha.lower()
    ok = True

    for wf in workflows:
        try:
            data = api_get(
                f"/repos/{args.repo}/actions/workflows/{wf}.yml/runs"
                f"?branch={args.branch}&per_page=30",
                token,
            )
        except Exception as exc:  # noqa: BLE001 - 网络/限流都按失败处理
            print(f"[FAIL] {wf}: 查询失败：{type(exc).__name__}")
            ok = False
            continue

        runs = [
            run
            for run in data.get("workflow_runs", [])
            if str(run.get("head_sha", "")).lower().startswith(expected)
            and run.get("event") == "workflow_dispatch"
        ]
        if not runs:
            print(f"[FAIL] {wf}: 没有 head_sha={args.sha} 的 workflow_dispatch run（疑似旧代码）")
            ok = False
            continue

        run = runs[0]
        status = f"{run.get('status')}/{run.get('conclusion')}"
        print(f"[{'OK' if run.get('conclusion') == 'success' else 'FAIL'}] "
              f"{wf}: {status} head_sha={str(run.get('head_sha'))[:7]} {run.get('html_url')}")
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            ok = False

        try:
            artifacts = api_get(
                f"/repos/{args.repo}/actions/runs/{run['id']}/artifacts", token
            ).get("artifacts", [])
        except Exception:  # noqa: BLE001 - artifact 查询失败不影响 SHA 结论
            artifacts = []
        if artifacts:
            for art in artifacts:
                print(f"       artifact: {art.get('name')} ({art.get('size_in_bytes')} B)")
        else:
            print("       artifact: 无（服务端/Web 为 GHCR 镜像）")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
