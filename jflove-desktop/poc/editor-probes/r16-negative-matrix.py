#!/usr/bin/env python3
"""
r16 负向对照变异矩阵（v1.6.1 Phase 6 · 设计 §14.3 的 9 条）

做一件事：**故意把源码改坏，然后要求对应判据必须失败**。
判据在干净源码上通过（基线）而变异后失败 ⇒ 判据真的在管这件事；
变异后仍然通过 ⇒ 该判据是「空转」的，必须补牙。

设计要点（逐字沿用 v1.6.0 的 `r9-negative-matrix.py` 纪律）
- 每个用例只改一处（字符串替换 / 追加 / 新建），**改完立刻按字节还原**，
  并以 sha256 复验；脚本结束再比对一次 ``git status --porcelain`` 作为兜底证据。
- 变异锚点必须**恰好命中 1 次**，否则视为脚本自身错误（exit 2）——
  拒绝「变异其实没生效」造成的假绿。
- 退出码：0 = 全部被抓且全部还原；1 = 有用例没被抓；2 = 脚本/环境错误
  （锚点未命中、还原失败、判据超时）。
- ``--baseline`` 会在变异前先跑一次判据、要求它**通过**（默认关闭，避免重复耗时）。

设计 §14.3 的 9 行 → 本脚本 10 条用例
（AC-66 / AC-68 在同一行里，拆成两条才能真正各证各的判据）：

| # | 设计 §14.3 的 AC | 负向注入 | 本脚本用例 id |
|---|-----------------|---------|--------------|
| 1 | AC-63 | 把导航项改成「始终渲染」 | ``AC63-NAV-ALWAYS`` |
| 2 | AC-64 | 丢弃挂起的唤起请求 | ``AC64-DROP-QUEUE`` |
| 3 | AC-65 | 同路径走「新开标签」分支 | ``AC65-NO-DEDUP`` |
| 4 | AC-66 | 切换时重建面板 state | ``AC66-UNMOUNT-PANEL`` |
| 5 | AC-68 | 切换（路由）时重建标签顺序 state | ``AC68-RESET-ON-UNMOUNT`` |
| 6 | AC-67 | 去掉溢出入口 | ``AC67-NO-OVERFLOW`` |
| 7 | AC-70 | 不接管 ``Ctrl+W``（交给宿主） | ``AC70-NO-CAPTURE`` |
| 8 | AC-71 | 在保存前插入会话校验 | ``AC71-SESSION-BEFORE-SAVE`` |
| 9 | AC-72 | 登出时清草稿 | ``AC72-DISCARD-ON-LOGOUT`` |
| 10 | AC-47（反转） | 恢复 v1.6.0 行为（未登录也能开编辑器） | ``AC47-UNGATED`` |
| 11 | **（表外）缺陷 R1** | 登录后落点判据恒为假 | ``R1-LANDING-OVERRIDE`` |
| 12 | **（本轮新增）F5 / AC-77①** | 卸载前的快照写回变成 no-op | ``F5-SNAPSHOT-NOOP`` |
| 13 | **（本轮新增）F6** | 关掉外壳侧剪贴板归一化 | ``F6-CLIPBOARD-NORMALIZE-OFF`` |

> 设计 §14.3 的表**共 9 行**，其中第 4 行同时点名 AC-66 与 AC-68 ⇒ 拆成 2 条（第 4、5 条），
> 合计 10 条；第 11 条 ``R1-LANDING-OVERRIDE`` 是本轮 Phase 6 新发现缺陷 R1 的补牙用例，
> **不属于设计 §14.3**，单独统计。
>
> 第 12/13 条是 **v1.6.1 本轮 7 条用户反馈修复**（F5 正文保活 / F6 外壳侧剪贴板归一化）的
> 负向对照，同样**不属于设计 §14.3**。两条都只做**单测层**对照，原因（**机制限制，已实测**）：
>
> - **F5 的真机判据（``r15`` 的 AC68-2）跑的是 ``ui/dist`` 里**构建好的**bundle**，
>   而本脚本的变异只改 ``ui/src/**`` —— 不重新 ``vite build`` 的话，探针测到的是**陈旧产物**，
>   会把"变异其实没生效"记成假绿（"未抓"）。本脚本的机制是"改源码 → 直接跑判据"，
>   不含构建步骤 ⇒ **真机那一半不在本脚本**，由 ``out/r15-*-f5neg/`` 的
>   手动变异轮（改源码 → ``vite build`` → ``r15 --only 68 --webui <新产物>`` ⇒ AC68-2 必须 FAIL）
>   与 ``out/r15-*/`` 的还原后绿轮两两对照来证。本条锁的是**同一行为的单测半边**
>   （AC-77 主判据 ``scratch-editor-content-survival.test.tsx``，与真机判据同源同语义）。
> - **F6 的真机判据（``r17 --only CLIP-AWT-REAL``）**需要剪贴板上**已由 JDK 写好真实 AWT 载荷**
>   且 JVM 驻留（``AwtClipHtml.java --hold=900``）—— 那是本脚本**之外**的前置（要在用例的
>   变异窗口内起/停一个 JVM），且"前置未满足"时 r17 也会 FAIL ⇒ 与本脚本
>   "只看退出码"的判据**无法区分**"因变异而红"和"因前置缺失而红"（假绿风险）。
>   故本条只锁**单测半边**（``tests/test_v161_f6_clipboard_normalize.py``，含"接线真的装上了"
>   的 BL-51 回归守门）；真机那一半见回执里手动变异轮的 ``r17`` 红/绿对照。
>
> 两条新用例都带 ``expect``：**光有非零退出码不算"被抓"**，还必须命中指定的失败指纹
> （``FAILED <具体用例>``），避免"判据因为别的原因红"被当成牙齿。

**AC-70 的真机一半不在本脚本**：设计 §13.3 第 11 条要求「真机探针必须失败」，
该证据由 ``r13-ctrlw-takeover.py --negative-ac70`` 的负向轮提供
（``out/r13-20261015-100921-neg/``，H5 = 负向对照按预期失败）。本脚本只证**渲染层**
那一半（不 ``preventDefault`` ⇒ 单测必红）。AC-71 的真机一半同样由 ``r15`` 的 AC71-* 提供。

用法：
    python jflove-desktop/poc/editor-probes/r16-negative-matrix.py
    python jflove-desktop/poc/editor-probes/r16-negative-matrix.py --baseline
    python jflove-desktop/poc/editor-probes/r16-negative-matrix.py --only AC64-DROP-QUEUE
    python jflove-desktop/poc/editor-probes/r16-negative-matrix.py --list
    ... --out poc\\editor-probes\\out\\r16-<证据目录>   # 另写 r16-matrix.json 汇总（可选）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

NODE = shutil.which("node") or "node"
DESKTOP_UI = "jflove-desktop/ui"
DESKTOP_VITEST = "node_modules/vitest/vitest.mjs"
#: 桌面端 Python（venv）解释器：跑外壳侧（Python）判据用
DESKTOP_PYTHON = str(DESKTOP / "venv-win" / "Scripts" / "python.exe")

UI = "jflove-desktop/ui/src"
WEB_SHELL = "jflove-desktop/src/ui/web_shell.py"
LAYOUT = f"{UI}/layouts/DesktopLayout.tsx"
OPEN_QUEUE = f"{UI}/utils/scratch/open-paths-queue.ts"
SCRATCH_STORE = f"{UI}/stores/scratch-store.ts"
WORKSPACE = f"{UI}/components/scratch/ScratchWorkspace.tsx"
TAB_BAR = f"{UI}/components/scratch/ScratchTabBar.tsx"
SHORTCUT_RUNTIME = f"{UI}/utils/scratch/shortcut-runtime.ts"
EDITOR_GATE = f"{UI}/utils/scratch/editor-gate.ts"
OPEN_PATHS = f"{UI}/utils/scratch/editor-open-paths.ts"
#: 「登录后落点」判据的叶子模块（Phase 6 收尾轮消环 R2 时从 `editor-open-paths.ts` 搬到这里）
EDITOR_LANDING = f"{UI}/utils/scratch/editor-landing.ts"


def _vitest(*targets: str) -> tuple[str, ...]:
    """桌面端 UI 判据（直接走 node + vitest 入口，绕开 npm 批处理包装）"""
    return (NODE, DESKTOP_VITEST, "run", *targets)


def _pytest(*targets: str) -> tuple[str, ...]:
    """桌面端 Python（外壳侧）判据：走模块内 venv 的 pytest（不经 shell 包装）"""
    return (DESKTOP_PYTHON, "-m", "pytest", "-q", *targets)


@dataclass(frozen=True)
class Case:
    """一条变异用例：把 ``rel`` 改坏，要求 ``argv`` 判据**失败**。

    ``expect``：可选的"失败指纹" —— 判据必须**同时**命中这些子串才算"被抓"，
    以免"非零退出码来自别的原因"（环境错、别的用例红）被当成判据有牙齿。
    """

    id: str
    row: str
    ac: str
    why: str
    op: str  # patch | append | create
    rel: str
    cwd: str
    argv: tuple[str, ...]
    old: str = ""
    new: str = ""
    content: str = ""
    timeout: int = 420
    tags: tuple[str, ...] = field(default_factory=tuple)
    expect: tuple[str, ...] = field(default_factory=tuple)


CASES: tuple[Case, ...] = (
    Case(
        id="AC63-NAV-ALWAYS",
        row="设计 §14.3 第 1 行 AC-63",
        ac="AC-63①",
        why="导航项改成「始终渲染」⇒ 未登录零存在用例必须失败",
        op="patch",
        rel=LAYOUT,
        cwd=DESKTOP_UI,
        old=(
            "  const navItems = NAV_ITEMS.filter((item) => !item.requiresEditor"
            " || canUseEditor);\n"
        ),
        new="  // MUTATION：AC-63 负向注入 —— 导航项不再过滤，未登录也能看见编辑器入口\n"
        "  const navItems = NAV_ITEMS;\n",
        argv=_vitest("src/layouts/__tests__/desktop-layout-gate.test.tsx"),
    ),
    Case(
        id="AC64-DROP-QUEUE",
        row="设计 §14.3 第 2 行 AC-64",
        ac="AC-64③",
        why="未登录时丢弃挂起的唤起请求（清队而非挂起）⇒「登录后自动打开」用例必须失败",
        op="patch",
        rel=OPEN_QUEUE,
        cwd=DESKTOP_UI,
        old="  if (!gate()) return [];\n",
        new="  // MUTATION：AC-64 负向注入 —— 未登录直接丢弃请求，而不是排队挂起\n"
        "  if (!gate()) {\n"
        "    pending = [];\n"
        "    queued.clear();\n"
        "    return [];\n"
        "  }\n",
        argv=_vitest("src/utils/scratch/__tests__/editor-open-paths-gate.test.ts"),
    ),
    Case(
        id="AC65-NO-DEDUP",
        row="设计 §14.3 第 3 行 AC-65",
        ac="AC-65",
        why="同路径不再查重、直接走「新开标签」分支 ⇒ 去重聚焦用例必须失败",
        op="patch",
        rel=SCRATCH_STORE,
        cwd=DESKTOP_UI,
        old=(
            "      const existing = get().tabs.find((tab) => tab.path !== null"
            " && tab.path === item.path);\n"
            "      if (existing) {\n"
            "        get().setActiveTab(existing.id);\n"
            "        return existing.id;\n"
            "      }\n"
        ),
        new=(
            "      // MUTATION：AC-65 负向注入 —— 不查同路径，一律新建标签\n"
            "      const existing = undefined;\n"
            "      if (existing) {\n"
            "        get().setActiveTab(existing.id);\n"
            "        return existing.id;\n"
            "      }\n"
        ),
        argv=_vitest("src/stores/__tests__/scratch-store-save.test.ts"),
    ),
    Case(
        id="AC66-UNMOUNT-PANEL",
        row="设计 §14.3 第 4 行 AC-66",
        ac="AC-66",
        why="非激活面板被卸载（切走即丢 state）⇒ 状态保持用例必须失败",
        op="patch",
        rel=WORKSPACE,
        cwd=DESKTOP_UI,
        old="  const mountedTabs = tabs.filter((tab) => tab.mounted || tab.id === activeTabId);\n",
        new="  // MUTATION：AC-66 负向注入 —— 只渲染激活项，切换即重建面板 state\n"
        "  const mountedTabs = tabs.filter((tab) => tab.id === activeTabId);\n",
        argv=_vitest("src/components/scratch/__tests__/scratch-tab-switch-state.test.tsx"),
    ),
    Case(
        id="AC68-RESET-ON-UNMOUNT",
        row="设计 §14.3 第 4 行 AC-66 / AC-68（同行拆出的 AC-68 一侧）",
        ac="AC-68②",
        why="切路由（卸载工作区）时把内存标签状态重建 ⇒ 顺序 / 激活项保持用例必须失败",
        op="patch",
        rel=WORKSPACE,
        cwd=DESKTOP_UI,
        old=(
            "  // ① 准入兜底：未登录不渲染工作区（路由守卫已挡一层，"
            "这里防\"停留期间会话失效\"）\n"
            "  if (!gate) return null;\n"
        ),
        new=(
            "  // MUTATION：AC-68② 负向注入 —— 卸载工作区时把标签顺序 state 一并重建\n"
            "  useEffect(\n"
            "    () => () => {\n"
            "      useScratchStore.setState({ tabs: [], activeTabId: null });\n"
            "    },\n"
            "    [],\n"
            "  );\n"
            "\n"
            "  // ① 准入兜底：未登录不渲染工作区（路由守卫已挡一层，"
            "这里防\"停留期间会话失效\"）\n"
            "  if (!gate) return null;\n"
        ),
        argv=_vitest("src/components/scratch/__tests__/scratch-tab-reorder.test.tsx"),
    ),
    Case(
        id="AC67-NO-OVERFLOW",
        row="设计 §14.3 第 5 行 AC-67",
        ac="AC-67",
        why="去掉溢出入口（按钮 + 下拉整体不渲染）⇒「能定位第 12 个」用例必须失败",
        op="patch",
        rel=TAB_BAR,
        cwd=DESKTOP_UI,
        old=(
            "      {/* 溢出入口：常驻右侧；点开 = 全量标签下拉（放不下时仍能切换） */}\n"
            "      <div className=\"scratch-tab-overflow\" ref={overflowRef}>\n"
            "        <button\n"
            "          type=\"button\"\n"
            "          className=\"scratch-tab-overflow-button\"\n"
            "          data-scratch-tab-overflow=\"true\"\n"
            "          aria-label=\"全部标签页\"\n"
            "          aria-haspopup=\"menu\"\n"
            "          aria-expanded={overflowOpen}\n"
            "          title=\"全部标签页\"\n"
            "          onClick={() => setOverflowOpen((open) => !open)}\n"
            "        >\n"
            "          <Icon name=\"more\" size=\"sm\" />\n"
            "        </button>\n"
            "        {overflowOpen && <ScratchTabOverflowMenu"
            " onClose={() => setOverflowOpen(false)} />}\n"
            "      </div>\n"
        ),
        new=(
            "      {/* MUTATION：AC-67 负向注入 —— 去掉溢出入口 */}\n"
            "      <div className=\"scratch-tab-overflow\" ref={overflowRef} />\n"
        ),
        argv=_vitest("src/components/scratch/__tests__/scratch-tab-bar.test.tsx"),
    ),
    Case(
        id="AC70-NO-CAPTURE",
        row="设计 §14.3 第 6 行 AC-70（渲染层一半）",
        ac="AC-70①②",
        why="命中后不 preventDefault / 不 stopPropagation ⇒ Ctrl+W 接管用例必须失败",
        op="patch",
        rel=SHORTCUT_RUNTIME,
        cwd=DESKTOP_UI,
        old=(
            "  event.preventDefault();\n"
            "  event.stopPropagation();\n"
            "  runShortcut(matched, panelTabId);\n"
        ),
        new=(
            "  // MUTATION：AC-70 负向注入 —— 不接管，把 Ctrl+W 交给宿主默认动作\n"
            "  runShortcut(matched, panelTabId);\n"
        ),
        argv=_vitest("src/components/scratch/__tests__/shortcut-runtime-intercept.test.tsx"),
        tags=("真机一半见 r13 --negative-ac70",),
    ),
    Case(
        id="AC71-SESSION-BEFORE-SAVE",
        row="设计 §14.3 第 7 行 AC-71",
        ac="AC-71①③",
        why="保存前逐次校验会话（服务端不可达即拒写）⇒ 保存通路用例必须失败",
        op="patch",
        rel=SCRATCH_STORE,
        cwd=DESKTOP_UI,
        old=(
            "      // ② 手动保存接管本轮：取消排队中的自动保存，"
            "避免同一路径写两次（AC-37 的\"恰好一次\"）\n"
            "      clearFlushTimer(id);\n"
        ),
        new=(
            "      // MUTATION：AC-71 负向注入 —— 保存前插入会话校验"
            "（登录后仍依赖服务端，离线即拒写）\n"
            "      try {\n"
            "        await (await import('../services/auth-service')).authService.adminExists();\n"
            "      } catch {\n"
            "        set({ lastError: '会话校验失败，无法保存' });\n"
            "        return rescheduleFlush();\n"
            "      }\n"
            "      // ② 手动保存接管本轮：取消排队中的自动保存，"
            "避免同一路径写两次（AC-37 的\"恰好一次\"）\n"
            "      clearFlushTimer(id);\n"
        ),
        argv=_vitest("src/stores/__tests__/scratch-store-save.test.ts"),
        tags=("真机一半见 r15 的 AC71-1..6（服务端进程真停）",),
    ),
    Case(
        id="AC72-DISCARD-ON-LOGOUT",
        row="设计 §14.3 第 8 行 AC-72",
        ac="AC-72③",
        why="登出（门翻假）时清掉本机草稿 ⇒「不丢内容」用例必须失败",
        op="patch",
        rel=WORKSPACE,
        cwd=DESKTOP_UI,
        old=(
            "      flushOpenPaths();\n"
            "    })();\n"
            "  }, [gate]);\n"
        ),
        new=(
            "      flushOpenPaths();\n"
            "    })();\n"
            "  }, [gate]);\n"
            "\n"
            "  // MUTATION：AC-72 负向注入 —— 登出即清理本机草稿（AC-72③ 的反面）\n"
            "  useEffect(() => {\n"
            "    if (gate) return;\n"
            "    useScratchStore.getState().tabs.forEach((tab) => {\n"
            "      void scratchService.draftDiscard(tab.id);\n"
            "    });\n"
            "  }, [gate]);\n"
        ),
        argv=_vitest("src/components/scratch/__tests__/scratch-workspace-gate.test.tsx"),
    ),
    Case(
        id="AC47-UNGATED",
        row="设计 §14.3 第 9 行 AC-47（反转）",
        ac="AC-47①",
        why="恢复 v1.6.0 行为（准入恒为真，未登录也能开编辑器）⇒ 准入用例必须失败",
        op="patch",
        rel=EDITOR_GATE,
        cwd=DESKTOP_UI,
        old="  return snapshot.isLoggedIn && !snapshot.tokenExpired;\n",
        new=(
            "  // MUTATION：AC-47 负向注入 —— 恢复 v1.6.0 的「未登录也能开编辑器」\n"
            "  void snapshot;\n"
            "  return true;\n"
        ),
        argv=_vitest("src/utils/scratch/__tests__/editor-gate.test.ts"),
    ),
    # ── 设计 §14.3 之外的**第 11 条**：Phase 6 本轮新发现的缺陷 R1 的负向对照 ──
    # 缺陷 R1：登录成功后 `LoginPage` 的 `navigate('/')` 覆盖了接线里的 `navigate('/editor')`
    # ⇒ 编辑器没被揭示（AC-64 ② 失败）。回修引入了「登录后落点」的单一判据
    # `editorRevealPendingOnLogin()`；本条把该判据钉成假的，要求新回归用例必须红。
    Case(
        id="R1-LANDING-OVERRIDE",
        row="Phase 6 缺陷 R1（AC-64 ② 的落点判据）",
        ac="AC-64②",
        why="落点判据恒为假 ⇒ 登录后回到首页而不是揭示编辑器，新回归用例必须失败",
        op="patch",
        # 判据本体在 Phase 6 收尾轮消环（R2）时从 `editor-open-paths.ts` 搬到叶子 `editor-landing.ts`
        rel=EDITOR_LANDING,
        cwd=DESKTOP_UI,
        old=(
            "export function editorRevealPendingOnLogin(): boolean {\n"
            "  return revealOnThisLogin;\n"
            "}\n"
        ),
        new=(
            "export function editorRevealPendingOnLogin(): boolean {\n"
            "  // MUTATION：R1 负向注入 —— 落点判据恒为假（登录后回首页，再次覆盖接线导航）\n"
            "  void revealOnThisLogin;\n"
            "  return false;\n"
            "}\n"
        ),
        argv=_vitest("src/pages/__tests__/login-page-landing.test.tsx"),
    ),
    # ── 本轮（v1.6.1 七条用户反馈修复）新增的两条负向对照 ──
    # F5：切菜单往返正文丢光 —— 修复点是"宿主卸载 / DOM 被换时把活正文快照回 store"
    # （`ScratchEditorHost.tsx:596/619` 两处调用 ⇒ `scratch-store.ts::snapshotInitialText`）。
    # 本条把**写侧唯一的落点**变成 no-op（正文不再进 `initialTexts`）⇒ 重挂只能以空 doc 建立，
    # AC-77 的主判据（真 CM6 实例 + 真 Workspace 挂载/卸载）必须失败。
    Case(
        id="F5-SNAPSHOT-NOOP",
        row="本轮修复 F5（AC-77① 渲染层写侧）",
        ac="AC-77①",
        why="把 `snapshotInitialText` 的正文写回改成 no-op ⇒ 「编辑 → 卸载工作区 → 重挂」正文必须丢",
        op="patch",
        rel=SCRATCH_STORE,
        cwd=DESKTOP_UI,
        old=(
            "  const tab = useScratchStore.getState().tabs.find((item) => item.id === tabId);\n"
            "  if (!tab) return; // ① 守卫：标签已关闭 / 从不存在 ⇒ 不写入（防\"死标签复活\"）\n"
            "  initialTexts.set(tabId, text);\n"
        ),
        new=(
            "  // MUTATION：F5 负向注入 —— 卸载前的快照写回变成 no-op（等价于修复前\"什么都不存\"）\n"
            "  void tabId;\n"
            "  void text;\n"
            "  void meta;\n"
        ),
        argv=_vitest("src/components/scratch/__tests__/scratch-editor-content-survival.test.tsx"),
        expect=("scratch-editor-content-survival.test.tsx", "failed"),
        tags=("真机一半见 out/r15-*-f5neg（改源码 → vite build → r15 --only 68 必须 FAIL）",),
    ),
    # F6：外壳侧剪贴板归一化（真凶 = Win32 `CFSTR_INETURL` ⇒ Qt 侧 `text/uri-list`）。
    # 本条把**平台开关**关掉（等价于修复前"根本不做归一化"）⇒ 单测（含 BL-51「接线真的装上了」
    # 的回归守门）必须失败。真机一半（r17 --only CLIP-AWT-REAL）的前置见模块文档的说明。
    Case(
        id="F6-CLIPBOARD-NORMALIZE-OFF",
        row="本轮修复 F6（外壳侧归一化开关）",
        ac="F6",
        why="把 `CLIPBOARD_NORMALIZE_ENABLED` 关掉 ⇒ 单测（归一化行为 + 接线守门）必须失败",
        op="patch",
        rel=WEB_SHELL,
        cwd="jflove-desktop",
        old='CLIPBOARD_NORMALIZE_ENABLED = sys.platform == "win32"\n',
        new=(
            "# MUTATION：F6 负向注入 —— 关掉平台开关（等价于修复前\"不做归一化\"）\n"
            "CLIPBOARD_NORMALIZE_ENABLED = False\n"
        ),
        argv=_pytest("tests/test_v161_f6_clipboard_normalize.py"),
        expect=("tests/test_v161_f6_clipboard_normalize.py", "failed"),
        tags=("真机一半 = r17 --only CLIP-AWT-REAL（需 JDK 写好真实 AWT 载荷 + JVM 驻留）",),
    ),
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_status(paths: tuple[str, ...] = ()) -> str:
    """只统计**变异目标文件**的状态：并行协作者的其它新增文件不应算作污染。"""
    argv = ["git", "status", "--porcelain"]
    if paths:
        argv += ["--", *paths]
    proc = subprocess.run(
        argv,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
    )
    return proc.stdout.strip()


def _run(argv: tuple[str, ...], cwd: Path, timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_env(),
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(out, bytes):  # pragma: no cover - 平台差异兜底
            out = out.decode("utf-8", "replace")
        return -1, out, time.monotonic() - started
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), time.monotonic() - started


def _tail(text: str, lines: int = 14) -> str:
    rows = [row for row in text.splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def _match_count(case: Case) -> int:
    """**只读**地算出变异锚点的命中次数（含 CRLF 归一化），不写盘。

    给 ``--dry-run`` 用：先把"锚点恰好命中 1 次"这条前置条件验掉，
    避免因为锚点失效（文件被后人改过）白跑一整轮 vitest。
    """
    if case.op == "create":
        return 0 if not (REPO / case.rel).exists() else 1
    text = (REPO / case.rel).read_text(encoding="utf-8")
    if case.op == "append":
        return 1
    old = case.old
    if text.count(old) != 1 and "\r\n" in text:
        old = old.replace("\n", "\r\n")
    return text.count(old)


def _apply(case: Case) -> tuple[Path, bytes | None]:
    """施加变异；返回 (目标路径, 原始字节)。``create`` 用例原始字节为 None。"""
    target = REPO / case.rel
    if case.op == "create":
        if target.exists():
            raise RuntimeError(f"变异文件已存在，拒绝覆盖：{target}")
        target.write_text(case.content, encoding="utf-8")
        return target, None

    original = target.read_bytes()
    text = original.decode("utf-8")
    if case.op == "append":
        text = text.rstrip("\n") + "\n" + case.content + "\n"
    elif case.op == "patch":
        old, new = case.old, case.new
        if text.count(old) != 1 and "\r\n" in text:
            # 目标文件是 CRLF：锚点按文件实际换行符重排
            old = old.replace("\n", "\r\n")
            new = new.replace("\n", "\r\n")
        hits = text.count(old)
        if hits != 1:
            raise RuntimeError(
                f"变异锚点命中 {hits} 次（要求恰好 1 次）：{old.splitlines()[0][:60]!r}"
            )
        text = text.replace(old, new, 1)
    else:  # pragma: no cover - 定义期错误
        raise RuntimeError(f"未知 op：{case.op}")
    target.write_bytes(text.encode("utf-8"))
    return target, original


def _restore(case: Case, target: Path, original: bytes | None) -> str:
    """还原并复验；返回可读的还原结论。"""
    if case.op == "create":
        if target.exists():
            target.unlink()
        return "已删除新建文件" if not target.exists() else "还原失败：新建文件仍在"

    assert original is not None
    target.write_bytes(original)
    now = _sha256(target.read_bytes())
    want = _sha256(original)
    return "sha 一致" if now == want else f"还原失败：sha 不一致（{now[:8]} != {want[:8]}）"


def main() -> int:
    parser = argparse.ArgumentParser(description="r16 负向对照变异矩阵（v1.6.1 / 设计 §14.3）")
    parser.add_argument("--only", nargs="*", default=None, help="只跑指定 id")
    parser.add_argument("--list", action="store_true", help="列出用例后退出")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读校验：逐个报告变异锚点的命中次数（要求 1），不写盘、不跑判据",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="变异前先跑一遍判据，要求它通过（更严谨，耗时约翻倍）",
    )
    parser.add_argument(
        "--out",
        default="",
        help="证据目录（可选）：汇总写入 <out>/r16-matrix.json；控制台日志由调用方留存",
    )
    args = parser.parse_args()

    cases = list(CASES)
    if args.only:
        wanted = {item.upper() for item in args.only}
        cases = [case for case in cases if case.id.upper() in wanted]
        if not cases:
            print(f"没有匹配的用例：{sorted(wanted)}")
            return 2

    if args.list:
        for case in cases:
            print(f"{case.id:26s} {case.row:32s} {case.rel}")
            print(f"{'':26s} 依据：{case.why}")
            print(f"{'':26s} 判据：{' '.join(case.argv[-1:])}（cwd={case.cwd}）")
        return 0

    if args.dry_run:
        bad = 0
        for case in cases:
            try:
                hits = _match_count(case)
            except Exception as exc:  # noqa: BLE001 - 只读校验也要把原因说清
                print(f"[错误] {case.id:26s} 读取失败：{exc}")
                bad += 1
                continue
            flag = "OK  " if hits == 1 else "错误"
            if hits != 1:
                bad += 1
            print(f"[{flag}] {case.id:26s} 锚点命中 {hits} 次 :: {case.rel}")
        print(f"\n锚点全部恰好命中 1 次：{'是' if bad == 0 else f'否（{bad} 条不合格）'}")
        return 0 if bad == 0 else 2

    vitest_bin = REPO / DESKTOP_UI / DESKTOP_VITEST
    if not vitest_bin.is_file():
        print(f"[环境错误] 桌面端 vitest 入口不存在：{vitest_bin}")
        return 2

    targets = tuple(dict.fromkeys(case.rel for case in cases))
    before = _git_status(targets)
    print("=" * 78)
    print("r16 负向对照变异矩阵（v1.6.1）：故意改坏源码 ⇒ 判据必须失败")
    print(f"仓库根：{REPO}")
    print(f"用例数：{len(cases)}（设计 §14.3 共 9 行 → 10 条；另含表外缺陷 R1 补牙 1 条"
          f" + 本轮 F5/F6 负向对照各 1 条）（baseline={'on' if args.baseline else 'off'}）")
    print("=" * 78)

    caught: list[str] = []
    missed: list[str] = []
    errors: list[str] = []
    records: list[dict] = []

    def note(case: Case, status: str, **extra: object) -> None:
        """把每一条用例的结论记进汇总（`--out` 时落盘为 `r16-matrix.json`）。"""
        records.append({
            "id": case.id,
            "ac": case.ac,
            "row": case.row,
            "rel": case.rel,
            "cwd": case.cwd,
            "why": case.why,
            "verdict": status,  # caught | missed | error
            "expect": list(case.expect),
            **extra,
        })

    for case in cases:
        label = f"{case.id}（{case.ac} · {case.row}）"
        print(f"\n── {label} :: {case.rel}")
        print(f"   依据：{case.why}")

        baseline_ok: str = "未跑"
        if args.baseline:
            code, out, cost = _run(case.argv, REPO / case.cwd, case.timeout)
            if code != 0:
                errors.append(case.id)
                note(case, "error", exit=code, costSec=round(cost, 1), error="baseline-failed",
                     tail=_tail(out))
                print(f"   [错误] 基线判据在干净源码上就没过（exit {code}, {cost:.1f}s）")
                print(_tail(out))
                continue
            baseline_ok = f"通过({cost:.1f}s)"

        target_path: Path | None = None
        original: bytes | None = None
        try:
            target_path, original = _apply(case)
        except Exception as exc:  # noqa: BLE001 - 脚本自身错误需要原样报出
            errors.append(case.id)
            note(case, "error", error=f"apply-failed: {exc}")
            print(f"   [错误] 施加变异失败：{exc}")
            continue

        try:
            code, out, cost = _run(case.argv, REPO / case.cwd, case.timeout)
        finally:
            restore_msg = _restore(case, target_path, original)
        if restore_msg != "sha 一致" and not restore_msg.startswith("已删除"):
            errors.append(case.id)
            note(case, "error", exit=code, error=restore_msg)
            print(f"   [错误] {restore_msg}")
            continue

        if code == -1:
            errors.append(case.id)
            note(case, "error", error="timeout", timeoutSec=case.timeout, tail=_tail(out))
            print(f"   [错误] 判据超时（>{case.timeout}s）")
            print(_tail(out))
            continue

        if code != 0:
            missing = [token for token in case.expect if token not in out]
            if missing:
                # 非零退出码来自**别的原因**（环境错 / 别的用例红）⇒ 不算"判据有牙齿"
                missed.append(case.id)
                note(case, "missed", exit=code, costSec=round(cost, 1), baseline=baseline_ok,
                     restore=restore_msg, error="expect-not-matched", missing=missing,
                     tail=_tail(out))
                print(f"   [未抓] 判据失败 exit={code} 用时 {cost:.1f}s，但**未命中失败指纹**"
                      f"{missing} ⇒ 红的原因不是本变异")
                print(_tail(out))
                continue
            caught.append(case.id)
            note(case, "caught", exit=code, costSec=round(cost, 1), baseline=baseline_ok,
                 restore=restore_msg, tail=_tail(out, 6))
            print(f"   [OK] 判据失败 exit={code} 用时 {cost:.1f}s｜基线:{baseline_ok}"
                  f"｜还原:{restore_msg}"
                  + (f"｜命中指纹:{list(case.expect)}" if case.expect else ""))
        else:
            missed.append(case.id)
            note(case, "missed", exit=code, costSec=round(cost, 1), baseline=baseline_ok,
                 restore=restore_msg, error="criterion-passed-unmutated", tail=_tail(out))
            print(f"   [未抓] 判据竟然通过（exit 0, {cost:.1f}s）⇒ 该判据在空转")
            print(_tail(out))

    after = _git_status(targets)
    print("\n" + "=" * 78)
    print(f"被抓：{len(caught)}/{len(cases)}；未抓：{len(missed)}；错误：{len(errors)}")
    if missed:
        print(f"未抓列表：{missed}")
    if errors:
        print(f"错误列表：{errors}")
    if before != after:
        print("[文件完整性] 变异目标文件的 git 状态与开局不一致！")
        print(f"  开局：{before.splitlines()[:6]}")
        print(f"  结束：{after.splitlines()[:6]}")
        errors.append("GIT-DIRTY")
    else:
        print(f"[文件完整性] {len(targets)} 个变异目标文件的 git 状态与开局逐行一致（变异全部还原）")
    print("=" * 78)

    verdict = 2 if errors else (1 if missed else 0)
    if args.out:
        # 相对路径按**调用方的工作目录**解析（不是仓库根）：本脚本的常规用法是在
        # `jflove-desktop/` 下跑并把证据写进 `poc/editor-probes/out/...`
        out_dir = Path(args.out)
        if not out_dir.is_absolute():
            out_dir = Path.cwd() / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "probe": "r16-negative-matrix",
            "at": datetime.now().isoformat(timespec="seconds"),
            "repo": str(REPO),
            "caseCount": len(cases),
            "baseline": bool(args.baseline),
            "caught": caught,
            "missed": missed,
            "errors": errors,
            "gitStatusBefore": before.splitlines(),
            "gitStatusAfter": after.splitlines(),
            "fileIntegrityOk": before == after,
            "exitCode": verdict,
            "cases": records,
        }
        path = out_dir / "r16-matrix.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"证据汇总：{path}")

    if errors:
        return 2
    return 1 if missed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台兜底
    except (AttributeError, OSError):  # pragma: no cover - 非 Windows / 老解释器
        pass
    raise SystemExit(main())
