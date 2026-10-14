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

> **⚠ 本台账的「未完成」项（截至 2026-10-14）**：**BL-6**（真机项，不做代码工作）· **BL-7**（历史文档中的 `设计预览/` 引用属有意保留）·
> **BL-9**（`localStorage` 跨重启，待补「写 → 退出 → 重启 → 读」完整链路验证）。
> **BL-10 已于 v1.6.0 闭环（CL-17）**，不再留在未完成清单。
> **闭环状态与续接入口的单一真相在 `plans/desktop-scratch-editor-v1.6.0.md §17`。**
