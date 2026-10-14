# A3 · 打包资产 + 派生链脚本 + 构建接线（v1.6.0）

- 被审版本：v1.6.0；基线 `049c20d`；端点 `HEAD`（`c515853`）
- 审查面：`jflove-desktop/scripts/gen_file_assoc_manifest.mjs`、`jflove-desktop/scripts/gen_file_assoc_assets.py`、
  `jflove-desktop/scripts/build-codemirror-bundle.mjs`（+ `scripts/editor-bundle/`）、`jflove-desktop/build.py`（接线）、
  `jflove-desktop/packaging/windows/{jflove.iss,file-assoc.iss}`、`jflove-desktop/packaging/linux/*`、
  `jflove-desktop/ui/public/file-assoc.manifest.json`、`jflove-desktop/tests/test_gen_file_assoc_assets.py`
- 负责验收标准：AC-43、AC-48a、AC-44、AC-B6、AC-B7
- 纪律依据：`.claude/skills/code-review/SKILL.md` 纪律 7（门禁脚本要审判据）、纪律 8（以 X 为准确认 X 可信）、
  `AGENTS.md` §8（桌面端交付形态 = 安装包）
- 取证环境：所有破坏性验证一律在 `$env:TEMP\jf-audit\` 下的临时树/临时副本上进行，**未修改仓库内任何源码、资产或打包脚本**；
  每轮实验后 `git status --porcelain` 复核，仓库侧零改动（仅本报告文件与任务开始前既有的未跟踪项）。

---

## 结论摘要

| 级别 | 条数 |
| --- | --- |
| 严重 | 0 |
| 中等 | 1 |
| 轻微 | 7 |

单一真相机制在本面**成立且可反向验证**：AC-48a / AC-44 / AC-B6 / AC-B7 全部通过；AC-43 通过但带一处卸载残留条件。
唯一「中等」是门禁本身的降级路径（缺 node 时静默跳过派生核对），其余为轻微加固项。

---

## 发现

### A3-1 中等 · `--check` 的派生核对在缺 `node` 时降级为「告警 + 返回通过」

- 文件：`jflove-desktop/scripts/gen_file_assoc_assets.py:474-478`
  ```python
  node = shutil.which("node") or shutil.which("node.exe")
  if node is None:
      print("[gen-file-assoc-assets] [warn] 未找到 node："
            "跳过 manifest ↔ scratch-types.ts 核对（只核对三处资产）", file=sys.stderr)
      return []
  ```
- 判据（纪律 7）：`--check` 声明做两件事（`:28`），但第①件（`manifest ↔ 唯一手写真相`）在没有 node 时**返回空失败列表**，
  调用点 `:412-413` 把它并入 `failures`，于是 `main()` 以 **0** 退出并打印「一致：manifest（34 条）+ 三处生成物」。
- 实测复现（`$env:TEMP\jf-audit`，把 `PATH` 收窄到 `C:\Windows\System32;C:\Windows` 使 node 不可见）：
  ```
  [gen-file-assoc-assets] [warn] 未找到 node：跳过 manifest ↔ scratch-types.ts 核对（只核对三处资产）
  exit=0
  ```
  → 「一致性核对通过」这句话在缺 node 的机器上**不成立**，与仓库自身的门禁规范（AGENTS.md §5.1.1
  「喂坏数据要拦住」）相悖。
- 实际危害：三处打包资产仍会与 `manifest` 逐字节比对，且 `ensure_file_assoc_assets()`（`jflove-desktop/build.py:395-442`）
  每轮构建都无条件重跑派生，故**发布产物不会带着漂移出厂**；影响面是「门禁结论不可信」而非「产物错误」。
- 建议：`--check` 下 node 缺失应计入 `failures`（或提供显式的 `--allow-no-node` 逃生开关），不要默认放行。

### A3-2 轻微 · 构建期从不在 `--check` 上设闸，只做「写盘自愈」

- 文件：`jflove-desktop/build.py:428-431`
  ```python
  steps = (
      [node, str(manifest_script)],
      [sys.executable, str(assets_script), "--write"],
  )
  ```
- 证据：`jflove-desktop/build.py:413` 的 docstring 自述「一致性判据是 `--check`（退出码 0 = 一致），由测试/评审守门」；
  但 `build.py` 全文件**没有任何** `--check` 调用，根 `build.py` 也没有；
  `.github/workflows/build-desktop.yml:80` 只跑 `python build.py --mode onedir --installer --zip`。
  实测真实树两处门禁均通过：`gen_file_assoc_assets.py --check` → exit 0；`gen_file_assoc_manifest.mjs --check` → exit 0。
- 后果：生成物漂移在流水线上**无声无息**（`--write` 顺手修好），只在 `git status` 留下脏文件；
  若同时命中 A3-1（缺 node），漂移的 `manifest` 会被原样打进 Linux 包。建议打包前加一道
  `gen_file_assoc_assets.py --check`（或 `--write` 后断言 `git diff --quiet`）。

### A3-3 轻微 · `file-assoc.iss` 的 `AppExeName` 护栏是大小写敏感的字符串比较，可产出「打开方式」死链

- 文件：`jflove-desktop/packaging/windows/file-assoc.iss:32-38`
  ```
  #define FILE_ASSOC_EXE "JFLove.exe"
  #define FILE_ASSOC_APP_KEY "Software\Classes\Applications\JFLove.exe"
  #if AppExeName != FILE_ASSOC_EXE
    #error jflove.iss 的 AppExeName 与文件关联注册片段不一致（重跑 gen_file_assoc_assets.py）
  #endif
  ```
- 护栏本身**有效**，两条反向/正向验证均自行复跑（Inno Setup 6 已装：`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`）：
  - `/DAppExeName=JFLove-new.exe` → `Error on line 37 in ...\file-assoc.iss: jflove.iss 的 AppExeName 与文件关联注册片段不一致`，**exit 2**（开发记录声称的 exit 2 属实）。
  - 对照组 `/DAppExeName=JFLove.exe` → 真实编译成功，`exit 0`，产出 `jf-guard-ok.exe`（179,856,997 字节）。
- 缺口：`FILE_ASSOC_EXE` / `FILE_ASSOC_APP_KEY` 是生成期写进片段里的**字面量**（`gen_file_assoc_assets.py:303-305` 附近），
  不是从真实 exe 名派生。若构建以 `/DAppExeName=JFLove.EXE`（或任何大小写/命名变体）通过，护栏不再触发，
  装出一个「Applications\JFLove.exe 指向不存在的 exe」的注册项 —— 「打开方式」列表可见但点了无效。
  本机 `build.py` 的 `APP_NAME` 与 `OUTPUT_EXE` 一致时不会踩到，属条件性缺陷。

### A3-4 轻微 · `editor.version.json` 的 `sha256` / `bytes` 从不被校验，也不进 git

- 生成端：`jflove-desktop/scripts/build-codemirror-bundle.mjs:362` `const sha256 = createHash('sha256').update(bundle).digest('hex');`，
  写入 `:372` 的 manifest。
- 消费端：`jflove-desktop/ui/src/utils/scratch/load-editor-bundle.ts:21,28` `import versionJson from '../../../public/vendor/editor.version.json';`
  并声明 `{ codemirror: string; merge: string; sha256: string; bytes: number }`，但全文件**只消费 `MANIFEST.codemirror`**（`:31` 派生 `EXPECTED_CODEMIRROR_VERSION`），
  `sha256` / `bytes` 是死字段。
- 跟踪状态：`git ls-files jflove-desktop/ui/public/vendor/` 为空（`.gitignore:86` = `jflove-desktop/ui/public/vendor/`）；
  只有 `scripts/editor-bundle/package.json` 与 `package-lock.json` 入库。
- 直接后果：`jflove-desktop/build.py:354-357` 的早返回只看**存在性**：
  ```python
  required = ("editor.bundle.js", "editor.version.json")
  if all((vendor_dir / name).exists() for name in required):
      return
  ```
  → 一份从旧版本 Release 资产下载、或半途中断留下的 `vendor/` 目录（含 `version.json` 但 js 与之不匹配）
  会被静默采信并打进安装包；`sha256` 字段本可拦住它，却从未被任何一方读取。
- 实测：现值**确实一致**（`Get-FileHash ui/public/vendor/editor.bundle.js` =
  `2CE113D35A2D0661939F07B385698AD8125E45AAB055D2E9EAE7999CD2025131`，与 `editor.version.json` 的 `sha256` 相等；
  `bytes` 1438477 与文件长度相等）。
- 建议：`ensure_editor_bundle()` 的早返回加一条 sha256/bytes 比对，不一致则重新生成；或在 `load-editor-bundle.ts` 里真正用上。

### A3-5 轻微 · `--check` 只覆盖「标记块 + 整文件」，`jflove.desktop` 标记块以外的手改不会被审判据

- 文件：`jflove-desktop/scripts/gen_file_assoc_assets.py:214-232`（`render_desktop`）—— 只重写
  `# >>> jflove-file-assoc (generated by ...) >>>` 与 `# <<< jflove-file-assoc <<<` 之间的段；
  `:435-444` 的比对是**目标整文件文本 vs 期望整文件文本**的等值比较，双向都管（目标多出来的内容会作为 diff 报出，不会被顺手删）。
- 覆盖矩阵（实测 `$env:TEMP\jf-audit\checkdrive`，空树、只放 `manifest` + `scratch-types.ts`）：
  - `jflove.xml` 缺失 → `- 缺少生成物：...\packaging\linux\jflove.xml（请运行 ...）`，exit 1 ✅
  - `file-assoc.iss` 缺失 → 同样报「缺少生成物」，exit 1 ✅
  - `manifest` 比 TS 多一条 `.zzz` → `- manifest 与 scratch-types.ts 的派生结果不一致：...` + 完整 unified diff，exit 1 ✅
  - `jflove.desktop` 的 `MimeType=` 行被手改（在标记块内）→ 落入 `:442` 的不等值分支，报告 + diff ✅
  - **未覆盖**：标记块之外的正文（如 `Categories=`、`Comment=`）手改 —— 按设计如此（该文件还有非生成内容），
    但意味着 `.desktop` 只有那一行受门禁保护。可接受，仅登记。

### A3-6 轻微 · 卸载清理依赖**当前版本**的扩展名清单，跨版本残留（AC-43 ③ 的条件缺口）

- 写入端（只此三处子键 + 一个整键）：
  - `jflove-desktop/packaging/windows/file-assoc.iss:41-43` → `HKCU\Software\Classes\Applications\JFLove.exe` 默认值 / `DefaultIcon` / `shell\open\command`
  - `jflove-desktop/packaging/windows/file-assoc.iss:46-79` → `...\SupportedTypes` 下逐扩展名空值（34 条）
- 卸载端：`jflove-desktop/packaging/windows/jflove.iss:161-185`
  ```pascal
  Exts := '{#FILE_ASSOC_EXTS}';            { :167 }
  ...
  RegDeleteKeyIncludingSubkeys(HKCU, '{#FILE_ASSOC_APP_KEY}\SupportedTypes\' + Ext);   { :182 }
  RegDeleteKeyIncludingSubkeys(HKCU, '{#FILE_ASSOC_APP_KEY}');                          { :184 }
  ```
  调用点 `:193-199`（`usUninstall`，早于文件删除；`:158-160` 说明为何不用 `usPostUninstall`）。
- 缺口：`{#FILE_ASSOC_EXTS}` 是**装配期**从当时 manifest 派生的清单（`file-assoc.iss:28`）。
  若用户先装 v1.6.0（34 个扩展名），再升级到某个把扩展名变少的版本（例如 TYPES 删掉 `.patch`），
  用新版卸载器卸载时循环里没有 `.patch` ⇒ 该值名残留；又因为 `:184` 删掉了父键，残留值挂在**孤立子键**上，任何版本都再也看不见、清不掉。
  Windows 不因「应用键不存在」而清理 `SupportedTypes` 子键，故无法靠后续卸载补救。
- 证明判据（本机未能端到端验证，见「无法验证的项」）：`HKCU\Software\Classes\Applications\JFLove.exe\SupportedTypes` 删除后仍存在。
- 建议：卸载循环改为**枚举** `SupportedTypes` 现有值名（Inno `RegQueryStringValue` 遍历不支持，可在
  `RemoveFileAssocKeys` 里先 `RegDeleteKeyIncludingSubkeys(HKCU, '...\SupportedTypes')` 删整节），或在注册项里留一份「历史扩展名并集」。

### A3-7 轻微 · 单一真相在 Python 侧存在两份小型手写映射

- `jflove-desktop/src/services/text_detect_service.py:87-88`
  ```python
  "json": "json",
  "jsonc": "json",
  ```
  与 `jflove-desktop/ui/src/config/scratch-types.ts:179` `exts: ['json', 'jsonc'],`
- 两处都是「扩展名 → 语言」的小映射，与 `TYPES` 里 `json/jsonc→language:json` 的取值重复。
  `gen_file_assoc_manifest.mjs` 的头部声明（`:30-31`）「任何 Python 文件 / `.iss` / `.desktop` / `.xml` **都不得**手打扩展名清单」，
  这两处严格说不是「清单」（不含 MIME/标签/顺序），但一旦 TYPES 里改掉 json 的 `language`，三处会静默分叉。
- 建议：从 manifest 派生，或加一条「交叉校验」用例把这两处与 TYPES 对齐。

---

## AC 判定表

| AC | 要求（要点） | 判定 | 证据 |
| --- | --- | --- | --- |
| **AC-48a** | 扩展名清单**只**在 `ui/src/config/scratch-types.ts` 定义一处；关联清单与类型表单一真相 | **通过** | 全仓检索扩展名序列字面量，唯一手写源 = `jflove-desktop/ui/src/config/scratch-types.ts:420,440`（`EXTENSIONS`/`ASSOC_EXTENSIONS` 均为 `TYPES.map`）；`gen_file_assoc_manifest.mjs` 以文本切片 + `JSON.parse` **解析**（`DEFAULT_SRC` = `:41`，非执行 TS）；反向实测「TYPES 加 `.zzz` → 派生 35 条（基线 34）」；真实树 `node scripts/gen_file_assoc_manifest.mjs --check` → exit 0 |
| **AC-44** | 清单来源 = `TYPES`，其余均为生成物 | **通过** | `ui/public/file-assoc.manifest.json` 34 条含 `"generatedFrom": "config/scratch-types.ts"`；`packaging/windows/file-assoc.iss:1-6`、`packaging/linux/jflove.xml:2-4`、`packaging/linux/jflove.desktop:12-14` 均带「生成物请勿手改」标记；`tests/test_gen_file_assoc_assets.py:124,135,174-179` 覆盖 |
| **AC-43** | 只写 `HKCU\...\Applications\JFLove.exe`（含 `SupportedTypes` 空值）；不写 `.ext` 类键 / `UserChoice` / `ChangesAssociations`；卸载删净 | **通过（带 A3-6 条件缺口）** | `file-assoc.iss` 共 37 条 `Root: HKCU`，全部落在 `Applications\JFLove.exe` 树内；`grep 'HKLM|UserChoice|ChangesAssociations|Classes\\\.'` 仅命中 `:20-22,24` 的**注释**，无代码写入；`jflove.iss` 无 `ChangesAssociations`；卸载 `:161-185` 按 `{#FILE_ASSOC_EXTS}` 逐项 `RegDeleteKeyIncludingSubkeys` 后删整键；`AppExeName` 护栏 `file-assoc.iss:36-38` 实测拦下不匹配（iscc **exit 2**）且对照组编译成功（exit 0） |
| **AC-B6** | 清债提交不得与打包资产混在同一提交 | **通过** | `git log -1 0f04ba2` = `chore(cleanup): 全仓过时内容清查（批次 0~7）`，`git show --stat 0f04ba2` 仅命中 skills/文档/mermaid/sync_version/parity 等清债文件，**无** `packaging/`、`scripts/gen_file_assoc_*`、`scripts/build-codemirror-bundle.mjs`；打包资产分别落在独立的 `187a31b`（CM6 分包）与 `bbb49be`（文件关联派生链 + Linux 打包） |
| **AC-B7** | Linux 打包资产齐备 | **通过** | `packaging/linux/jflove.desktop:13` 有 `MimeType=`（登记 21 类，含末位 `application/x-jflove-text`）；`jflove-desktop.spec` 三处齐：`:69 %install` → `:86` 装 `.desktop`、`:100` 装 `jflove.xml`；`:107 %files` → `:110-113` 双登记；`:119 %post` 与 `:128 %postun` 均含 `update-mime-database %{_datadir}/mime &> /dev/null || :`（`:124`、`:130`，`|| :` 保证命令缺失不致失败）；`build_rpm.sh:202 cp .../jflove.desktop`、`:207 cp .../jflove.xml`（`:204` 注明不拷则 `%install` 直接失败） |

### 派生链 / 构建接线附加核对（任务重点方向 4、5）

| 项 | 结论 | 证据 |
| --- | --- | --- |
| `ensure_file_assoc_assets()` 在 `npm ci + build` **之前** | ✅ | `jflove-desktop/build.py:467` 早于 `:469-471`（`build_frontend` 内），注释 `:465-466` 说明否则 Vite 不会把它复制进 `dist/` |
| manifest 真的进包 | ✅ | `jflove-desktop/ui/dist/file-assoc.manifest.json`（3116 字节）存在 ⇒ Vite 已把 `ui/public/` 拷进 `dist/`；`resolve_web_root()` 冻结态返回 `_MEIPASS/webui` |
| 生成是否幂等（跑两次字节一致） | ✅ | 临时树 `$env:TEMP\jf-audit\idem` 两轮 `--write` 后四个文件 SHA256 逐一相同；`jflove.desktop` 第二轮报「已是最新」 |
| 失败是否真 `fail()`（不吞） | ✅ | `build.py:365-374`（缺 node/npm/脚本）、`:382-384`（生成返回码非 0）、`:386-391`（生成后仍缺文件）、`:416-420`/`:424-426`/`:435-437`/`:440-441` 全部走 `fail()` |
| 生成是否会改动工作区 | ✅ | `--write` 落点即入库生成物本身；本机跑完 `git status --porcelain` 无非预期条目（`ui/public/vendor/` 走 `.gitignore:86`；`scripts/editor-bundle/node_modules` 走 `.gitignore` 的 `node_modules/`） |
| CM6 产物形态 | ✅ | `ui/public/vendor/editor.bundle.js` 1438477 字节，单文件 IIFE（文件头 `(()=>{`），无动态 `import(`、无 `importScripts`，含挂载点 `window.__JFLOVE_CM__`；`build-codemirror-bundle.mjs:323`（IIFE 硬约束）、`:326`（挂载点缺失即 fail）、`:344-349`（伪 window 求值 + api 键完整性）为构建期自检 |
| `editor.version.json` ↔ 产物 sha256 | ✅ | 见 A3-4 实测哈希一致（但**无人校验**，已单列） |
| `scripts/editor-bundle/package.json` 是否被误写进 `ui/package.json` | ✅ 未发生 | `ui/package.json` 中 `codemirror` 出现 0 次；CM6 依赖只在 `scripts/editor-bundle/package.json`（24 个精确钉死版本，无 `^`/`~`），与 `package-lock.json` 逐条一致（lockfileVersion 3，24/24 匹配，零版本错配） |
| 测试执行 | ✅ | `venv-win\Scripts\python.exe -m pytest tests/test_gen_file_assoc_assets.py tests/test_file_assoc_service_v1_6_0.py tests/test_text_detect_service_v1_6_0.py -q` → **72 passed in 1.94s**（无失败） |

### 纪律 7 反向验证（自己复跑，不采信开发记录）

| 反向用例 | 结果 |
| --- | --- |
| TYPES 加一条 `{ "ext": "zzz", ... }` → 派生清单 | 34 → **35**，顺序正确（`zzz` 落在 manifest 首位）✅ |
| TYPES 内注入 `// 注释`（`:39` 处） | node **exit 1**，`TYPES 字面量不是合法 JSON：Expected double-quoted property name in JSON at position 39`，**不产出**清单文件 ✅ |
| TYPES 数组尾加逗号 | node **exit 1**，`Unexpected token ']'`，不产出文件 ✅ |
| 把 `Array<TypeEntry>` 改成 `TypeEntry[]` | node **exit 1**（首个 `[` 落在注解的 `[]` 上）—— 该注解形态是硬约束，已在 `scratch-types.ts` 头部登记 ✅ |
| `manifest` 比 TS 多一条 `.zzz` | `--check` → exit 1，打印 `manifest 与 scratch-types.ts 的派生结果不一致` + 完整 unified diff ✅ |
| 生成物缺失（xml / iss） | `--check` → exit 1，逐项 `缺少生成物：...` ✅ |
| 缺 node（PATH 收窄） | `--check` → **exit 0**，仅 stderr `[warn] ... 跳过 manifest ↔ scratch-types.ts 核对` ❌ → **即 A3-1** |

### 非阻断观察

- `gen_file_assoc_manifest.mjs` 对 `label` 字段零校验（只查 `ext` 正则/重复与 `mime` 非空字符串数组）。
  实测在合法 JSON 中注入 `"label": "JO\u0000SON"`：脚本 **exit 0** 且把原始 NUL 字节写进 manifest
  （`manifest label bytes = b'JO\x00SON'`）。该值直通设置页与 Explorer「打开方式」文案，建议加
  「非空 + 无控制字符」校验（轻微，未单列编号）。
- `file-assoc.manifest.json` 保留 `language` 字段，而 `gen_file_assoc_assets.py:140` 附近只保留
  `ext/label/mime` 三键 —— 双方都不校验 `language` 与 `TYPES` 是否一致（与 A3-7 同源）。
- `file-assoc.iss:30` 的 `#define FILE_ASSOC_COUNT 34` 与其注释「装配期自检用」不符：
  全仓没有任何 `#if FILE_ASSOC_COUNT != ...` 之类的编译期自检，该 define 目前是装饰品（轻微，未单列编号）。

---

## 无法验证的项

1. **RPM 端到端（AC-B7 的运行时面）**：本机无 `rpmbuild`、无 `docker`
   （`Get-Command rpmbuild` / `docker` 均为空），无法执行 `build_rpm.sh` 产出 `.rpm`，
   也无法在 Linux 上跑 `update-mime-database` 验证 `jflove.xml` 的 34 条 glob 与 `.desktop` 的 MimeType 行真的被登记。
   本次仅完成**静态接线核对**（`%install` / `%files` / `%post` / `%postun` 四处 + `build_rpm.sh` 的两处 `cp`）。
2. **AC-43 ③ 的实机注册表验证**：Windows 上 iscc 可用且已完成编译期护栏的正/反两组复跑（`exit 2` / `exit 0`），
   但**未执行安装-卸载**，故 `RemoveFileAssocKeys()` 的实际删除结果（尤其 A3-6 的孤立子键）只能推理不能实证；
   `RegDeleteKeyIncludingSubkeys(HKCU, '...\SupportedTypes')` 对「只有值名、没有子键」的键是否连值一起删，
   需在真机（或一次真实安装+卸载）确认。
3. **`ensure_editor_bundle()` 的就地生成全流程**：本机 `ui/public/vendor/` 两个文件已存在（`:356-357` 早返回），
   为避免污染工作区未重跑 `npm ci` + esbuild（约 1–2 分钟 + 写 `scripts/editor-bundle/node_modules`）。
   已验证的是：路径拼接、早返回判据、`fail()` 分支的完整性，以及生成器「跑两次字节一致」的幂等性——
   **未**端到端复现「删除 vendor → 构建自动补回」。
4. **CI 上的真实行为**：`build-desktop.yml` / `ci-guards.yml` 均未在运行时触发过，
   无法确认 node 版本（CI `node-version: '22'`）下 `gen_file_assoc_manifest.mjs` 的 JSON 报错文本与本地 node 24 完全一致。
