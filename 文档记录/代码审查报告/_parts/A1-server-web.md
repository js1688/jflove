# A1 面 · 服务端 + Web 端（v1.6.0 Phase 5 审查片段）

> 被审端点 `HEAD` = `c515853`；基线 `049c20d`。工作目录 `D:\github\jflove`。
> **本面只输出意见，未修改任何源代码**。命令均以仓库根为 cwd，Python 用 `jflove-server\venv-win\Scripts\python.exe`。
> 缺陷计数：**严重 2 · 中等 2 · 轻微 3**（另记 6 组「通过项」，不计入缺陷）。

---

## 一、严重

### [严重] `_safe_join` 用字符串前缀判越界，同名前缀的兄弟目录可穿越 → 认证用户跨虚拟磁盘根读/写/删
- 位置：`jflove-server/src/services/file_service.py:104-107`
  ```python
  base_path = Path(base).resolve()
  target = (base_path / rel.lstrip("/")).resolve()
  if not str(target).startswith(str(base_path)):      # :106 ← 前缀比较，非目录包含
      raise PermissionError("非法路径访问")
  ```
- 违反：`AGENTS.md §9.3(10)`（用户资源路由必须验证归属）、`§9.4.13`；直接削弱 **BL-10**「认证后目录穿越」的收敛目标。
- 证据（实测，未抛异常即为穿越）：
  ```
  > jflove-server\venv-win\Scripts\python.exe -c "from src.services.file_service import _safe_join; print(_safe_join(r'D:\disks\alice','../alice2/evil.txt'))"
  D:\disks\alice2\evil.txt          # 越出磁盘根，静默放行
  > ... _safe_join(r'D:\disks\alice','../bob/evil.txt')
  PermissionError: 非法路径访问        # 对照：仅「前缀不同」被挡住
  ```
- 可达入口（`get_stream_range` 的 `filename` **直取客户端**、不过 `_validate_filename`）：
  - `jflove-server/src/controllers/file_controller.py:580-581`（`path = body.get("path","")` / `filename = body.get("filename","").strip()`）
  - `file_controller.py:610-613` → `file_service.get_stream_range(..., path, filename, ...)`
  - `file_service.py:702-703`（`rel_full = os.path.join(path.lstrip("/"), filename)` → `_safe_join(base, rel_full)`）
  - 取 `path=""` + `filename="../<同前缀兄弟目录>/x"` 即命中。
- 同一判据还有 7 个端点，写操作危害更大：`list_files:226`、`download_file:462`、`delete_file:490`、`make_dir:522`、`get_preview:549`、`rename_file:591,600`、`move_file:640-641`。
- 基线归属：`049c20d` 的 `_safe_join` 与 `:702-703` 与本端点**逐字节相同**（`git diff 049c20d..HEAD -- jflove-server/src/services/file_service.py` 中无 `_safe_join`/`get_stream_range` 相关 hunk）⇒ **非本版回归**，属 BL-10 未纳入的历史缺口。
- 记录误判（须一并纠正）：`plans/desktop-scratch-editor-v1.6.0.md` §17.8「不在面内的相邻点」写
  「`get_stream_range`（`file_service.py:652`）也拼 `filename`，但整体过了 `_safe_join` ⇒ **不可穿越**（只登记，未改）」——
  与上式实测不符（另注：该行号 `:652` 已过期，实际在 `:702-703`）。
- 建议：判定改为目录包含语义 —— `os.path.commonpath([str(base_path), str(target)]) == str(base_path)`，或 `target == base_path or str(target).startswith(str(base_path) + os.sep)`；改一处即同时收敛上列 8 个端点，并同步更正计划记录。

### [严重] BL-10 分层防御只在 upload 两个入口成立；其余入口（含 `filename`）无 `_validate_filename` 兜底
- 位置：`jflove-server/src/services/file_service.py:281-284`（`init_upload` 唯一调用点）与 `:373`（`complete_upload` 第二层 `_safe_join(dest_dir, filename)`）
- 事实：`_validate_filename` 全仓仅 `init_upload` 一处调用：
  ```
  > git grep -n "_validate_filename" HEAD -- jflove-server
  src/services/file_service.py:115:def _validate_filename(...)
  src/services/file_service.py:281:# 校验放在权限判定之后，与下方 `_safe_join(base, rel_path)` 的位置保持一致
  src/services/file_service.py:283:    rel_path = _validate_filename(...)      # 仅 upload/init
  tests/test_upload_filename_traversal_v1_6_0.py（用例侧引用）
  ```
  而 `get_stream_range:702`（`filename`）、`download_file:462` / `delete_file:490` / `make_dir:522` / `get_preview:549` / `rename_file:591,600` / `move_file:640-641`（`path`/`rel_path` 及 `new_name`）全部直接吃客户端字段，仅靠 `_safe_join` 单层判定。
- 后果：BL-10 的「第二层兜底」在这些路径上**不存在**；这些路径的实际安全边界 100% 取决于上面那条前缀判定（已确认可绕过），例如 `filename="../../<同前缀兄弟目录>/secret.txt"` 可下载/删除该文件。
- 违反：`AGENTS.md §9.3(10)`、`§9.7` 自查第一条；需求 v1.6.0 §4 BL-10 条「认证后目录穿越」的验收意图。
- 建议（择一）：① 修 `_safe_join`（推荐，一处兜全部）；② 或在 `get_stream_range`/`download`/`delete`/`preview`/`rename`/`move` 各自入口对 `filename`/`new_name` 复用 `_validate_filename`，并在设计中显式登记「表外相邻点」清单（当前仅在 `plans/…v1.6.0.md` §17.8 以错误结论登记）。

---

## 二、中等

### [中等] `_validate_filename` 判据不穷尽：Windows ADS 名放行 → 上传必然失败且目录残留 0 字节文件
- 位置：`jflove-server/src/services/file_service.py:136-148`，尤其 `:141`
  ```python
  if len(name) >= 2 and name[1] == ":" and name[0].isalpha():   # :141 只挡「单字母+冒号」盘符
  ```
- 证据（判据探针 + Windows 语义实测）：
  ```
  > python -c "... print(_validate_filename('ab:c.txt'), _validate_filename('_x:c.txt'), _validate_filename('a:b.txt'))"
  ab:c.txt  _x:c.txt            # 放行（2 字母以上前缀不算盘符）
  ValueError: filename 非法：不能包含盘符前缀     # 仅单字母被拦
  > python -c "... print(_validate_filename('evil.') , _validate_filename('CON'))"
  evil.  CON                    # 尾随点 / Windows 保留名亦放行
  ```
  ADS 落盘语义实测（Windows/NTFS，脚本见下）：
  ```
  listdir: ['ab']                             # 不存在名为 "ab:c.txt" 的普通文件
  isfile('ab:c.txt'): True  isfile('ab'): True # 落到基文件 ab 的备用数据流
  os.replace 失败: OSError [WinError 87] 参数错误: '...\.ab:c.txt.jflove-upload-1.part' -> '...\ab:c.txt'
  ```
  命令（`.part` 名按 `file_service.py:382-386` 的 `.{basename(dest_file)}.jflove-upload-<id>.part` 复现）：
  ```
  @'
  import os, tempfile
  d = tempfile.mkdtemp(); p = os.path.join(d, "ab:c.txt")
  open(p, "wb").write(b"x"); print(os.listdir(d), os.path.isfile(p))
  s = os.path.join(d, ".ab:c.txt.jflove-upload-1.part"); open(s, "wb").write(b"y")
  try: os.replace(s, p); print("ok", os.listdir(d))
  except Exception as e: print("os.replace 失败:", type(e).__name__, e)
  '@ | Set-Content "$env:TEMP\ads_probe.py"; jflove-server\venv-win\Scripts\python.exe "$env:TEMP\ads_probe.py"
  ```
- 后果链（服务端）：`init_upload` 放行 → `complete_upload` 写 `.part`（创建 0 字节基文件 `.ab`）→ `_replace_with_retry`（`file_service.py:79-87`，对**任意** `OSError` 重试 5×0.1s）5 次全败 → 抛 `OSError(WinError 87)` → 控制器无 `OSError` 分支（仅 `ValueError→400` / `PermissionError→403`）→ 全局兜底 **500**；`finally: _remove_quietly(tmp_file)` 只清掉 ADS，**基文件 `.ab` 留在用户磁盘目录**（违反 AC-B9③「失败后不残留无法识别的文件」的精神）。
- 违反：需求 v1.6.0 §4 BL-10 / §9.11 AC-B9③；`AGENTS.md §4.3`。
- 建议：`:141` 改为 `if len(name) >= 2 and name[1] == ":"` 直接拒（不限单字母）；补拒尾随 `.`（`"evil."` → Windows 落盘为 `evil`，返回的 `dest_file` 与实际不符）、Windows 保留名（`CON/PRN/AUX/NUL/COM1..9/LPT1..9`）与折叠后重名。

### [中等] 本版实际改动范围超出需求锁定的「§4 点名清单」（AC-B7 / AC-B1⑥ 与 CL-16 互相矛盾）
- 事实（`git diff --name-only 049c20d..HEAD -- jflove-web jflove-server`，21 文件）——**不在 §4 点名清单内**的有 9 个：
  `jflove-server/README.md`、`jflove-web/README.md`、`jflove-web/build.py`、`jflove-web/package-lock.json`、`jflove-web/src/config/constants.ts`、`jflove-web/src/index.css`、`jflove-web/src/styles/components.css`、`jflove-web/src/utils/mermaid/config.ts`、`jflove-web/src/utils/mermaid/renderer.ts`
  （§4 `:335-340` 点名清单 = `config_controller.py` / `http-client.ts` / `package.json` / **新建** `requirements-dev.txt` / `file_service.py` / 两份 `styles/tokens.css` 注释 / 文档 + `BACKLOG.md`；另有 6 个 `tests/*` 文件属 AC-R2/AC-B9 必需的回归载体，单列）。
- 抽样复核：上述表外文件的 diff **全部是注释/docstring 级**（`gate` 命令 `git diff 049c20d..HEAD -- <path> | Select-String '^[+-][^+-]'`），例如
  `jflove-web/src/config/constants.ts:18`（`APP_VERSION = '1.5.1'` 未动，仅改「实测」注释）、`src/index.css:69`、`src/styles/components.css:684`、`src/utils/mermaid/{config,renderer}.ts` 逐条把 `设计预览/…mjs` 死引用改写为「脚本已不在仓库」。**无功能变化。**
- `jflove-app/` 4 文件 +9 -9（`README.md`、`lib/config/design_tokens.dart:6`、`lib/pages/login/login_page.dart:12`、`test/mermaid_security_test.dart:120-123`）同样是**纯文档/注释**（版本号文案、路径指向更正），但直接违反 `文档记录/需求文档/v1.6.0.md:64`「**禁止改动** `jflove-app/`（一律不动）」与 `:734` AC-B7 / `:747` AC-R6「零改动」。
- 登记依据只在计划与台账层：`plans/desktop-scratch-editor-v1.6.0.md:551`（「全仓过时内容清查 + 清理批次 0~7 ｜ 65 文件 / 962 行」）、`:289`（「workflow 6 个面 → 85 条 / 44 个文件」）、`BACKLOG.md:206` `CL-16`、提交 `0f04ba2`。
  而需求文档 v1.6.0 全文 **0 命中** `CL-16` / `清查` / `BL-10`（`Select-String -Path 文档记录\需求文档\v1.6.0.md -Pattern 'CL-16|清查|BL-10'` 仅命中 `:335/:728/:768/:781` 的 **BL-7** 相关行），且
  `:728` AC-B1⑥ 明确「表外约 20 处同类失效引用**登记为 BL-7、不在本范围清理**（§11 U-1 未裁决）」、`:781` U-1 决议「**保留挂账**」，与 `BACKLOG.md:123` BL-7「**部分闭环（v1.6.0）**…源码与脚本范围死引用归零」**互斥**。
- 影响：AC-B7 字面未通过；但实体无功能风险（零功能改动、6 项 §4 点名工作全部落到点名文件）。属**范围/口径纪律**问题，不是回归。
- 建议：Phase 5→7 由 PMO 在需求文档补登记「CL-16 全仓过时内容清查」「CL-17/BL-10」两项范围扩展（或注明为已批准例外），并把 AC-B1⑥「保留挂账」与 BL-7「已清理」统一为一个口径；`jflove-app` 的 4 处零功能改动建议按已登记例外放行、不回退。

---

## 三、轻微

### [轻微] 原子化用例未覆盖的失败面（并发覆盖 / EXDEV / 进程被杀）
- 位置：`jflove-server/tests/test_upload_atomic_v1_6_0.py`
- 已覆盖（判据方向正确）：注入真实 —— `:136-146` monkeypatch `file_service.aiofiles.open` 于读第 2 分片时抛 `OSError("模拟分片读取失败（IO 异常 / 连接中断）")`；`:171-172` 删掉分片文件以穿透「缺分片」前置检查。断言 ①原文件字节 + sha256 不变（`:155-156`）②hash 不匹配时原文件仍在且内容不变（`:206-209`）③无 `.part` 残留（`:158/184/209/229`）④成功覆盖内容 + mtime 还原（`:243-254`）⑤替换确实走 `os.replace` 且临时文件与目标**同目录**（`:273-285`）⑥越权 403 且不破坏他人文件（`:299-302`）。
- **未**断言：两个会话并发覆盖同一 `dest_file`（无锁，实际语义 = 后写者胜）；`os.replace` 返回 `EXDEV`（同目录约束已由 `:280-284` 覆盖）；「进程被 kill」场景（设计上由同目录 `.part` + `:416-419 finally` 覆盖，但无用例）。
- 建议：补一条并发覆盖用例，或把「最后写入者胜」显式写进 BL-8 的已知行为。

### [轻微] `_replace_with_retry` 对**任意** `OSError` 重试 5 次，永久性错误白等 0.4s
- 位置：`jflove-server/src/services/file_service.py:79-87`（`except OSError as e:` → `time.sleep(0.1)` ×4）
- 文档意图是「Windows 目标被占用（`PermissionError`）重试即可」（`:68-70`），但 `except OSError` 把 `WinError 87` 这类永久错误也纳入重试（上面 ADS 用例即 5 次全败）。
- 建议：只对 `PermissionError` / `winerror in (32, 33)` 重试，其余立即抛出。

### [轻微] Web 覆盖率 21.1% 且不设门槛 —— 属用户已接受的风险
- 位置：`jflove-web/package.json:14`（`test:coverage`）、`:38`（`@vitest/coverage-v8`）；`文档记录/项目管理记录/BACKLOG.md` `CL-14`
- 事实：`npm run test:coverage` → **exit 0**，产出 `jflove-web/coverage/index.html`（`Test-Path` = True）；全量 21.1% 语句覆盖，**无 threshold**（D16 未授权改 `.github`，需求 AC-B4 亦只要求「退出码 0 + 产出报告」）。
- 遵纪律 5：**已知并接受**，兜底 = 脚本与产物真实存在、退出码可判。仅记录，不要求本版整改。

---

## 四、通过项（有判据、无缺陷，不计入缺陷数）

1. **BL-8 原子覆盖三条判据全部达标**（`jflove-server/src/services/file_service.py`）：
   - 不再先截断目标：`:377-388` 完成数据写入的是 `.part` 临时文件（`:382-386` 与目标同目录），`:415 await asyncio.to_thread(_replace_with_retry, tmp_file, dest_file)` 才原子替换；原实现 `open(dest_file,"wb")` 已不存在（`git diff 049c20d..HEAD -- jflove-server/src/services/file_service.py` 可见该 hunk）。
   - sha256 不匹配不再删既有文件：`:401-406` 只 `raise ValueError("文件校验失败，hash 不匹配")`，全文**不存在** `os.remove(dest_file)`（`git grep -n "os.remove" HEAD -- jflove-server/src/services/file_service.py` → 仅 `:56` `_remove_quietly` 内部与 `cancel_upload` 的 `rmtree`）。
   - 失败不残留半截：`:416-419 finally: _remove_quietly(tmp_file)`（`:54-58` 吞 `OSError`，幂等；成功路径下 tmp 已被 replace 移走）。
   - 负向对照方向可独立复现：旧实现「先截断再写」必然使 `test_upload_atomic_v1_6_0.py:155` 的 `after == before` 失败；计划 §17.8 记实测「还原 HEAD 版 → 33 failed, 6 passed」，与之一致。
2. **AC-R4① 路由清单与 v1.5.1 一致**：`git grep -h -e '@router\.' 049c20d/HEAD -- jflove-server/src/controllers | Sort-Object` 各 **47 条**，`Compare-Object` **输出为空**；本版服务端 `src/` 只改 2 文件（`config_controller.py` 文案、`file_service.py` BL-8/BL-10）⇒ 无新增接口/路径参数。
3. **AC-R4②③ 白名单与加密原语未动**：`git diff --name-only 049c20d..HEAD -- jflove-server/src/main.py jflove-server/src/utils/middleware.py jflove-server/src/utils/crypto.py` **为空**（`_PLAIN_PATHS` 未扩大）；服务端改动逐行无 session_key / 盐（`b"jflove-v1"`）/ 写死对称密钥新增。
4. **AC-B3 三例全绿（含桌面端零改动）**：`jflove-web/src/utils/http-client.ts:63-74`（`:64 if (!isEnvelope(body)) return body as T;` 明文直返；`:70-71 throw new ApiError(502, '响应解密失败（会话密钥不匹配或响应被篡改）')`，文案不含 `nonce`/`ciphertext`），两条调用路径同判：`:139-140`（fetch/POST）与 `:208-209`（GET URL-query 信封）；用例 `jflove-web/tests/utils/http-client.test.ts:272`（200+解不开 → 502）、`:288`（GET 同判定）、`:297`（200+明文 → 直返成功、不误伤白名单）。桌面端同源实现：`git diff --name-only 049c20d..HEAD -- jflove-desktop/src/utils/http_client.py jflove-desktop/src/services/http_client.py` **为空**。
5. **AC-B8 依赖落点正确**：`jflove-server/requirements-dev.txt:15 pytest-cov==7.1.0`；`Select-String jflove-server\requirements.txt -Pattern 'pytest-cov'` → **0 命中**（运行时清单干净）；`jflove-web/package.json:14/38` 具备 `test:coverage` 与 `@vitest/coverage-v8`。
6. **门禁脚本判据审查（纪律 7）——判据未受本版影响且反向验证齐全**：
   - `scripts/check_db_schema.py` 本版 **零改动**（`git diff --name-only 049c20d..HEAD -- scripts/check_db_schema.py` 空）；本版 dev 范围改动为 `scripts/{build_mermaid_bundle.mjs,check_web_desktop_parity.py,sync_version.py}`。
   - 本版**无表结构变更**：`Get-ChildItem jflove-db -Filter '*1.6.0*'` → 命中 0（无 migrate/rollback）⇒ `expected_schema()` 的「代码 → dev」比对对象未变。
   - `jflove-server/tests/test_expected_schema_v1_5_0.py` 唯一改动是注释引用更正（`设计预览/verify_schema_guard.py` → `scripts/verify_check_db_schema.py`），**判据零变化**（`git diff` 只此一行）。
   - 反向验证实跑：`jflove-server\venv-win\Scripts\python.exe scripts\verify_check_db_schema.py` → **20 passed / 0 failed, exit=0**（坏数据被拦：缺表/缺列/缺索引/表 DDL 不一致各自 exit 1；纯格式差异放行；`extra_table` 只报告不删除且表仍在）。
7. **AC-R2 服务端部分全绿**：`jflove-server\venv-win\Scripts\python.exe -m flake8 src\ tests\ --max-line-length=99` → **exit 0**；`... -m pytest tests\ -q` → **219 passed**（基线 180，+39）；Web `npm run test` → **20 files / 211 tests passed**（本面实测）。

---

## 五、AC 判定表（本面负责项）

| AC / 条目 | 判定 | 一句判据 |
| --- | --- | --- |
| **AC-B2**（BL-2 接口描述） | 通过 | `git diff 049c20d..HEAD -- jflove-server/src/controllers/config_controller.py` 的全部增删行都在多行 `description` 字符串内（无签名/逻辑行），`:335-340` 描述的「键值对映射」与 `config_service.get_all` 一致，回归 219 passed |
| **AC-B3**（BL-3 解密失败语义） | 通过 | `http-client.ts:70-71` 抛 `ApiError(502,'响应解密失败（…）')`、`:64` 明文直返；`http-client.test.ts:272/288/297` 三例覆盖「解不开/明文」两向；桌面端 `http_client.py` 零改动（diff 为空） |
| **AC-B4**（Web 覆盖率脚本 + 未设 CI 门槛） | 通过 | `npm run test:coverage` exit 0 且 `jflove-web/coverage/index.html` 存在；`git diff --name-only 049c20d..HEAD -- .github` **为空** |
| **AC-B7**（变更集中 / app 与 .github 零改动） | **未通过** | `.github` 零改动 ✓；但 `jflove-app/` 4 文件 +9 -9（字面违反「零改动」），且 `jflove-web`/`jflove-server` 21 文件中有 9 个不在 §4 点名清单内 —— 全部为文档/注释级、零功能变化，登记依据在 `plans/…v1.6.0.md:551` + `BACKLOG CL-16`，需求文档未登记（详见中等第 2 条） |
| **AC-B8**（服务端依赖落点 + 报告产出） | 通过 | `pytest-cov==7.1.0` 仅 `requirements-dev.txt:15`，`requirements.txt` 0 命中；`pytest --cov` 等价命令由开发记录判据 180 passed / 73%（BACKLOG `CL-14`）；`.github` 零改动 |
| **AC-B9**（BL-8 破坏性写入） | 通过 | 代码逐条达标 + `test_upload_atomic_v1_6_0.py` 注入点真实、①②③④⑤⑥ 断言齐备；负向对照方向由代码结构可证（旧实现必使 `:155` 失败，计划 §17.8 实测 33 failed / 6 passed）。⑤三端回归：服务端 219、Web 211 本面实测，桌面 415（`_scope §5`），移动端非本面未跑 |
| **AC-R2**（服务端部分） | 通过 | flake8 exit 0 + pytest 219 passed（含 BL-8/BL-10 改动） |
| **AC-R4①**（路由清单与 v1.5.1 一致） | 通过 | 47/47 条路由装饰器逐一相同（`Compare-Object` 空），`src/` 仅 2 文件且均未动路由 |
| **AC-R4②**（明文白名单未扩大） | 通过 | `main.py` / `src/utils/middleware.py` 本版零改动（diff 为空）⇒ `_PLAIN_PATHS` 未变 |
| **AC-R4③**（无新增长期密钥/静态盐/写死密钥） | 通过 | 服务端改动仅 2 文件，逐行无密钥/盐/新原语新增 |
| **BL-10**（用户新增裁决：filename 路径穿越） | **部分未通过** | 判据穷尽性缺口（ADS 名放行 → 500 + 0 字节残留，中等）；分层防御只覆盖 `init_upload`/`complete_upload`，其余 6 个端点仍吃客户端 `filename`/`path`（严重）；其唯一安全边界 `_safe_join` 的前缀判定可绕（严重） |

---

## 六、无法验证 / 未验证项

| 项 | 状态 | 原因与需要什么条件 |
| --- | --- | --- |
| AC-B9⑤ 移动端（`flutter test`）上传/目录同步回归 | 未验证（非本面） | 本面只覆盖服务端 + Web；移动端由移动端面/测试阶段负责 |
| 真实浏览器/桌面端 GUI 交互路径 | 无法验证（无头） | 需真机/真浏览器（`_scope §3` 已记）；本面用单测 + 直读代码替代 |
| `pytest --cov` 服务端 180 passed / 73% | 未复跑（引用方判据） | 本面未改依赖安装（`requirements-dev.txt` 新装会影响 venv），按纪律 2 记为**待验证输入**，来源 `BACKLOG CL-14` 与 `文档记录/后端开发记录/v1.6.0.md` |
| 跨磁盘根穿越的可利用性（需实盘存在同前缀兄弟目录） | 逻辑已实测、场景未实测 | `_safe_join` 已用探针证明放行 `D:\disks\alice2\...`；端到端利用需在真实 `JFLove-disk` 根下建两个同前缀目录后跑 `/files/stream`（本面未改动任何数据/配置，未做该实验） |
