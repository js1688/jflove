# 技术债 / 下个版本候选台账（BACKLOG）

> **怎么用**：**开工新版本之前先读这份**（`AGENTS.md` 的「文档目录」一节挂了指针）。
> 这里的每一条都带**证据（文件:行 + 命令）**、**为什么当时没做**、**建议做法**、**验收判据**，
> 目的是让下个版本的会话**不必重新取证**就能直接排期与动手。
>
> 状态约定：`待排期` / `已排期（版本号）` / `已闭环（版本号）`。**闭环的条目不要删**，改成 `已闭环` 并在末尾补一句结果，便于追溯。
> 本文件**不是**版本内计划：版本内计划按 `AGENTS.md §1.1.1` 放 `plans/<模块>-<任务简述>.md`（`plans/` 在 `.gitignore` 内，仅本地）。

- **建立日期**：2026-10-12
- **来源**：v1.5.1 的代码审查（两轮）、测试报告、PMO 记录，以及发布期发现
- **用户决策（2026-10-12）**：以下条目**先记录、不在 v1.5.1 内处理**，由下个版本的另一个开发会话加载后解决

---

## BL-1（中）桌面端徽标色调表与设计系统在 brand / neutral 两档取值不一致 —— ✅ 已闭环（前提失效）

- **状态**：已闭环（v1.6.0，**前提失效，未改任何代码**）｜ **来源**：`文档记录/代码审查报告/v1.5.0.md` 的 M-3
- **闭环依据（实测）**：BL-1 点名的 `tokens.py` 与符号 `BADGE_TONE_ATTRS` 在**整个仓库历史 0 命中**；
  `设计预览/` 目录（含 `assets/design-system.css`）**全历史不存在**。取证命令：
  `git log --all --oneline -- "*tokens.py"`（空）／`Test-Path jflove-desktop/src/config/tokens.py`（False）／
  `Test-Path 设计预览`（False）／全仓 grep `BADGE_TONE_ATTRS` → 仅命中 `文档记录/` 内 8 处
- **现状**：徽标色调只在**一处**定义 ——
  [`jflove-desktop/ui/src/styles/components.css:227-271`](../../jflove-desktop/ui/src/styles/components.css)
  （与 `jflove-web/` 侧**同名同行、逐字节一致**），
  取值正好等于原报告所述的「设计系统值」（brand 暗 `rgb(99 102 241 / 0.18)`/`#c7d2fe`；
  neutral 暗 `rgb(255 255 255 / 0.08)`/`#94a3b8`）
  ⇒ **代码无偏离，不存在「两处各留一个值」的问题**
- **实际处置**：纠正**文档指向**（把「令牌唯一真相 = `设计预览/assets/design-system.css`」改为指向两份 `tokens.css`），
  并把 `src/styles/tokens.css` 纳入 [`scripts/check_web_desktop_parity.py`](../../scripts/check_web_desktop_parity.py)
  的 `PARITY_MANIFEST`
- **附带项复核**：`BADGE_TONE_ATTRS` 的 `info` 档**同样不存在**（全仓 0 命中）；
  `.badge` 只 **5 档**（`brand/success/warning/danger/neutral`，`components.css:227-271`）⇒ 无「扩展档」需要注明
- **原条目前提说明（留痕）**：BL-1 是从 v1.5.0 报告**直接抄录**的条目，抄录时未复核被引用文件是否存在；
  报告自己在 `代码审查报告/v1.5.0.md` 的附录 **C-2 / C-2b** 已承认过 `设计预览/` 与那些探针
  「全历史 0 命中」，且 C-2b 明确指出 M-3 这类**带行号的源码引用**所指向的 `src/ui/kit/*`、`src/ui/theme/*`、
  `src/ui/widgets/*` **从未入库** ⇒ 该结论**不可用仓库复现，不得当作既成事实**

## BL-2（轻）`GET /api/v1/config` 的 OpenAPI 描述与实现不符（列表 vs 字典） —— ✅ 已闭环（v1.6.0）

- **状态**：**已闭环（v1.6.0）** ｜ **来源**：v1.5.1 测试报告 D3
- **闭环证据**：`jflove-server/src/controllers/config_controller.py:42` / `:46-47` 改为「配置项**键值对映射**（`{key: value}`）」并补齐 PUT 的响应字段说明；实测 `app.openapi()` 路径数 = **42**（与 v1.5.1 一致）、`_PLAIN_PATHS` 仍 **3** 条。详见 `文档记录/后端开发记录/v1.6.0.md §2`
- **证据**：`jflove-server/src/controllers/config_controller.py:41-47` —— description 写
  「响应体（加密后）字段：`config`：配置项**列表**，每项含 `key`、`value`」；
  而实现返回的是 **`{key: value}` 字典**（`config_service.get_all()`）。
- **性质**：**行为正确、描述有误**（不是接口缺陷）。
- **建议做法**：把 description 改成「`config`：配置项**键值对映射**（`{key: value}`）」；
  顺手核对同文件其它接口的 description 是否也有同类措辞问题。
- **验收判据**：description 与实现一致；`/docs`（OpenAPI）描述可读；接口行为不变（回归用例应保持全绿）。

## BL-3（轻）Web 端把「响应解密失败」归为网络错误，措辞误导排查 —— ✅ 已闭环（v1.6.0）

- **状态**：**已闭环（v1.6.0）** ｜ **来源**：v1.5.1 测试报告 D5 + 复审（`代码审查报告/v1.5.1.md` R5 相关）
- **闭环证据**：`jflove-web/src/utils/http-client.ts` 新增 `isEnvelope()` + `decryptResponse()`，
  **两处**解密点（POST/PUT/DELETE 与 GET query 信封）均改为：形状不是信封 → 明文直返；是信封但解不开 →
  `ApiError(502, '响应解密失败（会话密钥不匹配或响应被篡改）')`（对齐桌面端 `_is_envelope()` 口径）。
  新增 **3 条**用例（含「明文不误伤」判据 + GET 路径），`npm run test:coverage` → **211 passed**。
  详见 `文档记录/Web端开发记录/v1.6.0.md §1`
- **遗留**：错误响应体的解密（`tryDecryptError()`）在信封解不开时退化为 `HTTP <状态码>` —— 已带真实状态码、**不误导**，
  故不在本条范围；如需对齐桌面端 `_decrypt_error` 应另立条目（见 Web 记录 §4 W-1）
- **证据**：`jflove-web/src/utils/http-client.ts:102-109`（POST/PUT/DELETE 的 catch）——
  任何非 `ApiError` 异常都被包成 `ApiError(0, '网络错误：…')`，而**解密失败**（`decryptEnvelope` 抛）也走这里；
  加密 GET 路径（同文件约 `:165-171` 一带）同型。
- **性质**：**会抛错、非静默降级**（安全语义没问题），但用户看到的文案是「网络错误」，
  真实原因是「解密失败」，**排查方向会被带偏**。
- **建议做法**：对齐桌面端口径 —— 单独 try/catch 解密调用，抛
  `ApiError(502, '响应解密失败（会话密钥不匹配或响应被篡改）')`；
  桌面端已这么做（`jflove-desktop/src/utils/http_client.py` 的 `_is_envelope()` + `ApiError(502)`，v1.5.1 修复 M-4 时一并落地）。
- **验收判据**：用例 —— 喂「200 + 解不开的信封」→ 断言抛 `ApiError(502)` 且文案**不含** `nonce`/`ciphertext`；
  喂「200 + 明文 body」→ 直返成功（明文白名单路径不误伤）。

## BL-4（中）覆盖率工具未接入，SKILL 的「核心逻辑 ≥ 90%」无从验证 —— ✅ 已闭环（v1.6.0，不含 CI 门槛）

- **状态**：**已闭环（v1.6.0）** ｜ **来源**：v1.5.1 测试报告「覆盖率：未测量」
- **闭环证据**：
  - **Web 侧**：`jflove-web/package.json` 加 `@vitest/coverage-v8@2.1.9` + `"test:coverage"`；
    实测 `npm run test:coverage` → **Test Files 20 passed / Tests 211 passed**，`All files` 覆盖率 **21.1%**，**exit 0**
  - **服务端**：**新建** `jflove-server/requirements-dev.txt`（`pytest-cov==7.1.0`，**不入运行时 `requirements.txt`**）；
    实测 `pytest tests/ -q --cov=src --cov-report=term-missing` → **180 passed / TOTAL 73%**，**exit 0**
  - **不设 CI 门槛**（D16 未授权改 `.github`）—— 两个数字是**首次可测量基线**，不作为通过判据
  - 详见 `文档记录/Web端开发记录/v1.6.0.md §2` 与 `文档记录/后端开发记录/v1.6.0.md §8`
- **新登记**：`pytest` 本身从未在任何 requirements 中声明（预装在 venv 的既有事实），本次按范围纪律**未顺手补**，见后端记录 §8.3
- **现状证据（实测）**：全仓未配置任何覆盖率工具 ——
  `jflove-web/package.json` 无 coverage 脚本、devDeps 无 `@vitest/coverage-*`；
  `jflove-server/requirements.txt` 无 `pytest-cov`；无 `pytest.ini`/`pyproject.toml`/`setup.cfg`/`tox.ini`；
  全仓 `--cov` / `cov-report` / `addopts` 0 命中。
  而 `.claude/skills/testing/SKILL.md` 写着「覆盖率目标：核心业务逻辑 ≥ 90%」⇒ **目标与工具脱节**。
- **建议做法**：web 加 `@vitest/coverage-v8` + `"test:coverage"` 脚本；
  server 加 `pytest-cov`（开发依赖，**不入** `requirements.txt` 的运行时清单）+ 统一配置；
  决定是否把覆盖率门槛写进 CI（`ci-guards.yml` 或各 build workflow），以及门槛值。
- **验收判据**：一条命令能生成报告（`npm run test:coverage` / `pytest --cov`）；
  报告数字来自仓库内可执行命令；若设门槛，CI 退出码即判据。

## BL-5（轻-中）Python 版本口径：要求 3.14+，本机 venv 实测 3.13.14 —— ✅ 已闭环（v1.6.0，取「记录差异」）

- **状态**：**已闭环（v1.6.0）** —— 按 **D14 取「记录差异」**，**不升级本地环境** ｜ **来源**：v1.5.1 测试报告环境一节 + PMO §5
- **闭环证据**：`jflove-desktop/README.md`「运行环境」下方新增「Python 版本口径」块，
  显式记录「要求 3.14+ / `venv-win` 实测 **3.13.14**（本机只有 `Python313`）/ v1.6.0 用例全绿 / **不升级环境**」；
  服务端侧证据见 `文档记录/后端开发记录/v1.6.0.md §9`；实测 `python -V` = **3.13.14**（未变）
- **⚠ 仍留在台账的事**：「把本地两个 venv 升到 3.14」尚未做 —— 如需做，应作为**独立条目**排期（重建 venv + 重装 PySide6/cryptography/requests/pyinstaller）
- **证据**：要求侧 —— `jflove-server/Dockerfile:5` `FROM python:3.14-slim`、
  `AGENTS.md:45-46`（Python 3.14+）、根 `README.md:3` badge；
  实测侧 —— `jflove-server/venv-win` 与 `jflove-desktop/venv-win` 均为 **3.13.14**，
  且本机 `Programs\Python` 下**只有 `Python313`**（无 3.14、无 `py` launcher）。
- **性质**：**不是文档写错**，是**本地环境低于项目要求**（v1.5.1 的全部用例在 3.13.14 下全绿）。
- **两条可选做法**：
  1. **升级环境**：安装 Python 3.14 → 重建两个 venv → 重装依赖（PySide6 / cryptography / requests / pyinstaller …）→ 复跑三端测试。
  2. **记录差异**：在开发环境说明（`AGENTS.md §3.1` 或各模块 README）写清「本地实测 3.13.14 亦全绿；CI 与镜像用 3.14」，
     并把「本地升级到 3.14」留在本台账。
- **验收判据**：`python -V` 与文档口径一致；或文档显式记录差异且 CI 仍绿。

## BL-6（信息项）13 项真机像素/几何细项未逐项回报

- **状态**：观察中（用户 2026-10-12 反馈「已发布成功、已体验成功」）
- **内容**：见 `文档记录/测试报告/v1.5.1.md §10`（V-1~V-15，V-7 已闭环、V-12 已撤回）：
  关窗后整屏无浮层残留、拖动/缩放跟随与是否滞后、全屏不被拉回、全屏关窗恢复、系统最小化是否带走浮层、
  1024/1280/1366/1920 下大纲侧栏是否裁切、Win/Linux 一致性、托盘恢复、并发数保存立即生效、工具栏位移观感、全屏 vs `visible=false` 口径观感。
- **处置**：无头环境（`QT_QPA_PLATFORM=offscreen`）**无法验证**，已明确不伪造通过（`LESSONS.md` L27/L40）。
  若后续使用中发现异常 → 按 `AGENTS.md §7.5.3` 快速修复通道或 hotfix 处理；否则无需动作。

## BL-7（轻）`设计预览/` 的失效引用（源码与脚本范围已在 v1.6.0 清理；历史文档保留原文 + 更正）

- **状态**：**部分闭环（v1.6.0）** ｜ **来源**：v1.6.0 全仓过时内容清查
- **背景**：仓库根 `设计预览/` 目录**全历史不存在**，但曾散落 **30+ 处**引用（技能 / README / 设计文档 / 源码注释）
- **v1.6.0 已做**：源码与脚本范围（`jflove-*/src`、`jflove-*/ui/src`、`tests`、`scripts/`、`build.py`）**死引用归零**；
  活文档（`.claude/skills/**`、`AGENTS.md`、各模块 README）**就地改对**
- **仍保留**：`文档记录/` 下历史快照按 `LESSONS.md` **条目 L21** 采用「保留原文 + 追加更正」⇒ 仍能看到 `设计预览/` 字样，**这是有意为之，不是漏改**
- **验收判据**：`grep` 源码/脚本范围 **0 命中**；历史文档中的引用必须**同段带更正标注**

## BL-8（严重，数据安全）`complete_upload` 覆盖写非原子：先截断再写、hash 失败即删原文件 —— ✅ 已闭环（v1.6.0）

- **状态**：**已闭环（v1.6.0）** ｜ **来源**：v1.6.0 前期调研（P-5）
- **闭环证据**：`jflove-server/src/services/file_service.py` 的 `complete_upload` 改为
  「**同目录临时文件**（保证 `os.replace` 同文件系统原子）→ 合并 + 校验都只写临时文件 →
  校验通过后 `os.replace()` 原子替换」；**`os.remove(dest_file)` 从代码中消失**；失败只删临时文件；
  mtime 还原作用在临时文件上。新增 **11 条**注入式用例（`tests/test_upload_atomic_v1_6_0.py`）；
  **负向对照**：换回 HEAD（截断 + 删原文件）实现 → **8 failed, 3 passed**；改回后全量 **180 passed**、`flake8` 0 警告。
  详见 `文档记录/后端开发记录/v1.6.0.md §3 / §5.2 / §5.3`
- **⚠ 本条未消除的相邻风险已另立条目**：目标路径本身仍可被穿越（`filename` 未过滤分隔符）→ **BL-10**（严重）—— **已于 v1.6.0 闭环**（同函数两层防护 + 39 条用例）
- **位置**：`jflove-server/src/services/file_service.py` 的 `complete_upload`
- **缺陷**：① 直接 `open(dest_file, "wb")` **先截断目标文件** ⇒ 中途失败即"原文件变成部分内容"；
  ② hash 不匹配时 **`os.remove(dest_file)`** ⇒ **原文件被删除**；③ mtime 还原作用在已被破坏的文件上
- **触发面**：**任何上传覆盖同名文件** —— Web / 桌面 / 移动**三端共用**，文件管理与目录同步都会走到
- **建议做法**：临时文件**放在目标目录内**（保证 `os.replace` 同文件系统原子）→ 合并 + 校验都写临时文件 →
  校验通过后 `os.replace()` 原子替换；失败**只删临时文件**，**绝不触碰原文件**；mtime 还原作用在临时文件上
- **验收判据**：注入式用例 —— 让 `complete` 中途失败 / hash 不匹配 → 断言**原文件内容与 sha256 不变**；
  **负向对照**：改回截断写法 → 用例**必须失败**
- **归属**：Phase 3 后端 + Phase 6 补负向对照用例；**回归三端上传与目录同步**

## BL-9（中）桌面端渲染层的 `localStorage` 偏好可能不跨重启（默认 profile 是 off-the-record）

- **状态**：**待验证**（验证后决定是否排期）｜ **来源**：v1.6.0 的 R-1 前置实测（`jflove-desktop/poc/editor-probes/R1-结论.md`）
- **证据（已实测）**：QtWebEngine 的**默认 profile 是 off-the-record** —— `storageName=''`、`isOffTheRecord=True`、
  `persistent=…\QtWebEngine\OffTheRecord`、HTTP 缓存**在内存**。从 `import` → `QApplication` →
  `QtWebEngineQuick.initialize()` → `defaultProfile()` → `QWebEnginePage()` → `QWebEngineView()` → `show` + `loadFinished`
  → **写 `localStorage`**，全程 **user-data 目录新增文件数 = 0**；`%LOCALAPPDATA%\…\QtWebEngine\Default\` **不存在**。
- **影响面**：桌面端渲染层在用的 `localStorage` 键 ——
  `ui/src/utils/session.ts` 的 `serverUrl` / `localSessionMaxSeconds` / `notesDiskId` / `notesPath`，
  以及 `ui/src/stores/theme-store.ts` 的主题模式。
  若确实不跨重启，则这些**纯渲染层偏好每次启动都会丢失**（主题有 Python 侧 `theme.json` 兜底、
  `serverUrl` 有 `session.json` 兜底，**但 `notesDiskId` / `notesPath` / `localSessionMaxSeconds` 是否另有落盘未查证**）。
- **⚠ 本条的判定前提（未验证）**：R-1 只证了"**写入时不落盘**"，**未做**「写 → 退出 → 重启 → 读」的完整链路验证。
  **必须先补这个验证**：能读回来 ⇒ 本条不成立（降级为"无问题"）；读不回来 ⇒ 本条成立。
- **建议做法**（与 v1.6.0 既有决策一致）：纯渲染层偏好改为 **Python 侧持久化** ——
  v1.6.0 的快捷键（`USER_DATA_DIR/storage/shortcuts.json`）与文件关联（`file_assoc.json`）**已经这么做**，
  正是为避开这个坑；可照同一模式迁移 `session.ts` 的偏好。
- **验收判据**：① 完整链路验证脚本（写 → 退出 → 重启 → 读）留下原始输出；② 若成立，迁移后复跑该脚本，
  断言偏好**跨重启保留**；③ 已有 Python 兜底的项（主题 / 服务端地址）不得回归。

## BL-10（严重，**安全**）`upload/init` 的 `filename` 未过滤路径分隔符 ⇒ 可构造目录穿越目标路径 —— ✅ 已闭环（v1.6.0）

- **状态**：**已闭环（v1.6.0）**（用户 2026-10-14 裁决「修」，采纳本条「v1.6.0 内修」的建议）｜ **来源**：v1.6.0 的 BL-8 实施期额外发现
- **闭环证据**：`jflove-server/src/services/file_service.py` 新增 `_validate_filename()`（拒绝空 / `.` / `..` / `/`、`\`、NUL / Windows 盘符前缀 / `basename != name` / 平台 `sep`·`altsep`），
  `init_upload` 调用它把 `filename` 收窄为**纯文件名**；`complete_upload` 再用 `_safe_join(dest_dir, filename)` 兜底（越界转 `ValueError` ⇒ 400），
  临时文件名改取 `os.path.basename(dest_file)`。新增 **39 条**用例（`tests/test_upload_filename_traversal_v1_6_0.py`：
  服务层口径 / 接口层攻击面 / 第二层兜底 / 负向对照）；**负向对照**：把 `file_service.py` 还原为 HEAD 版本 → **33 failed, 6 passed**；
  修复后全量 **219 passed**（180 + 39）、`flake8` 0 警告。详见 `文档记录/后端开发记录/v1.6.0.md §10`
- **不在面内的相邻点**（只登记不顺手改）：`get_stream_range`（`file_service.py:652`）同样把 `filename` 拼进路径，但结果整体过了 `_safe_join` ⇒ 不可穿越
- **位置**：`jflove-server/src/controllers/file_controller.py:152-159`（`filename` 只 `strip()` + 非空校验）、`file_service.py::init_upload`（只校验 `rel_path`）、`file_service.py::complete_upload`（`os.path.join(dest_dir, session["filename"])`）
- **影响面**：**需已认证且对该磁盘有写权限**（`init_upload` 有 `check_disk_permission`）才能触发 ⇒ 属**认证后路径穿越**；一旦触发可写到虚拟磁盘根之外（受进程权限限制）
- **为什么现在提**：BL-8 的原子写改造**沿用**了同一条 `os.path.join(dest_dir, filename)` —— 虽已修掉"失败删原文件"，但**没有**消除目标路径本身可被穿越
- **建议做法（已实施）**：在 `init_upload`（或 controller）校验 `filename` —— 拒绝含 `/`、`\`、`..` 或 `os.path.sep`/`os.path.altsep` 的值，并要求 `os.path.basename(filename) == filename`；`complete_upload` 侧再用 `_safe_join(dest_dir, filename)` 兜底
- **验收判据（已满足）**：注入式用例 —— `filename = "../../evil.txt"` → **400** 且目标路径外**无文件产生**（快照比对）；正常文件名（中文 / 空格 / 点号 / 隐藏文件）不受影响；**负向对照**：去掉校验 → 用例**必须失败**（实测 33 failed）

---

## 已闭环（本轮做掉的，留痕便于追溯）

| 编号 | 项 | 闭环于 | 结果 |
|------|----|--------|------|
| CL-1 | 桌面端 **M-4**：200 响应「是加密信封但解不开」被当明文成功（v1.5.0 遗留中等、安全相关） | v1.5.1 | `_is_envelope()` 判形状 + 抛 `ApiError(502)`；24 例 + 负向对照 3 failed |
| CL-2 | **V-1**：桌面端 Web UI 的 `APP_VERSION` 不在 `sync_version.py` 位置表（界面显示旧版本号） | v1.5.1 | 位置表 7 → 8 处；`--check` 现在覆盖它 |
| CL-3 | **D1**：`test_stream_v1_1_0.py::Test安全宪法流式预览` 依赖他类副作用、单独跑假失败 | v1.5.1 | 搬进 session fixture `no_perm_session`；三粒度 + 两组反向对照 |
| CL-4 | **D7**：跨端门禁未接 CI | v1.5.1 | `ci-guards.yml` 新增 job `cross-end-consistency-guard`；云端 4 步全绿 |
| CL-5 | **D6**：安全用例第 4 类（错误响应加密）缺 422 判据 | v1.5.1 | 新增 9 例 + 注入式负向对照 |
| CL-6 | **桌面端 README 文档债**（含已删文件、onefile 表述、v1.4.2 版本号、已移除依赖） | v1.5.1（提交 `bab4e4a`） | 版本号/架构表/构建章节/目录树/依赖清单全部对齐现状 |
| CL-7 | **Service Worker 文档过期**（web-frontend/code-review SKILL、jflove-web README） | v1.5.1（提交 `bab4e4a`） | 改为 MSE 主路径口径；历史版本文档按 `LESSONS.md` L21 保留原样 |
| CL-8 | **v1.5.0 文档失实**（声称 M-4 已修、引用的用例/探针文件在仓库全历史 0 命中） | v1.5.1 | 两份 v1.5.0 报告末尾追加事后更正 C-1~C-4 + 流程改进（测试数字必须来自仓库可执行命令或 CI 日志） |
| CL-9 | **移动端同型缺陷复核**（是否存在与 M-4 同型的信封静默降级） | v1.5.1 复核 | **不存在**：`jflove-app/lib/utils/http_service.dart:166-168` 解不开即抛 `AppException` |
| CL-10 | **BL-1**：徽标色调前提失效（`tokens.py` / `BADGE_TONE_ATTRS` **全历史 0 命中**） | v1.6.0 | 改为「已闭环（前提失效）」；纠正「令牌唯一真相」指向两份 `tokens.css`；`tokens.css` 纳入 `PARITY_MANIFEST`（门禁清单 2 → 3 条） |
| CL-11 | **BL-2**：config OpenAPI 描述「列表 vs 字典」 | v1.6.0（Phase 3） | 描述改「键值对映射」+ 补齐 PUT 响应字段；路径数 42 / 白名单 3 条未变 |
| CL-12 | **BL-8**：`complete_upload` 覆盖写非原子（**数据安全**） | v1.6.0（Phase 3） | 同目录临时文件 + `os.replace` 原子替换；`os.remove(dest_file)` 消失；11 条新用例 + 负向对照 `8 failed, 3 passed` |
| CL-13 | **BL-3**：Web 端解密失败被误报「网络错误」 | v1.6.0 | 两处解密点单独判定 → 合成 `ApiError(502)`；+3 用例（211 passed）；明文形状不误伤 |
| CL-14 | **BL-4**：覆盖率工具未接入 | v1.6.0 | web `@vitest/coverage-v8` + `test:coverage`（21.1%）；server 新建 `requirements-dev.txt`（`pytest --cov` → 180 passed / 73%）；**不设 CI 门槛** |
| CL-15 | **BL-5**：Python 版本口径（要求 3.14+ / 实测 3.13.14） | v1.6.0 | 取 D14「记录差异」：桌面端 README 显式留痕；**不升级环境** |
| CL-16 | 全仓过时内容清查（批次 0~7） | v1.6.0 | **65 文件 / 962 行**；源码与脚本范围死引用归零；历史文档按 L21「保留原文 + 追加更正」 |
| CL-17 | **BL-10**：`upload/init` 的 `filename` 未过滤路径分隔符（**严重 · 安全**，认证后目录穿越） | v1.6.0 | `_validate_filename()` 收窄为纯文件名 + `complete_upload` 的 `_safe_join` 兜底；39 条新用例 + 负向对照 `33 failed, 6 passed`；全量 219 passed |
| CL-18 | **S-1 + S-2**：`_safe_join` 前缀判据可被同名前缀兄弟目录绕过 + `_validate_filename` 只覆盖一处（**严重 · 安全**） | v1.6.0（`0cd63a6`） | 新建 `src/utils/path_safety.py`（`is_within_base` / `safe_join` / `validate_filename`）作为**单一实现**，`file_service` / `sync_service` 全部调用点委托；32 条参数化用例 + 负向对照 `3 failed, 29 passed`；服务端 251 passed |
| CL-19 | **S-7**：草稿索引损坏后写路径把空索引整体回写 ⇒ 其它窗格草稿静默丢失（违反 AC-S2②） | v1.6.0（`24d485b`） | 索引不可用但正文仍在 ⇒ 按 `pane-*.txt` 重建条目并 warning；15 条新用例 + 负向对照 `11 failed, 4 passed`；桌面端 430 passed |
| CL-20 | **M-5**：UTF-16LE 的 CRLF 被误判为 `lf`（未编辑保存即改写全文换行，AC-S4③） | v1.6.0（`8b3cb6d`） | `_detect_line_end(sample, encoding)` 先按编码解码再数换行；8 条新用例（含字节级往返）+ 负向对照 `6 failed, 28 passed`；438 passed |
| CL-21 | **S-3**：渲染层/桥的暴露面与文档来源无关，外壳无导航策略（r7 探针 T2/T3 实证） | v1.6.0（`1f59ad5`） | `ShellPage.acceptNavigationRequest` 白名单（`about:blank` + web 根内 `file://`）+ `createWindow → None` + 外链交 `QDesktopServices.openUrl`（**同时闭环 L-1**）；23 条新用例；461 passed |
| CL-22 | **S-4 + M-6**：AC-46② 未实现（转交前不恢复窗口）+ 降级分支静默丢命令行路径（AC-48） | v1.6.0（`6ccbd4e`） | `bootstrap_single_instance()` / `build_paths_sink()` 两个可测接缝，两条分支都不丢 `cli_paths`；8 条新用例 + 负向对照 `3 failed, 5 passed`；469 passed |
| CL-23 | **S-5 + S-6 + S-8 + M-13**：工具栏无挂载点 / `viewMode` 无消费者 / 无 wheel 监听 / 打开先落类型默认视图（**严重 ×3 + 中等**） | v1.6.0（`a3c7f18`） | `ScratchPane` 常挂工具栏 + 五态分派 + 宿主"只隐藏不卸载"；`ScratchEditorHost` 非 passive wheel（Ctrl 缩放 / Shift 横滚）；barrel 补导出；`applyReadResult` 一律 `viewMode:'edit'`；lint/build exit 0 + 真 CM6 校验 |
| CL-24 | **S-9**：AC-11 期望串自相矛盾（1 基第 2~4 列 vs `…ijk`） | v1.6.0（`a961d09`） | 用户裁决"以实现为准"：需求文本 + 常量 + 注释三处一致为 `bcd\nh  \njkl`；真跑 CM6 `Text` 校验一致 |
| CL-25 | 审查要求的"工具栏已挂载"断言（`ui/` 无测试运行器，见 BL-14） | v1.6.0（`4b3ccb7`） | 新增 `poc/editor-probes/r8-ui-wiring.py`：9 项接线锚点 + 2 项反向判据，退出码即判据；负向对照 `1 项 FAIL / exit 1` → 还原后 11 项全过 |
| CL-26 | **BL-14 + BL-31**：`ui/` 无测试运行器 ⇒ 渲染行为只有静态接线判据兜底 | v1.6.0（Phase 6） | 引入 vitest + jsdom + @testing-library/react（devDeps，`npm i -D` 65 包）+ `ui/vitest.config.ts` + `src/test/setup.ts`；**20 个用例文件 / 196 例（153 passed + 43 expected fail）**覆盖 AC-8/10/11/12/13/25~35/54 与 S-5/S-6/S-8/M-13 回归；AC-10 已从 r8 的静态断言升级为**真 DOM 断言**（真 `KeyboardEvent` + `defaultPrevented` + `stopPropagation`），r8 降级为辅助 | `npm run test` exit 0（`Test Files 20 passed (20)` / `Tests 153 passed \| 43 expected fail (196)`）；lint 0 / build 0 |

> **⚠ 本台账的「未完成」项（截至 2026-10-14）**：**BL-6**（真机项，不做代码工作）· **BL-7**（历史文档中的 `设计预览/` 引用属有意保留）·
> **BL-9**（`localStorage` 跨重启，待补「写 → 退出 → 重启 → 读」完整链路验证）·
> **BL-11 ~ BL-16**（v1.6.0 Phase 4a 新增登记，见下节）。
> **BL-10 已于 v1.6.0 闭环（CL-17）**，不再留在未完成清单。
> **2026-10-14 更新（代码审查回修轮后）**：Phase 5 定级的 **9 条严重项已全部闭环**（CL-18 ~ CL-24；S-1~S-8 为缺陷修复，S-9 为产品裁决后同步），
> 中等项 M-5 / M-6 / M-13 亦随批闭环；**未完成清单新增 BL-18 ~ BL-31**（回修轮未覆盖的中等/轻微项 + 2 条观察项 + r8 探针的过渡性质）。
> **闭环状态与续接入口的单一真相在 `plans/desktop-scratch-editor-v1.6.0.md §17`。**
> **2026-10-14 更新（Phase 6 测试轮后）**：**BL-14 / BL-31 已闭环（CL-26）** —— `ui/` 已引入 vitest 与真 DOM 断言；
> 本轮**未改任何生产代码**，故 BL-18 ~ BL-30 状态不变（其中 M-4 / M-9 / M-10 / M-11 / M-12 已被新用例咬住并留 `it.fails` / `xfail` 证据）；
> **新增 BL-32 ~ BL-43**：2 条严重缺陷（AC-13 未实现且现绑定有破坏性、AC-36/37 整条未实现）+ 新发现 N-1/N-2/N-3 + 未覆盖面登记。
> 详见 `文档记录/测试报告/v1.6.0.md §7`。

---

## BL-11 ~ BL-16：v1.6.0 Phase 4a 新增登记（2026-10-14，交 **Phase 5 复核** / Phase 7 落账）

> 来源：`文档记录/桌面端开发记录/v1.6.0.md §7.1 / §8 / §9`，以及计划 §17.10 D 表。
> 这些**不是缺陷**，而是「本版本做了取舍、需要复核或后续排期」的项——按 `AGENTS.md §8` 逐条留痕。

| 编号 | 级别 | 项 | 事实（含证据） | 建议做法 | 验收判据 |
| --- | --- | --- | --- | --- | --- |
| **BL-11** | 中 | **设计偏离 D-1**：`ensure_editor_bundle()` 改为「缺失时**就地生成**、生成失败才 fail」 | 设计 §6.1.1 原文是「缺失即 `fail()`」；但 `build-desktop.yml:46-51` 只生成 Mermaid 分包、`:80` 直接跑模块 build.py，而 **D16 未授权改 `.github`** ⇒ 按原文 **CI 必红**（Phase 8 阻塞）。证据：移走产物后调用 → 重新生成成功且**逐字节一致**（1,438,477 B / sha256 相同） | 二选一：① 保留本偏离（无需改 CI）；② 用户授权改 `.github` 加一步 `node scripts/build-codemirror-bundle.mjs`，回到设计原文口径 | 桌面端 CI（Windows onedir + Linux RPM）**两个作业都绿**；本地 `python build.py -m desktop` 在**干净 clone** 上能一次跑通 |
| **BL-12** | 中 | **文档张力 T-1**：`.iss` 的 `[Registry]` 由谁写 | §6.7.2 的裁决列要求「**构建期生成静态段**」，§5.1 消费方 ③ 写「由 **Python 侧安装后写入**」——两者互斥。实现按 §6.7.2（自洽性依据：卸载清理必须在 Inno `[Code]` 里遍历同一清单，Python 在卸载阶段不运行） | Phase 5 复核该选择；若认定应以 §5.1 为准，则需改回**运行时注册**并同步 AC-43 ① 的「安装后键存在」措辞 | 设计与实现**说法一致**（二者只留一个）；`file-assoc.iss` 的 `[Registry]` 与 `[Code]` 清理清单**同源**（同一 manifest 派生） |
| **BL-13** | 中 | **顺序依赖 T-2**：`ui` 构建期 import `public/vendor/editor.version.json` | 全新 clone / 未跑过分包时 `tsc --noEmit` 会失败（AC-R2 判据在**干净环境**下不成立）。这是设计 §6.1.1 的有意取舍（用构建期失败换取"内核一定在包里"） | 发布/CI 文档中写明顺序：先 `build.py::ensure_editor_bundle()` 再前端构建；BL-11 的自动生成即兜底 | 干净 clone 上 `python build.py -m desktop` 一次通过；`npm run build` 单独跑失败时有**明确指引**（不是晦涩的模块找不到） |
| **BL-14** | 轻-中 | **覆盖缺口 T-3**：`ui/` 无测试运行器 | §6.1.2 要求把 `blockSelectCopy` 抽成纯函数「以便单测」，但 `ui/package.json` 只有 lint/build/dev（加 vitest 超出 §3.1 的 35 项文件清单）。当前只有注释里的 AC-11 手算样例 | Phase 6 决定是否引入 vitest（或把纯函数搬到 Python 侧测）；至少为 `block-select.ts` / `toolbox.ts` 补真实用例 | `blockSelectCopy` 的 AC-11 样例（`abcdef\ngh\nijklmn` 选 2~4 列 ⇒ `bcd\nh  \nijk`）与工具箱 12 项**有自动化用例**且纳入自测命令 |
| **BL-15** | 轻 | **生成物归属 T-5**：`ui/public/file-assoc.manifest.json` 入库 | 设计 §5.1 写它是「生成物，git **忽略**」，但 `.gitignore` 只忽略 `ui/public/vendor/`，且本版本**未授权改 `.gitignore`** ⇒ 改为**入库 + `--check` 守门**（与 `jflove.xml` / `file-assoc.iss` 同模式） | 保持现状（入库 + 判据）或在授权后加 `.gitignore` 规则并改为构建期生成 | `python jflove-desktop/scripts/gen_file_assoc_assets.py --check` → exit 0；**反向验证**（夹具加 `.zzz`）→ exit 1 |
| **BL-16** | 轻 | **方法学 L-1**：测试里 patch PySide6 **类属性**会让 pytest 在 `QApplication` 析构期崩 `0xC0000409` | `monkeypatch.setattr(QFileDialog, "getOpenFileNames", …)` 导致整套 pytest 崩（同命令 3/3 复现；去掉该文件 3/3 exit 0）。已改为 `editor.py` 内的模块级接缝 `_open_file_names()` / `_save_file_name()` | 后续写 Qt 桥测试一律照此：**patch 本模块的薄接缝，不 patch Qt 类**；在 SKILL/测试约定里写明 | 含 Qt 用例的文件单独跑与全量跑都 exit 0（无 `0xC0000409`） |
| **BL-17** | 中 | **设计缺口**：快捷键偏好的**桥方法在 §7.1 的表里不存在** | §4.2.4 规定了存储位置与结构（`USER_DATA_DIR/storage/shortcuts.json`）并禁止渲染层碰文件系统 / localStorage（R-1：默认 profile off-the-record），但方法表**漏列读写方法** ⇒ 界面存不进去（桥回「未登记的方法」）。已在 `src/bridge/editor.py` 按命名空间一致性补齐 `editor.shortcutsLoad` / `editor.shortcutsSave`（工作线程、原子写、读取容错、写入校验），并加了 24 条用例 | ① 认可补齐（推荐，无需改设计）；② 或在设计文档补上这两个方法（Phase 5 定） | 设置页改键 → 保存 → 重启后键位仍在（`shortcuts.json` 落地）；`tests/test_bridge_editor_shortcuts_v1_6_0.py` 全通过 |

> **⚠ 另有一条已修的真缺陷（不留在未完成清单，仅留痕）**：`ui/src/config/shortcuts.ts::formatShortcut`
> 对「仅修饰键」chord 会把修饰位与键名各输出一次 ⇒ `normalizeShortcut('Alt') === 'Alt+Alt'`，导致
> 第 14 项「块选择修饰键」**永远改不了键**且会写伪 override。已于 `52f75f1` 修复并实测
> （`normalizeShortcut('Alt') === 'Alt'`、`defaultNormalizationConsistent() === true`）。
> 另有一条并行集成点（`ScratchPaneLayer` 的捕获监听在改键时重复触发窗格动作）同批修复。

---

## BL-18 ~ BL-31：v1.6.0 代码审查回修轮新增登记（2026-10-14，**回修后仍未修**）

> 来源：`文档记录/代码审查报告/v1.6.0.md`（§13 回修台账记录了已闭环的 S-1~S-9 与 M-5/M-6/M-13）。
> 本节收录**回修轮未覆盖**的中等项（M-1 ~ M-4、M-7 ~ M-12）、轻微项批量（L-3 ~ L-21）与 2 条新观察项。
> 级别沿用审查报告定级；"事实"列的详细取证、行号与复现命令见审查报告对应小节，不在此重复。

| 编号 | 级别 | 项 | 事实（含证据） | 建议做法 | 验收判据 |
| --- | --- | --- | --- | --- | --- |
| **BL-18** | 中 | **M-1** 退出标记写入吞掉全部异常 + 与草稿库私有原语紧耦合 | 审查报告 §4 M-1：退出标记路径捕获 `Exception` 后无任何可观测信号 | 公开 `mark_abnormal_exit` 语义，写失败给出 warning 或返回状态 | 标记写失败时日志/界面可观测；退出握手用例覆盖"写失败"分支 |
| **BL-19** | 中 | **M-2** 盘符判定只挡单字母 ⇒ `ab:c.txt` / `CON` 放行；落 ADS 名后 `os.replace` 失败 → 500 + 0 字节残留 | 审查报告 §4 M-2（`file_service.py:141`；实测 `WinError 87`） | 设备名（`CON`/`PRN`/`AUX`/`NUL`/`COM1`…）与非法卷标名统一拦截；`OSError` 归为 400 并清理基文件 | 新增用例：设备名与 `ab:c.txt` ⇒ 400 且磁盘零残留 |
| **BL-20** | 中 | **M-3** 版本范围超出需求点名清单 + 需求与 BACKLOG 互相矛盾 | 审查报告 §4 M-3、§9.1（`jflove-app` 4 文件为已裁决 D-A 例外；9/21 文件未点名；AC-B1⑥ 与 BL-7 措辞冲突） | 需求侧补登记已发生的范围；把 AC-B1⑥ 与 BL-7 的措辞对齐 | 需求文档与 BACKLOG 不再互相矛盾；范围清单可机械核对 |
| **BL-21** | 中 | **M-4** 只读目标写失败路径残留**含本次新正文**的临时文件（AC-41④） | 审查报告 §4 M-4（`editor_service.py:306-310`，实测残留 `.ro.txt.jflove-XXXX.tmp`） | 临时文件不继承只读位 + 失败后清理（`chmod` 兜底） | AC-41④ 用例通过：失败后目录内无含新正文的临时文件 |
| **BL-22** | 中 | **M-7** GB18030 兜底不可证伪 ⇒ AC-S4④ 点名的 Latin-1 样本被静默判成 `gbk` | 审查报告 §4 M-7（`text_detect_service.py:404-416`；实测 3/5 样本误判） | 兜底命中必须给出可见信号（如 `encoding_uncertain`），并补 Latin-1 对照 | AC-S4④ 用例通过：Latin-1 样本不被静默判 `gbk`，或给出可见提示 |
| **BL-23** | 中 | **M-8 + L-11 + L-14（合并为"派生链守门"一件事）**：`--check` 缺 `node` 时降级为警告仍 exit 0；构建期从不跑 `--check`；`editor.version.json` 的 `sha256`/`bytes` 是死字段 | 审查报告 §4 M-8、§5 L-11/L-14；回修轮实测：PATH 收窄后 `--check` 仍 **exit 0**；`build.py:428-431` 只跑 `--write` | 缺 `node` ⇒ **fail**；构建期追加一次 `--check`；让消费方真正校验 `sha256`/`bytes` | 缺 `node` 场景 exit ≠ 0；构建期能拦住生成物漂移；陈旧 vendor 被拒 |
| **BL-24** | 中 | **M-9** AC-10：仅 7 项 app-scope 快捷键 `preventDefault`，另 12 项既无行为也不拦宿主 | 审查报告 §4 M-9（`ScratchPaneLayer.tsx:34-45`） | 20 项键位逐项定行为（拦 / 放行）并按表实现 | AC-10 的 20 项宿主行为有组件或真机用例覆盖 |
| **BL-25** | 中 | **M-10** 块选择跨空行时判定为非矩形并返回空串，破坏右补空格口径 | 审查报告 §4 M-10（`block-select.ts:65-67`） | 跨空行仍按"行尾补空格"处理，或明确降级语义并同步需求 | 新增用例覆盖跨空行选区 |
| **BL-26** | 中 | **M-11** 批量保存不过滤只读窗格 ⇒ 只读窗格被标记为脏时内容会被写回原文件 | 审查报告 §4 M-11 | 保存前置条件加 `!read_only`（或 UI 侧不允许把只读窗格置脏） | 用例：只读窗格置脏后批量保存不改原文件 |
| **BL-27** | 中 | **M-12** 非法键位被静默接受（`normalizeShortcut('!!!') === '!!!'`、`isValidShortcut('!!!') === true`） | 审查报告 §4 M-12 | 键位白名单 + 保存前校验，非法键给可见提示 | 用例：`!!!` 被拒且界面可见提示 |
| **BL-28** | 轻（批量） | **L-3 ~ L-21**（L-1 已随 S-3 闭环：外链交系统浏览器） | 审查报告 §5 表 + §11.3（去重后清单） | 逐条按建议处理；其中 L-11 / L-14 并入 **BL-23** | 各条对应判据见审查报告 §5 与 §11.3 |
| **BL-29** | 观察 | **无 BOM 的 UTF-16LE `.txt` 仍被判为 `utf-8`** ⇒ B3 救援分支对白名单扩展名不可达 | 回修轮 M-5 修复时实测（`文档记录/桌面端开发记录/v1.6.0.md §10.2`）：AC-49 让白名单扩展名跳过嗅探，而 NUL 是合法 UTF-8 | 二选一：① 对白名单扩展名补"零字节密度"轻量嗅探；② 在需求里明确"无 BOM UTF-16 不在支持范围" | 二者取一后该场景行为明确且写入文档 |
| **BL-30** | 观察 | **Qt 无头析构期崩溃 `0xC0000409`** 在回修轮全量跑中复现 1 次（QML `Object … destroyed while one of its QML signal handlers is in progress`） | `文档记录/桌面端开发记录/v1.6.0.md §10.1`；同族 BL-16 / L-1 | Phase 6 连续多次全量跑留证；若稳定复现则定位到具体用例并照 BL-16 口径"patch 薄接缝、不 patch Qt 类" | 连续 5 次全量 `pytest` exit 0（无 `0xC0000409`） |
| **BL-31** | 中 | **r8 静态探针是过渡措施**（根因同 BL-14）：S-5/S-6/S-8 的**渲染行为**目前只有静态接线判据兜底 | 回修轮提交 `4b3ccb7`；`ui/` 无测试运行器 ⇒ 无法断言"菜单恰好 12 项""五态分派真实生效" | Phase 6 决定引入 vitest / 组件测试，或把纯函数（`block-select` / `toolbox`）移到可测侧 | 工具栏 12 项、五态分派、Ctrl+滚轮缩放均有**真实渲染/行为**用例，r8 降级为辅助 | **（本轮已闭环：见 CL-26 / BL-14）** |

---

## BL-32 ~ BL-43：v1.6.0 Phase 6 测试轮新增登记（2026-10-14）

> 来源：`文档记录/测试报告/v1.6.0.md`（§7 逐条取证；真机证据在 `jflove-desktop/poc/editor-probes/out/r11-20261014-204307/r11-result.json`、
> `out/r10-20261014-204345/r10-result.json`，门禁记录 `out/v1.6.0-phase6-gates.txt`）。
> 本轮**未改任何生产代码**（测试角色边界），故这里只登记"事实 + 建议 + 验收判据"，交由 Phase 7 裁决。
> **其中 BL-32 / BL-33 建议走 `AGENTS.md §7.5` 快速修复通道，在 v1.6.0 发布前处理。**

| 编号 | 级别 | 项 | 事实（含证据） | 建议做法 | 验收判据 |
| --- | --- | --- | --- | --- | --- |
| **BL-32** | **严重** | **AC-13（Shift 列选择）未实现，且现绑定有破坏性**：真机 `Alt+Shift+↓` 每按一次**复制 2 行**（+50 字节/+2 行），最终是**单区间**选区而非跨行矩形；改动在 1.5~4 s 内被 1500 ms 自动保存**写回原文件** | r11 H3 `detail`：`docLengths [100,100,100,100,100,150,200]`、`prevented [null,true,false,false,false,true,true]`、最终 `{from:100,to:128,anchor:128,head:100,fromLine:5,toLine:6,text:'ABCDEFGHIJKLMNOPQRSTUVWX\nabc'}`、判定串「各行宽度不一致：[3, 24]」；`keymapTruth`：`copyMoveNames=[copyLineDown,copyLineUp,moveLineDown,moveLineUp]`、`rectColumnNames` 只有 `blockComment` 系 4 项、`hasRectSelectionExtension=true`；H3b：`fixtures/rect.txt` 100 B → 200 B（sha `fc67e9f0…` → `c8508d3c…`）+ 日志 `已保存文件: rect.txt#… size=200` + 草稿 `pane-*.txt (200 字节)`。**措辞更正**：审查报告 L-20 写"bundle 缺 `Shift-Alt-Arrow{Left,Right}`"不准确 —— 绑定存在（语义是 `selectSyntaxLeft/Right`），整包**无任何 rect/column 命令** | 两步：① 先让 `Alt+Shift+↑/↓` 不再命中 `copyLineUp/copyLineDown`（改绑无冲突键位，或先禁用）；② 再实现按列扩展（裁决 `ui/src/components/scratch/ScratchEditorHost.tsx:294-295` 的 keymap 与 `:506-512` 的 `rectangularSelection` 优先级）。**在 ① 之前不得发布**（数据破坏面） | r11 的 H3（gating）转 PASS：真机 `Alt+Shift+↓` 不改变 `doc.length`，各 range 宽度按"行尾补空格"口径判定；H3b 的磁盘 sha 前后一致 |
| **BL-33** | **严重** | **AC-36 / AC-37（保存 / 另存为）整条未实现**：快捷键有定义、无应用级接管、`pickSaveTarget` 全仓无调用方 | `ui/src/config/shortcuts.ts:104-177` 定义 `file.save`(Ctrl+S) / `file.saveAs`(Ctrl+Shift+S)（描述与 AC 原文一致）；`ui/src/components/scratch/ScratchPaneLayer.tsx:52-60` 的 app-scope 只接管 7 项（**不含** `file.save`）；`grep -rn pickSaveTarget ui/src` 只命中 `ui/src/services/scratch-service.ts:117-120` 的定义与测试替身；真机 r11 H5c：OS 级 `Ctrl+S` 到达渲染层、`defaultPrevented=false`、`downloads []`、无对话框（与 `src/__tests__/scratch-save-decision.test.tsx` 的 4 条 it.fails 互为独立证据） | 在 app-scope 接入 `file.save`（有 `path` ⇒ `writeText` 写回；无 `path` ⇒ 先 `pickSaveTarget` 再写）与 `file.saveAs`；保留 `flushPendingSaves()` 作为兜底 | r11 H5c 变 PASS（`defaultPrevented=true` 且磁盘 sha 变化）；`scratch-save-decision.test.tsx` 的 4 条 it.fails 全部转正 |
| **BL-34** | 轻（文档口径） | AC-29 树视图数组节点文案不一致 | `ui/src/components/scratch/views/JsonTreeView.tsx:87` 渲染「[…] N 项」；需求/设计写「数组(N 个元素)」；`scratch-views-markdown-json.test.tsx` 1 条 it.fails | 择一改口径（推荐改实现，向需求对齐） | 该 it.fails 转正 |
| **BL-35** | 中 | **N-1 死键**：默认状态下 `pane.next` / `pane.prev` **完全按不出来**；并牵连 M-9 的计数口径 | `ui/src/config/shortcuts.ts:272-275` 的 `DEFAULT_BINDINGS` 取原始 `def.defaultKey`（`Ctrl+Alt+ArrowRight/Left`），而事件侧 `keyboardEventToShortcut` 归一成 `Ctrl+Alt+Right/Left` ⇒ `ScratchPaneLayer.tsx:293` 的 `getBinding(id) === chord` 永不成立；**实际接管 5 项**（`pane.new/close/tile/cascade/file.open`）、待接管 **13** 项（`ScratchPaneLayer.tsx:46` 注释即写 13），审查报告写的"7 项 / 另 12 项"不准；2 条 it.fails 留证 | `DEFAULT_BINDINGS` 统一走 `normalizeShortcut`；同步更正审查报告与 BL-24 的计数 | 2 条 it.fails 转正；文档计数改为 5 项生效 / 13 项待接管 |
| **BL-36** | 轻 | **N-2**：`parseShortcut` 丢弃空片段 ⇒ `'Ctrl+'` 被当"仅修饰键"接受 | `ui/src/config/shortcuts.ts:352-355`；`shortcut-normalize.test.ts` 1 条 it.fails | 空片段即判非法 | it.fails 转正 |
| **BL-37** | 轻 | **N-3**：块选择修饰键白名单允许 `Shift`/`Meta`，但判定只认 `Ctrl`，其余静默回退 `Alt`（界面接受却无效果） | `ui/src/stores/shortcut-store.ts:129-132` + `resolveBlockSelectModifier`（只认 `Ctrl`） | 白名单收敛为 `Ctrl`，或真正实现 Shift/Meta | 用例：所选修饰键真实生效（非静默降级） |
| **BL-38** | 轻 | `xmlSafeMinify` 实为"安全预检空操作"：删除分支不可达 ⇒ `ok:true` 时等价原样回写，与"压缩"承诺不符（安全向正确） | `ui/src/utils/scratch/toolbox.ts:795/799`（删除条件用 `!meaningful`）与 `:779-784`（预检已把该情形整体拒绝）；`toolbox-xml.test.ts` 2 条 it.fails | 实现真实压缩，或把 capability 文案改为"安全预检" | AC-33 的压缩判据有**真实输出**（非原样回写） |
| **BL-39** | 轻 | `tryParseJson` 的 `position` 正则在 Node v24.18.0 上**永不命中** ⇒ 恒报"未知位置" | `ui/src/utils/scratch/toolbox.ts:309-310` 的 `/position\s+(\d+)/i`；V8 报错文案（`Unexpected token '}', {'a':} is not valid JSON`）不含 position | 适配 V8 新文案或多模式匹配 | 非法 JSON 的行列定位可测 |
| **BL-40** | 观察（非缺陷） | `editor_service.write_text()` 的 `read_only` / `bom` 形参**无任何生产调用方**，且 docstring 与实现不符 | `src/bridge/editor.py:305-330` 入参表仅 `path/text/encoding/lineEnding/expectedSha256`；`src/services/editor_service.py:238-239` 形参、`:250` docstring（称"桥层把 `pane.readOnly` 传进来兜一层"）；只读保护实际由 `READ_ONLY_EXTS` + `> TEXT_READONLY_BYTES` 承担 ⇒ **不构成安全缺口** | 删参数或改 docstring（二者择一） | 文档与实现一致；只读两条来源仍有自动化用例 |
| **BL-41** | 中（未覆盖面） | 本轮**未做**的 AC：**AC-18**（计划落点 `toolbox-comment-toggle.test.ts` 未落地）、**AC-50③**（"500 MB 仍 ≤1 s"未实测）、**AC-43**（注册表项与卸载清理）、**AC-44**（真机重启后关联仍生效）、**AC-46③**（单实例第二实例推路径 ⇒ 出现对应窗格） | 测试报告 §5 的 ⏸ 行与本条一一对应；AC-43/AC-44 属"会改本机注册表/程序目录"，需**用户明确授权**后再做 | Phase 7 排期；AC-43/AC-44 先取得授权 | 每条各有可判定的自动化或真机判据 |
| **BL-42** | 观察（降级中） | **BL-30 的 `0xC0000409` 本轮 3 次全量 `pytest` 均未复现** | `505 passed, 1 xfailed` ×3（6.02 / 6.02 / 6.29 s），记录见 `out/v1.6.0-phase6-gates.txt`；xfail 即 BL-21 的只读残留留证 | 继续观察；若复现照 BL-16 口径"patch 本模块薄接缝、不 patch Qt 类"排查 | 连续 5 次全量 `pytest` exit 0（当前 3 次） |
| **BL-43** | 观察 | **覆盖率仍未测量**（不是"跑不起来"，是工程没配置） | `jflove-server/requirements-dev.txt` 只有 `pytest-cov==7.1.0`，但无 `pytest.ini`/`pyproject.toml` 的 `--cov` 配置与 CI 采集；`jflove-desktop/venv-win` 未装 `pytest-cov`；`ui/` 未装 `@vitest/coverage-v8`（Phase 6 的硬约束是"不新增依赖"）；测试报告 §8 因此**不给任何覆盖率数字** | 单独立项：三端装工具 + 阈值写进 CI（与 CL-14 的 web 侧覆盖率口径统一） | 三端均能产出覆盖率报告，且阈值纳入门禁 |
