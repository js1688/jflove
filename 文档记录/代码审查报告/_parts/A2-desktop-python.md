# A2 · 桌面端 Python 原生侧（服务层与启动/退出时序）

> 审查面：`jflove-desktop/src` 的**服务层与启动/退出时序**（`services/{text_detect,editor,scratch,file_assoc}_service.py`、
> `utils/single_instance.py`、`config/settings.py`、`main.py`、`ui/tray.py`、`tests/` 下 9 个新测试文件）。
> 被审版本 **v1.6.0**；基线 `049c20d`；端点 `HEAD`（`c515853`）。格式遵循 `_parts/_scope.md` §4。
> **纪律**：本文件只输出意见，审查期间未修改任何源代码（`git status --porcelain` 全程为空）。
> 本面计数：**严重 1 · 中等 4 · 轻微 4**。

---

## 1. 发现

### [严重] 草稿索引损坏时，写路径把「空索引」当成真值整体回写 → 其它窗格的可还原草稿被静默丢弃（无提示）

- 位置：`jflove-desktop/src/services/scratch_service.py:480`（同类写法还有 `:512`、`:555`、`:570`）；配合 `:387-399`（损坏回落空索引）、`:523-541`（`list_drafts` 只读索引、不扫目录）
- 违反：需求 v1.6.0 **§9.10 AC-S2 ②**（"最多丢失最后一次自动暂存窗口内的内容（此前已写入的窗格内容**可还原**）"）；`_scope.md` §2 纪律 4（静默失败优先于显式报错）
- 证据（真实实现，`src.config.settings.SCRATCH_DIR` 指向临时目录，`probe` 脚本见 §2 末）：

  ```text
  index before: ['p1a', 'p2b']
  list_drafts:  ['p1a', 'p2b']
  index.json 被破坏为半截 JSON，且 .tmp 已清空
  save_draft(p3c) -> {'ok': True, 'at': 1791973007.0772336}
  index after : ['p3c']
  scratch files: ['.tmp', 'index.json', 'pane-p1a.txt', 'pane-p2b.txt', 'pane-p3c.txt']
  list_drafts after: ['p3c']
  ```

  机制：`_load_index()` 在 `index.json` 存在但解析失败、且 `.tmp` 无可用 `index.json.*.tmp` 副本时返回
  `_empty_index(clean_exit=True)` + 一条面向用户的 warning（`scratch_service.py:393-399`）；而 `save_draft()` 第 480 行写作
  `index, _warnings = _load_index(scratch, validate_content=False)` —— **warnings 被丢进 `_warnings` 不再出现**，
  随后 `:481-487` 拿这个空索引做 upsert 并 `:487` 整体替换 `index.json`。
  `pane-p1a.txt` / `pane-p2b.txt` 的字节仍在盘上，但 `list_drafts()`（`:523-541`）只读索引、**不扫描目录**，
  于是它们从"可还原集"里消失 ⇒ **AC-55 / AC-60 的"全部自动还原"不再成立**，且用户看不到任何提示；
  此后 AC-61 的「清空草稿」会（按设计）把这些孤儿内容文件一并删除，草稿实际不可恢复。
  `:568-573 mark_clean_exit()` 同病：它也会用空索引覆盖 `index.json`，把损坏索引的最后残留一并抹掉。

  **触发条件**（不苛刻）：`index.json` 存在但不可解析**或不可读**（`:385 _read_json_file` 对 `OSError` 也返回 `None`
  —— Windows 上杀毒软件/共享冲突导致的瞬时读取失败即落此路径），且 `.tmp/` 里没有可用的 `index.json.*.tmp`。
  后者在一次"索引已损坏过一次"的启动之后就恒成立（`clear_all_drafts` / `_read_latest_tmp_index` 之外无人补写副本）。

  **测试为何没拦住**：`tests/test_scratch_service_v1_6_0.py::TestLoadTolerance::test_索引损坏时保存仍成功`
  只断言"保存仍然成功"，没有断言「**其它**窗格条目仍在索引里」；`test_索引损坏_退回tmp最新副本` 走的是有副本的分支。
- 建议：写路径（`save_draft` / `discard_draft` / `mark_clean_exit`）在 `_load_index` 报告"索引损坏"时**不得**用空索引整体回写：
  ① 最小修法是把 `_warnings` 透出到返回值并让桥层提示用户（至少不再静默）；
  ② 更稳的修法是先扫目录用 `pane-*.txt` 重建最小索引（`clear_all_drafts` 的 `glob` 已证明可扫）；
  ③ 或直接拒绝写入并返回 `ok=False`，把损坏状态交给用户决定。

---

### [中等] 目标文件只读时，失败路径**残留**含正文的临时文件（AC-41 ④ 明确判据，实测未通过）

- 位置：`jflove-desktop/src/services/editor_service.py:306-310`（`shutil.copymode` 把目标权限位**复制到临时文件**）→ `:311` 替换失败 → `:312-314 _silent_unlink` → `:336-341`（`_silent_unlink` 吞掉 `OSError`）
- 违反：需求 v1.6.0 **§9.5 AC-41 ④**（"目录中**不残留**半截临时文件 `[负向对照]`"，且 ① 点名的场景正是"文件设为只读"）
- 证据（真实实现，Windows `attrib +R`）：

  ```text
  target attrs: 0o100444
  raised EditorError: ro.txt 保存失败：PermissionError
  leftover: ['.ro.txt.jflove-i1z3zasx.tmp']
    .ro.txt.jflove-i1z3zasx.tmp attrs=0o100444 read-only=True
    os.unlink FAILED: [WinError 5] 拒绝访问。: 'C:\...\a2ro-6keqxkg4\.ro.txt.jflove-i1z3zasx.tmp'
  --- 对照：目标非只读时注入 replace 失败 ---
  leftover after injected replace failure: []   （只有上面那个只读残留仍在）
  ```

  因果链：`copymode(只读目标, tmp)` 把 0o444 复制给临时文件 → `os.replace(tmp, path)` 对只读目标必然 `PermissionError`
  （重试 3 次后抛）→ 异常路径 `_silent_unlink(tmp)` 里的 `os.unlink` 也因**临时文件自身只读**而 `WinError 5`，
  被 `:340-341` 的 `except OSError: pass` 吞掉 ⇒ 临时文件（内含本次要写入的**新正文**）永久留在用户文件同目录。
  这与"目标非只读、注入 replace 失败"的对照形成反差，说明问题**专由 `copymode` 引入**。

  **测试为何没拦住**：`tests/test_editor_service_v1_6_0.py:293-305 test_failure_during_replace_keeps_original` 用的是
  `tmp_path` 下**非只读**目标（`copymode` 复制 0o666，`os.unlink` 成功）；`test_write_replaces_content_without_temp_residue`（`:253`）
  只验证**成功**写入后无残留。没有"失败写入后无残留（$场景=只读目标）"的用例。

  AC-41 的 ①（非静默）与 ③（原文件 sha256 不变）实测**通过**：抛出的是 `EditorError: ro.txt 保存失败：PermissionError`
  （文案只含文件名，见 §2.3），`content after: b'old-ro'` 未被破坏。
- 建议：异常路径先清只读位再删（`os.chmod(tmp_path, stat.S_IWRITE)` / `os.chmod(tmp_path, 0o600)` 后再 `unlink`）；
  或把 `copymode` 挪到 `os.replace` **成功之后**（`preserve_mode` 的原意是"新文件保持旧文件的可执行位"，放在成功后做同样成立）；
  至少要让 `_silent_unlink` 的失败**响亮报警**而不是 `pass`。

---

### [中等] UTF-16（BOM）文件的换行符被误判为 LF ⇒ **未编辑也改写磁盘字节**

- 位置：`jflove-desktop/src/services/text_detect_service.py:421-427`（`_detect_line_end` 对**原始字节**计 `\r\n` / `\n`）；受害点 `jflove-desktop/src/services/editor_service.py:150-168`（`normalize_line_end`）+ `:267`（`encode_text`）
- 违反：需求 v1.6.0 **§9.10 AC-S4 ③**（"UTF-8-BOM 文件保存后 BOM 保留、**CRLF 保留**"的同口径）与 §9.10 AC-20；设计 §6.2.2「按原编码 + **原换行符**原子写回」
- 证据（真实实现，`probe → read_text → write_text`，全程未改动一个字符）：

  ```text
  probe ok=True enc=utf-16le line_end=lf bom=True
  orig  bytes=fffe 604f 7d59 0d00 0a00 164e 4c75 0d00 0a00   （你好\r\n世界\r\n）
  after bytes=fffe 604f 7d59 0a00 164e 4c75 0a00             （你好\n世界\n）
  IDENTICAL=False
  ```

  UTF-16LE 的 CRLF 字节是 `0d 00 0a 00`，**字节层面不出现相邻 `\r\n`**，故 `crlf=0`、`lone_lf=N` ⇒ 判为 `lf`
  ⇒ 写回时 `normalize_line_end(text,"lf")` 把每处 `\r\n` 变成 `\n`。用户什么都没改，磁盘字节已变（每个 CRLF 少一个 CR）。
  **同源代码**还导致混合换行文件被归一化（实测）：`line_end=lf  orig=b'a\nb\nc\r\nd\r\n'  after=b'a\nb\nc\nd\n'  IDENTICAL=False`。
  作为对照，三种"字节级换行符 == 字符级换行符"的编码实测**字节完全相同**：UTF-8+CRLF、UTF-8-BOM+CRLF（BOM 三字节保留）、GBK+CRLF。
- 建议：`_detect_line_end` 在已判定 `utf-16le/utf-16be`（有 BOM）时**先按该编码解码再数换行**；混合换行时不要静默归一化
  （要么保留原文的换行序列，要么给出一次性提示）。对应补一条 UTF-16+CRLF 的往返用例——既有
  `tests/test_editor_service_v1_6_0.py:160 test_utf16_bom_round_trip` 用的是 `\n` 样本，正好绕开了这条路径。

---

### [中等] 单实例转交失败的降级路径**静默丢弃**命令行路径（与同文件注释、设计 §6.6.1 自相矛盾）

- 位置：`jflove-desktop/src/main.py:98-108`（唯一交付点 `:108` 挂在 `elif` 上）
- 违反：设计 §6.6.1「降级为本地启动，**绝不静默丢弃**用户"用 JFLove 打开"的意图」（`main.py:100` 就是这句原话）；
  需求 §9.6 AC-48（"窗格数 = 有效路径数"）
- 证据：

  ```python
   98: if not guard.try_become_primary():
  101:     if guard.forward(cli_paths, cwd=os.getcwd()):
  103:         return 0
  104:     logger.warning("单实例转交失败，本进程降级为本地启动（可能出现两个实例）")
  105: elif cli_paths:                       # ← 只有"首实例"分支才会交付命令行路径
  108:     guard.notify_external_paths(cli_paths, source=SOURCE_CLI)
  ```

  `forward()` 返回 False（连接 3 次失败 / 无 ack / ack 被拒，`single_instance.py:454-489`）时，本进程落到 `:104` 后**继续把
  `WebShell` 起起来**（自己开窗），但 `cli_paths` 既不转交也不入队：`try_become_primary()` 为 False，`elif` 不执行，
  本进程随后再无 `notify_external_paths` 调用点 ⇒ 用户双击的那几个文件**一个都不打开**，界面上也没有任何提示。
- 建议：把 `:105` 改成独立的 `if cli_paths:`（或紧接 `:104` 之后补一次 `guard.notify_external_paths(cli_paths, source=SOURCE_CLI)`），
  让降级进程自己打开这些路径。

---

### [轻微] 冷启动固定多付 ~250 ms（`STARTUP_SETTLE_MS` 盲等）

- 位置：`jflove-desktop/src/utils/single_instance.py:415`（`self._sleep_ms(STARTUP_SETTLE_MS)`；常量定义 `:108`）
- 证据（真实 `QCoreApplication`，连测 3 次）：`become_primary=True elapsed=264 / 275 / 263 ms`；
  第二实例判定 `elapsed=0 ms`（`:410-412` 的连接探测命中即返回）。而 `AGENTS.md §8` 明确在意冷启动时延（onedir 1.31 s 是记录在案的目标）。
- 建议：④ 的自探测（`:440-445 _confirm_sole_listener`）已能抓住"`listen()` 成功但名字被别人占了"的竞态，
  ② 的盲等 250 ms 可砍到 50–80 ms，或仅在 ① 返回的连接错误**不是** `ServerNotFoundError` 时才等。

### [轻微] 「替换前被强杀」会在用户目录留下含正文的隐藏临时文件（无任何清理）

- 位置：`jflove-desktop/src/services/editor_service.py:298-300`（同目录 `.{name}.jflove-{随机}.tmp`）
- 违反：AC-S1 ② 的**意图**（"保存过程产生的临时文件不残留"）——该条字面只约束"正常保存成功后"，故按轻微记
- 证据：`orphan temp left in user dir: .keep.txt.jflove-ofhxrczv.tmp`（进程在 `mkstemp` 与 `os.replace` 之间被杀时）。
  代码里没有任何"启动清扫历史临时文件"的逻辑。注意它**与上一条中等发现不同**：上一条是异常路径清理失败，本条是根本没有机会清理。
- 建议：启动时（或在 `atomic_write` 入口）清扫目标目录下 `.{name}.jflove-*.tmp`。

### [轻微] 协议对 `action` 取值不设白名单，任何非 `selfprobe` 动作都按"打开路径"处理

- 位置：`jflove-desktop/src/utils/single_instance.py:613-622`（只特判 `ACTION_SELF_PROBE`，其余全部走 `:617-622` 的交付）
- 证据：`decode_message()`（`:278-314`）只校验 `action` 是**非空字符串**（`:299-301`），不校验取值；
  因此 `{"v":1,"action":"ack","paths":["..."]}` 这类串味消息也会被 `_write_ack(ok=True)` 接受并交付给编辑器。
  协议全集只有 `open` / `ack` / `selfprobe` 三个（`:85-89`）。
- 建议：`:613` 前加白名单判定，未知 action 回 `ok=False, reason="unknown_action"`。

### [轻微] 面向渲染层的提示文案在特定输入下会带完整路径

- 位置：`jflove-desktop/src/services/text_detect_service.py:453`（`name = os.path.basename(path) or path`）；同型写法 `editor_service.py:257`
- 违反：`AGENTS.md §9.4.14`（异常 message 不 echo 完整路径）/ 需求 §9.14 AC-R5（文案面）
- 证据：`probe("C:\\...\\subdir\\")` → `name` 回退成整条路径，`message` 变成
  `C:\Users\admin\AppData\Local\Temp\1\a2probe-k85qds3e\subdir\ 是目录，不是文本文件`，
  该 message 经 `bridge/editor.py:300 _notice()` 推到渲染层 `editor.notice`。
  可达性低：命令行路径经 `normalize_paths()` 的 `os.path.abspath()` 会去掉尾部分隔符，只有"渲染层/拖放直接给带尾分隔符的路径"才会命中。
- 建议：`os.path.basename(path.rstrip("/\\")) or "<路径>"`，或直接复用已存在的 `safe_ref()`（`text_detect_service.py:270`）。

### [轻微] 原子写只 `fsync` 临时文件，未 `fsync` 父目录

- 位置：`editor_service.py:305`（有 `os.fsync(fp.fileno())`）、`:311`（`os.replace` 之后无目录 fsync）；`scratch_service.py:167-195` 同
- 影响：POSIX 掉电场景下 rename 可能未持久化（AC-S1 未覆盖此情形）；Windows（本版主平台）无对应语义。
- 建议：POSIX 分支在 `os.replace` 后对 `os.open(directory, os.O_DIRECTORY)` 做一次 `fsync`；或明确写进设计"不做目录 fsync"。

---

## 2. 逐方向取证记录（重点取证方向 1–9）

### 2.1 AC-S1 原子写（editor_service.py:284-341）

| 判据 | 结论 | 证据 |
| --- | --- | --- |
| 临时文件**同目录** | ✅ | `:299 dir=directory`，测试 `test_temp_file_is_same_dir_random_name`（`tests/test_editor_service_v1_6_0.py:262-277`）断言名字形如 `.b.txt.jflove-*.tmp` 且 `!= ".b.txt.tmp"`（**不是**本版要修的固定 `.tmp` 名） |
| `os.replace` 原子替换 | ✅ | `:311 → :317-340`，含 3 次 × 50 ms 短重试（Windows 短占用） |
| fsync | ⚠️ 部分 | 临时文件有 `fsync`（`:305`）；**父目录无 fsync**（见【轻微】） |
| 失败路径清理临时文件 | ❌ **只读目标场景失败** | 见【中等】第 2 条：`copymode` 让临时文件变只读 ⇒ `_silent_unlink` 的 `unlink` 报 `WinError 5` 被吞 |
| 失败非静默 | ✅ | 只读目标实测 `EditorError: ro.txt 保存失败：PermissionError`（`editor_service.py:271-272`）；`bridge/editor.py:322-330` 未吞异常，错误经桥响应回渲染层 |
| 失败不改动编辑器状态 / 原文件 | ✅ | 实测 `content after: b'old-ro'`；注入 replace 失败后同样保留旧内容（`leftover` 对照见上） |
| 测试是否真注入「写临时文件失败」 | ✅ | `tests/test_editor_service_v1_6_0.py:279-291` 把 `editor_service.os.fsync` 换成抛异常，断言目录仍只有 `["c.txt"]` |
| 测试是否真注入「替换前强杀」 | ⚠️ 近似 | `:293-305` 把 `_os_replace` 换成抛异常 —— 覆盖了"替换没发生"，但**异常路径本身有清理**，而"进程被强杀"没有机会跑清理；这正是【轻微】第 1 条残留形态没被覆盖的原因 |

### 2.2 AC-S4 编码判定顺序与回写（text_detect_service.py）

判定顺序实测与设计一致：`os.stat` 大小 → 扩展名白名单（命中则**不嗅探**）→ 读 `TEXT_SNIFF_BYTES`(8 KB) →
NUL 字节 → `_strict_decode`（**禁 `errors="replace"`**，`:331` 注释明写）→ UTF-16 无 BOM 的 b3 模式 → 编码判定。

- `errors="replace"` **不在写入路径上**：全 `jflove-desktop/src` grep 只有两处 —— `src/bridge/files.py:153`（既有文件预览路径，非编辑器）与 `text_detect_service.py:331` 的一句**注释**。✅
- 编码回写逐字节保真（实测，未修改直接保存）：
  `utf-8+CRLF IDENTICAL=True` / `utf-8-bom+CRLF IDENTICAL=True（BOM 三字节在）` / `gbk(GB18030)+CRLF IDENTICAL=True，re-decode='中文测试\r\n第二行\r\n'`。✅ AC-20 / AC-38 / AC-50a ④ 的正例成立。
- 「无法判定编码」**不是**静默按 UTF-8：`detect_encoding` 返回 `None` 时 `probe()` 走 `:543` 记录并给出提示，不是默认 UTF-8。✅
- **但 GB18030 兜底几乎不可证伪** ⇒ AC-S4 ④ 点名的 Latin-1 样本被静默判成 `gbk`（见【中等】第 3 条）。
- 保存**回写原编码**：`write_text` 要求 `encoding` 必填（服务层硬约束）并走 `encode_text`，GBK 文档保存后仍是 GBK（实测）。✅

### 2.3 AC-S3 读前拦截的落点（是否在读取完整内容之前判定）

方法：猴子补丁 `builtins.open` 统计**目标文件**的 `open()` 次数与 `read()` 字节数（脚本 `%TEMP%\a2probe2.py`），
配合 200 MB / 60 MB / 20 MB 三个文件（NTFS 稀疏）实测：

| 样本 | `probe` 结果 | 目标文件 `open()` 次数 | 从中读到的字节 | 耗时 | 私有内存 |
| --- | --- | --- | --- | --- | --- |
| 200 MB `.exe`（MZ + NUL） | `ok=False reason=too_large`，提示"文件过大（> 50 MB），已拒绝打开" | **0** | **0** | 1.1 ms | 不可测（见下） |
| 60 MB `.txt` | `ok=False reason=too_large` | **0** | **0** | 0.5 ms | 不可测 |
| 20 MB `.log` | `ok=True read_only=True` | 1（仅嗅探） | 65536（8 KB + 64 KB 样本） | 0.7 ms | 不可测 |
| 20 MB `.log` 续：`read_text` | `ok=True chars=20971520` | 1 | 20905984 | 32.6 ms | 不可测 |

- `open()` 次数为 **0** 是"未把文件读入内存"的直接证据：**大小阈值判定发生在任何读取之前**（`:466-495` 先 `os.stat`）✅
- `> 50 MB` 一律以 `too_large` 拒绝（200 MB 二进制也是 `too_large` 而非 `binary` —— 顺序是大小优先），AC-50 ③ / AC-S3 ①② 成立。
- 内存判据（AC-S3 ④ 的 `PrivateMemorySize64`）**本面未能取到有效值**：`ctypes.windll.psapi.GetProcessMemoryInfo` 在本机返回 0（脚本输出 delta 0.0 MB，不可信），故改用"目标文件被读取的字节数"作为等价判据。
- **20 MB 只读降级是真禁写，不只是 UI 标记**：`write_text(20MB目标) → {'ok': False, 'reason': 'read_only', 'error': '只读'}`，
  因为 `editor_service.py:207-209` 自己按 `os.stat().st_size > TEXT_READONLY_BYTES` 复判，**不依赖**渲染层的 `pane.readOnly`；
  且 `bridge/editor.py:322-328` 根本没把 `read_only` 传下去 —— 全靠服务层这层兜底，路径反而更硬。✅
- 5 MB 边界精确：`st_size = 5242880 → read_only=False`，`5242881 → read_only=True`（`>` 语义，与 AC-52 一致）✅

### 2.4 AC-42 零远端 / AC-R4 ⑤（命令与输出）

```powershell
cd D:\github\jflove\jflove-desktop
Select-String -Path src\bridge\editor.py,src\services\editor_service.py,src\services\text_detect_service.py,`
  src\services\scratch_service.py,src\services\file_assoc_service.py,src\utils\single_instance.py `
  -Pattern 'requests|http_client|api/v1|urllib|QNetwork|note_service|disk_service|file_service'
# → 0 命中
Select-String -Path src\services\text_detect_service.py,src\services\editor_service.py,src\services\scratch_service.py,`
  src\services\file_assoc_service.py,src\utils\single_instance.py -Pattern 'requests|http_client|/api/v1|urllib|socket\.|QNetwork'
# → 1 命中：src\utils\single_instance.py:740  state() == QLocalSocket.SocketState.ConnectedState（本机 IPC，非网络出口）
```

⇒ 5 个新模块 + 桥的编辑器子模块对 `requests` / `http_client` / `/api/v1` / `urllib` / `QNetwork` **0 命中**；
`file_assoc_service.open_system_defaults()` 只调 `QDesktopServices.openUrl("ms-settings:defaultapps")`（`file_assoc_service.py:330-343`），
`_is_os_registered()` 只 `winreg.OpenKey(..., KEY_READ)`（`:236`），**整个模块没有任何写注册表的代码路径** ✅
（`ui/src` 的渲染层部分属另一审查面，本面未覆盖。）

### 2.5 AC-46 / AC-47 / AC-48 端到端（**真·双进程实测**）

脚本：`%TEMP%\a2si_primary.py`（首实例：`try_become_primary()` → 延迟 1.5 s 才注入 sink，好让第一批走"入队→冲刷"路径）
与 `%TEMP%\a2si_second.py`（第二实例：`normalize_paths(argv)` → `forward()`），driver 用 `Start-Process` + 两次调用。

```text
SECOND_NORMALIZED=2 names=one.txt, two.txt        SECOND_IS_PRIMARY=False   SECOND_FORWARD=True    exit=0
SECOND_NORMALIZED=2 names=三.txt, 不存在.txt      SECOND_IS_PRIMARY=False   SECOND_FORWARD=True    exit=0
---- 首实例 stdout（UTF-8）----
PRIMARY_TRY_BECOME_PRIMARY=True
SINK source=single_instance count=2 names=one.txt, two.txt
SINK source=single_instance count=2 names=三.txt, 不存在.txt     ← 中文路径逐字符正确
SINK_ATTACHED pending_flushed=2                                  ← 两批都在接入点就绪前到达，走的是队列+冲刷路径
RECEIVED_TOTAL=2
```

- 第二实例：`try_become_primary()=False`、`forward()=True`、**退出码 0** ⇒ AC-46 ① 的进程侧语义成立（不新起实例、路径转交、收到 ack 才退出）。✅
- 路径**顺序与条数**在真进程间保持（2 条 / 2 条），含中文与"不存在的路径"（`normalize_paths` 按设计**不校验存在性**，`single_instance.py:182`）。✅
- 首实例侧 sink 是 `main.py:108 → guard.set_paths_sink(lambda ...: _shell.notify_external_paths(...))` 那一个接入点（设计只允许一个）。
- 注：第一次用 `Get-Content`（默认 GBK 解码）读 stdout 时中文显示成 `涓?txt`，**加 `-Encoding utf8` 后完全正确** —— 是显示层假象，**不是**协议缺陷（协议 `encode_message` 用 `ensure_ascii=False` + UTF-8，`_parse_json_line` 用 `bytes.decode("utf-8")`）。
- **AC-47 结构上成立**：`main.py:80-108` 的 guard 判定紧接 `build_app()`，**在** `_migrate_legacy_data()`（`:112`）、`Bridge()`（`:120`）、
  `WebShell(...)`（`:121`）、任何 `http_client` 使用之前；`single_instance.py` 只 import Qt 与标准库。
  ⇒ 单实例路径不依赖登录/服务端 ✅（端到端"未登录+断网下新建/保存/还原"属渲染层与真实 GUI，见 §3）。

### 2.6 日志安全（§9.14 / AC-R5）

全量 `logger.*` 调用逐条读过（`src` 全仓 188 处，其中新模块见下），**新模块自身 0 处打印完整路径或正文**：

| 模块 | 手法 | 位置示例 |
| --- | --- | --- |
| `text_detect_service.py` | 一律 `safe_ref(path)`（`:270` 定义：文件名 + sha256 截断） | `:471/:476/:492/:508/:523/:543/:622/:634` |
| `editor_service.py` | `safe_ref(path)` | `:259`（拒绝只读）、`:273`（已保存 size） |
| `scratch_service.py` | 只记 `_pane_file_name(pane_id)` / 计数 / 固定文件名 | `:392/:395/:413/:489/:519/:605` |
| `single_instance.py` | `log_safe_names()`（`:155-172`，强制只留最后一段） | `:479/:487/:620` |
| `file_assoc_service.py` | `path.name`（只文件名）/ 只记异常类型名 | `:114/:122/:127/:175/:316` |

亮点：收到畸形消息时**只记长度不记内容**，注释明写原因 —— `single_instance.py:603`
`logger.warning("单实例：收到不合法的消息（%d 字节），已忽略", len(raw))`；`encode_message` 的 `cwd` 字段"接收端不落日志"（`:232`），实测 `_handle_connection()` 确实没用它。
唯一反例是 §1 的【轻微】"面向渲染层文案带完整路径"。
（注：`main.py:63-69 _warn_missing_frontend()` 把前端产物**绝对路径**写进 `logger.error` 与对话框，属**既有**代码、不在本版 diff 内，未计入发现。）

### 2.7 AC-B5（Python 版本口径）

`jflove-desktop/README.md:95-104` 显式记录：文档/CI/镜像要求 **3.14+**，而本机 `venv-win/` 实测为 **3.13.14**
（`Programs\Python` 下只有 `Python313`），"**不是文档写错**"、v1.6.0 全部用例在该版本下全绿、口径为"只记录差异、不升级环境"。✅
`python -V` 仍为原版本，审查期间未做任何环境升级/重建 venv 的动作。

### 2.8 独立复跑门禁

```text
jflove-desktop\venv-win\Scripts\python.exe -m pytest tests\ -q      → 415 passed in 6.64s（无 warnings summary 段）
jflove-desktop\venv-win\Scripts\python.exe -m flake8 src\ tests\ --max-line-length=99 → 无输出，exit 0
```

### 2.9 AC-44 / AC-S2 草稿侧、tray 改动

- `file_assoc_service`：`set()` 归一去重**再**校验、遇到清单外扩展名**抛错且此时不写盘**（`:308-313`），
  只写 `settings.FILE_ASSOC_FILE`（`:192-203` 走 `editor_service.atomic_write`），`get()` 只报"仍在 supported 内"的勾选并注记被忽略项（`:265-270`）。
  跨重启保留 = 落盘于 `USER_DATA_DIR/storage/file_assoc.json`（settings 常量），**不是** QSettings/localStorage ⇒ AC-44 成立。✅
- `ui/tray.py:97` 由 `quit_application` 改为 `request_quit` 与 `main.py` 退出握手接线一致（`+12 -1` 只有 docstring 与这一行）。
- `scratch_service` 的原子性/容错整体设计是对的（索引 `.tmp` 副本恢复、`pane-*.txt` 先写、索引后整体替换、`clean_exit` 标记、
  `clear_all_drafts` 用 `glob` 覆盖孤儿文件）；**唯一缺口就是【严重】那条**：一旦走到"空索引"分支，写路径会把损坏状态扩散成全量丢失。

---

## 3. AC 判定表（本面负责的 41 条）

| AC | 判定 | 判据 |
| --- | --- | --- |
| AC-20 | **通过** | UTF-8-BOM+CRLF 样本 `probe→read_text→write_text` 实测 sha256 相同、`EF BB BF` 在、CRLF 在（§2.2） |
| AC-22 | **通过（Python 侧）** | 未知扩展名+文本 → `probe` `ok=True`、`language=text`，内容逐字符相等；"手动切换语言"属渲染层 |
| AC-36 | **无法验证（无头）** | 判据核心是"原生文件对话框出现/取消后磁盘不产生文件"，需真实 GUI；Python 侧 `write_text` 只写传入路径 |
| AC-37 | **通过（Python 侧）** | `write_text` 单目标原子写、不产生副本（`editor_service.py:284-314`）；"不弹对话框"属渲染层 |
| AC-38 | **通过** | 实测 UTF-8+CRLF 无修改保存 `IDENTICAL=True` |
| AC-39 | **通过（Python 侧）** | 另存为走 `write_text(q)`；`_read_only_reason` 对不存在的 q 返回非只读（`:210-212`）；原 p 不被触碰 |
| AC-40 | **通过（Python 侧）** | 草稿落在 `USER_DATA_DIR/storage/scratch/`（AC-40② 明确排除该目录）；`write_text` 只在显式保存时调用；1.5 s 防抖在渲染层 |
| AC-41 | **未通过（④）** | ①非静默 ✅（`EditorError: ro.txt 保存失败：PermissionError`）、②③✅（`old-ro` 未变）；**④ 只读目标场景残留只读临时文件**（§1 中等第 2 条） |
| AC-42 | **通过（Python 侧）** | ① grep 0 命中（§2.4）；② 编辑器模块无网络依赖，离线可用性属渲染层；③ 远端文件管理页入口属 Web/渲染层 |
| AC-44 | **通过** | 勾选落盘 `storage/file_assoc.json` + 原子写 + 读回同一文件 ⇒ 重启保留；`get/set` 语义符合 §7.1 #14/#15 |
| AC-46 | **通过（Python 侧，真双进程实测）** | 第二实例 `forward=True` 后 `exit 0`、不成为监听者；② 窗口恢复可见 / ③ 窗格内容属渲染层 |
| AC-47 | **通过（结构 + 实测）** | guard 位于任何会话/网络依赖之前（`main.py:80-108`）；双进程实测全程无 http；①③ 端到端属渲染层 |
| AC-48 | **通过（Python 侧）** | 真双进程：2 条路径保序保数、中文路径逐字符正确、不存在路径不中断其余（`detect_and_read` 单条失败 → `_notice`）；"窗格数=有效路径数"属渲染层。⚠ 另一路径见 §1 中等第 4 条（转交失败降级时 0 条打开） |
| AC-48a | **通过（本面可见部分）** | 扩展名清单只从 `ui/public/file-assoc.manifest.json` 派生（`file_assoc_service.py:103-139`），本模块**不手打**任何扩展名；派生链与判定属脚本/渲染层面 |
| AC-49 | **通过** | 白名单命中即返回语言、**不嗅探**（`text_detect_service.py:512-519` + 测试 `test_whitelist_hit_skips_sniff_on_binary_renamed_log`） |
| AC-50 | **通过** | NUL → `reason=binary` 拒绝 + 只读前 8 KB；200 MB 二进制实测 0 次 `open()`、1.1 ms（§2.3） |
| **AC-50a** | **通过** | GB18030 中文（零 ASCII）实测 `probe ok=True`、`encoding=gbk`、往返**字节相同**、`re-decode='中文测试\r\n第二行\r\n'`；`[负向对照]`（"非文本比例>0.30"）已在需求 §9.7 D19 删除，注入式反向验证属测试面 |
| AC-51 | **通过（Python 侧）** | 未知扩展名+文本 → `language=text`、内容逐字符相等（`test_unknown_extension_text_falls_back_to_text`）；"无高亮"属渲染层 |
| AC-52 | **通过** | 20 MB `.log` → `read_only=True`、内容完整（20971520 字符）、服务层真禁写（`{'ok': False, 'reason': 'read_only'}`）；边界 `=5 MB` 放行 / `+1 B` 只读 |
| AC-53 | **通过** | 60 MB `.txt` / 200 MB `.exe` 实测 0 次 `open()`、0.5 / 1.1 ms、`reason=too_large`、提示含"文件过大" |
| AC-54 | **无法验证（无头）** | "防抖后草稿存在 + 内容不变则 mtime 不变"需真实 GUI 计时；注意 Python 侧 `save_draft` **不做内容去重**（`:452-490` 每次调用都重写），"不重写"必须由渲染层保证 ⇒ 该项判据完全落在渲染层 |
| AC-55 | **通过（Python 侧）→ 但受 §1【严重】影响** | `list_drafts` + `is_abnormal_exit`（`clean_exit=false`）提供还原集；"3 窗格自动还原"端到端属渲染层。索引损坏时该集合会静默缩小 |
| AC-56 | **无法验证（无头）** | 退出确认框属 `web_shell` / 渲染层（本面文件清单外；`tests/test_v160_web_shell_quit_handshake.py` 有覆盖，需真实 GUI 复核"取消后 PID 仍在"） |
| AC-57 | **通过（草稿侧）** | `retain=false` → `web_shell.py:936 clear_all_drafts()` 清空目录与索引（`scratch_service.py:578-606`）；"该选择只出现一处"属渲染层 |
| AC-58 | **通过（Python 侧）** | 最小化到托盘不触达草稿：`clear_all_drafts` 的调用点只有 `web_shell.py:936`（退出握手 retain=false）与 `bridge/editor.py:408-415`（用户显式入口），没有"隐藏即清理"的路径 |
| AC-59 | **通过（Python 侧）** | `discard_draft` 幂等、删内容 + 删条目（`:493-520`）；询问框属渲染层 |
| AC-60 | **通过（Python 侧）** | 窗格数不设上限；`_pane_lock` 按 paneId 串行化（`:434-447`）+ `_index_lock` 整体替换，测试 `test_并发不同pane写入_索引不丢条目` |
| AC-61 | **通过** | `clear_all_drafts` 用 `glob` 覆盖孤儿内容文件（`:593-598`）+ 清 `.tmp/*.tmp` + 索引置空 `panes: [] clean_exit: true`；已保存文件不在草稿目录 |
| AC-S1 | **通过（① ② 正常路径）** | 同目录 mkstemp 随机名 + fsync + `os.replace`（含重试）；写入/替换失败均保留旧内容、无残留；「替换前强杀」形态见【轻微】 |
| AC-S2 | **未通过（②）** | ①③ 通过（`.tmp` 副本恢复、原子写不产生半截文件）；**② 在索引损坏 + 无 `.tmp` 副本时被违反**（§1 严重） |
| AC-S3 | **通过** | 200 MB / 60 MB 目标 `open()` 次数 **0**、≤1.1 ms；20 MB 只读态在服务层真禁写；5 MB 边界精确。内存判据本机取不到有效值（§2.3 已说明，改用读取字节数作等价判据） |
| AC-S4 | **部分未通过（④）** | ①②③ 实测通过（GBK 正确显示/回写字节不变；UTF-8-BOM 的 BOM 与 CRLF 保留）；**④ Latin-1 样本 3/5 被静默判为 `gbk`**（§1 中等第 3 条） |
| AC-B5 | **通过** | `jflove-desktop/README.md:95-104` 显式记录 3.14+ 要求 / 本机 3.13.14 / 不升级环境口径；无升级动作 |
| AC-R2（桌面端） | **通过** | `pytest tests\ -q` → **415 passed**（无 warning 段被吞）；`flake8 src\ tests\ --max-line-length=99` → exit 0 |
| AC-R4 ⑤（Python 侧） | **通过** | 6 个编辑器相关 Python 模块对远端/笔记/磁盘读写关键字 **0 命中**（§2.4）；④⑥ 的 `ui/src` 部分属另一面 |

---

## 4. 无法验证 / 待补的项

1. **无头环境**（`_scope.md §3` 已声明）：AC-36 / AC-56 / AC-57（渲染层部分）/ AC-54 的 GUI 计时、AC-46 ② 的"窗口恢复为可见"、
   AC-41 的"编辑器内容与脏标记不变"、AC-42 ② 的端到端离线可用 —— 一律 **无法验证（无头）**，需要真实 GUI + 两次真实启动/杀进程才能判定。
2. **内存判据**（AC-S3 ④ 的 `PrivateMemorySize64`）：`ctypes.windll.psapi.GetProcessMemoryInfo` 在本机返回 0（delta 0.0 MB，不可信），
   故 AC-S3 的内存部分**未直接验证**，用"目标文件被读取的字节数 = 0"作等价证据。若要严格判定，需在独立子进程内用
   `Get-Process -Id <pid> | Select PrivateMemorySize64` 前后取样。
3. **渲染层配对项**：AC-22/AC-36/AC-37/AC-40/AC-51/AC-52/AC-54/AC-55/AC-57/AC-59 的"可观测"部分（对话框、脏标记、状态栏、窗格数）
   在 `jflove-desktop/ui/src` 与 `bridge/editor.py` 的另一审查面；本面只给出 Python 侧判据。
4. **`[负向对照]` 的注入式反向验证**（AC-41 ③、AC-50、AC-50a、AC-S1 ③、AC-S2、AC-S4）：需要**改实现**才能跑，
   属测试面（Phase 6）职责；本面只核"当前实现能否通过正向判据"。
5. `file_assoc_service` 的 **AC-48a ②**（类型表新增一项 → 派生链自动 +1）需要 Node 脚本与 `ui/src/config/scratch-types.ts`，本面未跑（属脚本/渲染层面）。

---

## 5. 本面取证脚本（临时文件，均在 `%TEMP%`，未写入仓库）

| 脚本 | 覆盖 |
| --- | --- |
| `%TEMP%\a2probe.py` | Latin-1 编码误判、UTF-16 CRLF 保真、UTF-8/UTF-8-BOM/GBK 正例往返、混合换行、目录路径文案、replace 失败残留、索引损坏后的写路径、`try_become_primary` 冷启动耗时 |
| `%TEMP%\a2probe2.py` | AC-S3：`builtins.open` 计数 + 读取字节数 + 耗时 + 5 MB 边界（200 MB / 60 MB / 20 MB 稀疏文件） |
| `%TEMP%\a2ac41.py`、`%TEMP%\a2ro.py` | AC-41：只读目标 / 目录被删 的失败路径与残留临时文件属性 |
| `%TEMP%\a2si_primary.py` + `%TEMP%\a2si_second.py` | AC-46/47/48 真·双进程端到端（含中文路径、队列冲刷路径） |
