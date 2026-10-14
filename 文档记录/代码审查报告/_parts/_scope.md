# v1.6.0 代码审查 · 共享侦查备忘（Phase 5）

> 本文件是**审查工作面**的共享上下文，不是报告本体。报告本体：`文档记录/代码审查报告/v1.6.0.md`。
> **纪律：只输出意见，绝不修改任何源代码**。各审查面只能写自己那份 `_parts/<面>.md`。

## 0. 版本与范围

- 仓库根：`D:\github\jflove`；被审版本：**v1.6.0**；基线提交 `049c20d`（v1.5.1 收尾），
  被审端点 = `HEAD`（`c515853`）。
- 全量差异：`git diff 049c20d..HEAD` —— 375 文件 / +39043 / -333。
- 按区域（`git diff --shortstat 049c20d..HEAD -- <路径>` 可复现）：
  | 区域 | 文件 | 行 |
  | --- | --- | --- |
  | `jflove-server/src` | 2 | +165 -34 |
  | `jflove-server/tests` | 3 | +589 -1 |
  | `jflove-desktop/src` | 12 | +4192 -13 |
  | `jflove-desktop/tests` | 9 | +4189 |
  | `jflove-desktop/ui/src` | 35 | +8904 -15 |
  | `jflove-desktop/ui/public` | 1 | +40 |
  | `jflove-desktop/scripts` | 5 | +2351 |
  | `jflove-desktop/packaging` | 6 | +219 -2 |
  | `jflove-desktop/poc` | 233 | +13368 |
  | `jflove-web` | 14 | +755 -43 |
  | `.claude` | 7 | +191 -54 |
  | `jflove-app` | 4 | +9 -9 |
  | 根 | 3 | `AGENTS.md` / `README.md` / `build.py` |

## 1. 必读依据（按需定向读，不要整份读大文档）

| 依据 | 路径 | 用途 |
| --- | --- | --- |
| 需求文档 | `文档记录/需求文档/v1.6.0.md` | **§9 验收标准 AC-1~AC-62 / AC-S1~S4 / AC-B1~B9 / AC-R1~R6**（read 工具 offset 595 起，共 788 行）；§7.1 用户裁决 D1~D23；§10 本次不做；§11 待确认 |
| 设计文档 | `文档记录/设计文档/v1.6.0.md` | §10 有 **AC→设计映射（81 条全覆盖，供 Phase 5/6 逐条核查）**（行 1194-1301）；§0.2 设计约束；§13.2 未决项；§14.3 Phase 6 必须补的负向对照 |
| 审查角色规则 | `.claude/skills/code-review/SKILL.md` | **8 条审查纪律**（见下） |
| 经验库 | `.claude/skills/LESSONS.md` | 静默失败 / 编码陷阱 / 无头环境边界 |
| 项目总纲 | `AGENTS.md` | §4 编码规范、§7.6 上版缺陷跟踪、**§9 安全宪法**（9.6 表格 code-review 行） |
| 开发记录 | `文档记录/桌面端开发记录/v1.6.0.md`、`文档记录/后端开发记录/v1.6.0.md`、`文档记录/Web端开发记录/v1.6.0.md` | 实现方自述的判据与留痕（**当待验证输入，不是事实**） |
| 台账 | `文档记录/项目管理记录/BACKLOG.md` | BL-1~BL-17 的状态与证据 |

## 2. 审查纪律（`.claude/skills/code-review/SKILL.md`，摘要）

1. **结论必须有证据**：每条给 `文件:行` + 可复现命令/真实输出；不确定写「待确认」并说明原因；**不要编造**。
2. **设计文档里的"实测结论"要当待验证输入**，优先自己跑；发现方向反了就"先建议改文档，再谈代码"。
3. **区分"现状行为"与"期望行为"**：测试断言可疑行为时先判断它锁的是期望还是 bug。
4. **静默失败优先于显式报错**（"不报错但结果错"最该报）。
5. 用户已接受的风险不重复报缺陷，但要注明「已知并接受」并确认兜底存在。
6. **越界改动单独标注**。
7. **门禁/校验脚本要审"判据"**：比的是什么 / 方向对不对 / 跳过了什么 / 有没有反向验证。
8. **"以 X 为准"里要审 X 自己可不可信**。

## 3. 环境（真实可跑的命令）

```powershell
# 桌面端 / 服务端 Python（务必用带依赖的解释器）
jflove-desktop\venv-win\Scripts\python.exe -m pytest tests\ -q
jflove-desktop\venv-win\Scripts\python.exe -m flake8 src\ tests\ --max-line-length=99
jflove-server\venv-win\Scripts\python.exe -m pytest tests\ -q
jflove-server\venv-win\Scripts\python.exe -m flake8 src\ tests\ --max-line-length=99
# 桌面端 UI（workdir = jflove-desktop\ui）
npm run lint ; npx tsc --noEmit
# 跨端/版本门禁（仓库根）
python scripts\check_web_desktop_parity.py ; python scripts\check_web_desktop_parity.py --self-test
python scripts\sync_version.py --check
jflove-server\venv-win\Scripts\python.exe scripts\check_db_schema.py
```
> 已知环境注记：`pip` 走清华镜像会故障（需 `-i https://pypi.org/simple`）；本机 Python 只有 3.13.14
> （文档要求 3.14+，D14 裁决「只记录差异、不升级环境」）；无头环境**无法**验证真实 GUI 交互（键盘命中、
> 原生对话框、托盘、进程计数），这类项一律标 **无法验证（无头）** 并说明需要什么条件才能验证。

## 4. 报告片段格式（各面写进自己的 `_parts/<面>.md`）

```markdown
### [严重|中等|轻微] <一句话结论>
- 位置：`<相对路径>:<行号>`
- 违反：<AGENTS.md §x / 需求 v1.6.0 §y / 设计 v1.6.0 §z / AC-编号>
- 证据：<命令 + 真实输出片段>
- 建议：<最小修复方向>
```

面结尾必须附 **AC 判定表**（本面负责的每条 AC：`通过` / `未通过` / `无法验证（无头）` + 一句判据）。

## 5. 已完成、不要重复取证的门禁（主对话已实测，HEAD = `c515853`）

```text
桌面端 flake8 → exit 0 ；pytest → 415 passed（基线 133，+282）
ui: npm run lint → 0 警告 ；npx tsc --noEmit → exit 0 ；npm run build → exit 0（1733 modules，dist JS 726.19 kB）
parity → exit 0 ；parity --self-test → exit 0 ；sync_version --check → exit 0 ；check_db_schema → exit 0
git status --porcelain → 0 条
```
> 这些是**版本级门禁**的通过记录；各面仍可复跑与自己结论直接相关的命令，
> 但**不要**把"门禁绿"当成"本面无缺陷"的理由。
