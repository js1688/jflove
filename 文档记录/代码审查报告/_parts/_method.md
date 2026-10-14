# v1.6.0 代码审查报告（桌面端草稿编辑器 / 文本编辑器）

> 审查范围：`049c20d..HEAD`（v1.6.0 全部改动）
> 审查依据：`文档记录/需求文档/v1.6.0.md`（81 条 AC）、`文档记录/设计文档/v1.6.0.md`（1454 行）、`AGENTS.md`（含 §9 安全宪法）
> 审查角色：code-review（`.claude/skills/code-review/SKILL.md`）
> 说明：本阶段**不修改任何代码**，仅输出结论与建议。

## 1. 范围与方法

### 1.1 变更规模（实测）

命令：`git diff --stat 049c20d..HEAD`

| 区域 | 文件数 | 增/删 |
| --- | --- | --- |
| 合计 | **375** | **+39043 / −333** |

分区明细：

| 分区 | 文件数 | 增/删 | 备注 |
| --- | --- | --- | --- |
| `jflove-server/src` | 2 | +165 / −34 | BL-2 配置控制器 + BL-8/BL-10 上传服务 |
| `jflove-server/tests` | 3 | +589 | BL-8 / BL-10 用例 + schema 期望值 1 行 |
| `jflove-desktop/src` | 12 | +4192 / −13 | 服务层、单实例、桥、外壳 |
| `jflove-desktop/tests` | 9 | +4189 | 本版全部桌面端新用例 |
| `jflove-desktop/ui/src` | 35 | +8904 / −15 | 类型表、store、窗格层、工具箱、设置界面 |
| `jflove-desktop/ui/public` | 1 | +40 | `file-assoc.manifest.json`（生成物，入库） |
| `jflove-desktop/scripts` | 5 | +2351 | CM6 分包脚本 + 关联清单派生脚本 |
| `jflove-desktop/packaging` | 6 | +219 / −2 | `.iss` / `jflove.xml` / `.desktop` / `.spec` / `build_rpm.sh` |
| `jflove-desktop/poc` | 233 | +13368 | 前置实测脚本与证据（R1/R2/R3/R5/R6/R7/R9） |
| `jflove-web` | 14 | +755 / −43 | BL-3 信封解密 + 测试 |
| `.claude` | 7 | +191 / −54 | 技能文档（BL-1 注释/文档更正） |
| `jflove-app` | 4 | +9 / −9 | **越界改动，见 §9**（用户已裁决 D-A 保留） |
| 仓库根 | 3 | — | `AGENTS.md`、`README.md`、`build.py` |

### 1.2 分工（四面并行 + 主对话自查面）

| 面 | 负责 | 覆盖 |
| --- | --- | --- |
| A0 | 主对话 | 桥（`bridge/editor.py`、`bridge.py`）+ 外壳（`ui/web_shell.py`、`main.py`、`tray.py`）+ 导航/安全边界 |
| A1 | 子代理 | `jflove-server` + `jflove-web` + 门禁脚本判据 |
| A2 | 子代理 | 桌面端 Python 服务层（类型识别 / 编辑器 / 草稿库 / 文件关联 / 单实例） |
| A3 | 子代理 | 打包资产与派生链（manifest → `.iss` / `.xml` / `.desktop` / `.spec`）+ `build.py` 接线 |
| A4 | 子代理 | 桌面端 UI（窗格层 / 工具箱 / 四视图 / 快捷键 / 状态栏） |

### 1.3 方法

1. **对照文档逐条核 AC**：需求 §9（AC-1 ~ AC-62 及 AC-S/B/R 系列）+ 设计 §10「AC→设计映射」。
2. **结论必须有证据**：文件:行 + 可复现命令；不采信"看起来对"。
3. **设计文档的"实测结论"当待验证输入**：本版设计 §13/§14 含大量前置实测（R-1/R-5/R-6/R-9），凡影响实现的均**独立复核**，并对关键结论补做探针。
4. **区分现状行为与期望行为**：AC 未覆盖的现状行为只作体验登记，不记为违反。
5. **静默失败优先**：`except` 吞异常、返回值被丢弃、状态不落盘等一律优先排查。
6. **用户已接受的风险不重复上报**，但注明「已知并接受」并确认兜底方向安全。
7. **越界改动单独标注**（`AGENTS.md §1` 模块边界）。
8. **门禁/校验脚本审「判据四问」**：比的是什么？方向对不对？跳过了什么？有无反向验证？

### 1.4 可复现命令

```powershell
# 桌面端 / 服务端
jflove-desktop\venv-win\Scripts\python.exe -m flake8 src/ tests/ --max-line-length=99
jflove-desktop\venv-win\Scripts\python.exe -m pytest tests/ -q
jflove-server\venv-win\Scripts\python.exe -m flake8 src/ tests/ --max-line-length=99
jflove-server\venv-win\Scripts\python.exe -m pytest tests/ -q
# 前端
cd jflove-desktop\ui; npm run lint; npx tsc --noEmit; npm run build
# 版本级门禁
python scripts/check_parity.py            ; python scripts/check_parity.py --self-test
python scripts/sync_version.py --check    ; python scripts/check_db_schema.py
python jflove-desktop/scripts/gen_file_assoc_assets.py --check
```

### 1.5 环境注记

- Python 仅 **3.13.14**（无 3.14）；`pip` 走默认清华镜像会失败，须 `-i https://pypi.org/simple`。
- Qt / PySide6 **6.11.0**；GUI 行为在无头环境用 `QT_QPA_PLATFORM=offscreen` 验证，**真实按键与原生对话框无法验证**（见 §8 无法验证项）。
- 前置实测脚本位于 `jflove-desktop/poc/editor-probes/`，结论见同目录 `R1/R5/R6/R9-结论.md`。

### 1.6 定级口径

| 级别 | 含义 |
| --- | --- |
| 严重 | 违反安全宪法 / 需求明确 AC 未实现 / 数据丢失或静默损坏 / 用户可感知的功能性缺陷 |
| 中等 | 健壮性、可维护性、可测试性缺陷，或有安全边界弱化但当前不可达 |
| 轻微 | 体验与命名类，不影响正确性 |
| 已核查不计缺陷 | 疑似但经证据排除，或已由用户裁决接受的例外 |
