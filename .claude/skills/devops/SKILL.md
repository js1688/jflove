---
name: devops
description: 运维工程师，负责构建、打包、部署、发布与生产环境维护。Use when: 用户需要版本发布、构建打包、Docker 部署、生产环境维护。
---

# devops

- 运维工程师，负责通过审查与测试的版本安全、稳定地发布到生产环境。
- 关注构建脚本、打包产物、依赖锁定、环境一致性、生产库表结构同步、回滚预案。
- 禁止越界规则：见 `AGENTS.md §1`。

## 发布前 checklist

- [ ] 审查报告无未解决严重问题，测试报告通过
- [ ] 设计/开发记录已为当前版本归档
- [ ] **`python scripts/check_db_schema.py` 退出码为 0**（含内置的「代码 → dev」前置检查；prod 表结构已与 dev 一致；退出码 1 = 发布阻塞项，见「生产库表结构同步（发布阻塞项，强制）」章节）
- [ ] 本版本有表结构变更时，`jflove-db/migrate-<版本号>.sql` + `jflove-db/rollback-<版本号>.sql` 均已交付且**实测执行过**
- [ ] jflove-prod.db 除 `init_db()` 写入的默认 config 键外，所有表 0 行（无业务数据）
- [ ] 安全宪法 §9 发布检查清单逐条通过

## 发布前环境检查

构建前由根 `python build.py` 自动做逐模块环境检查（不满足则打印原因并跳过）。下表为各模块环境依赖参考：

| 模块 | 检查命令（Linux / Windows） | 通过条件 |
|------|---------|---------|
| jflove-server | `python --version` | Python 3.14+ |
| jflove-desktop | `python --version` + `pip list 2>&1 \| grep -i PySide6` / `pip list 2>&1 findstr PySide6` | Python 3.14+ + PySide6 |
| jflove-app | `flutter --version` + `flutter doctor` | Flutter SDK + Android SDK 已配置 |
| jflove-web | `docker version` | Docker 已启动（web 用 Docker 多阶段构建，无需本地 Node） |
| jflove-app R8 检查 | `grep -nE "isMinifyEnabled\s*=\s*true\|isShrinkResources\s*=\s*true" jflove-app/android/app/build.gradle.kts` / `findstr ...` | **无输出**（R8 必须关闭，见下方 ⚠️） |

> ⚠️ **移动端 R8 禁令（强制）**：`android/app/build.gradle.kts` 的 release buildType 中 `isMinifyEnabled` 和 `isShrinkResources` 必须为 `false`。Flutter 的 Dart 编译器已做 tree-shaking，再叠加 R8 的 `proguard-android-optimize.txt` 激进优化会破坏 Flutter 引擎和插件的反射/FFI 调用链，导致 release APK 闪退或白屏。**此项检查为发布阻塞项，不通过则阻断发布。**

**如果环境不满足，在版本发布记录中标记为"❌ 环境未就绪"并列出需安装的组件，不得跳过构建步骤直接输出"无变更"**。

> ⚠️ **Android licenses 自动接受**：`flutter doctor` 若提示 `licenses not accepted`，用以下命令自动回复 y（不要等用户手动输入）：
> ```bash
> yes | flutter doctor --android-licenses
> # Windows PowerShell:
> # "y`ny`ny`ny`ny`ny`ny`ny`ny`ny" | flutter doctor --android-licenses 2>&1
> ```

## 「仅本地打包」与「正式发布」是两个动作（强制区分）

> 依据 `.claude/skills/LESSONS.md` L22。**回复里必须让人一眼看出这次做的是哪一个。**

| | 仅本地打包（试用稿） | 正式发布 |
|---|---|---|
| 触发 | 用户说"打包给我先体验 / 我先验证一下 / 没问题再发布" | 用户说"发布" |
| 构建 | ✅ `python build.py -m <模块>` | ✅ 同左 |
| 产物报告 | ✅ **路径 + 大小 + sha256 校验值 + 试用方式**（怎么装、怎么跑、连哪个地址） | ✅ 同左 |
| 生产库结构核对 | ⚠️ 建议做（`check_db_schema.py`），因为产物要用真实库；**未做要说明** | ✅ **必做，退出码 0，阻塞项** |
| 版本号封板 / `sync_version.py` | 走构建脚本自动同步即可，**不算封板** | ✅ 必须 `version.json` + 全位置一致 |
| `文档记录/版本发布记录/<版本号>.md` | ❌ **不产出** | ✅ 必须 |
| PMO 记录（Phase 7） | ❌ **不产出** | ✅ 必须 |
| 根 `README.md`「版本变化」/「功能特性」 | ❌ 不改 | ✅ 必须改 |
| git（提交 / tag / Release / CI 触发） | ❌ **一律不动** | ✅ 按「发布方式分叉」执行 |
| 交付时明确声明 | 必须写明"**未产出发布/PMO 记录、未动 git**" | 必须写明产物与发布范围 |

- 仅本地打包时，代码审查（Phase 5）与测试报告（Phase 6）是否已出，取决于用户要求；**没出就在回复里说清"目前只是可运行的试用产物，尚未走审查/测试"**，不要把试用稿说成"已通过测试"。
- 试用稿的产物路径要与上一次的产物**区分清楚**，并在回复里点名"哪个文件是最新的"。
  桌面端**交付形态是安装包**（`JFLove-<ver>-win64-setup.exe` / `*.rpm`）；onedir 目录与便携 zip 是附带产物。
  产物被运行中的实例占用时 `build.py` 会自动改名（onedir → **整目录** `JFLove-new/`，安装包 → `*-setup-new.exe`），
  这时要在回复里说明"当前装的是哪一份"。

## 构建规则（强制）

- **统一打包入口（强制）**：所有本地打包一律走仓库根 `python build.py`，**禁止直接调用各模块 build.py / flutter build**：
  - `python build.py -m all`             → 构建全部已开发模块
  - `python build.py -m server,desktop`  → 构建指定模块
  - `python build.py`                    → 交互式多选模块
  - 根 build.py 自动完成：版本号同步 → 逐模块环境检查（不满足则打印原因并跳过）→ desktop 切 venv → 依次打包 → 汇总
  - 各模块构建产物：
    - server  → Docker 镜像 `jflove-server:<version>` + `latest`
    - desktop → PyInstaller **onedir 目录** `build/dist/JFLove/`（`JFLove` / `JFLove.exe` + `_internal/`）
      ⇒ 再打成**安装包**：Windows `JFLove-<ver>-win64-setup.exe`（Inno Setup）+ 便携
      `JFLove-<ver>-win64-portable.zip`；Linux/Fedora `jflove-desktop-<ver>-1.fc<N>.x86_64.rpm`
      - **v1.5.0 起不再产单体 exe**（onefile 每次启动要解压数百 MB：实测「启动→首窗可见」
        中位 9.58s → onedir 1.31s，快约 7.3×）。老用法仍可用 `jflove-desktop/build.py --mode onefile`
      - 本机（Windows）**不能**产 RPM（PyInstaller 不能交叉编译）：`--rpm` 在非 Linux 上**非 0 退出并给指引**；
        RPM 走 Fedora 机器（`packaging/linux/build_rpm.sh`）或 CI 的 Fedora 容器 job
      - Windows 出安装包需 Inno Setup 6（`ISCC.exe`）；缺失时 `build.py` **只降级出 zip 并打印指引，构建不失败**
      - ⚠ 构建前必须先生成 Mermaid 离线包（`resources/mermaid/` 是生成物，未被 git 跟踪）：
        `cd jflove-web && npm ci && node ../scripts/build_mermaid_bundle.mjs`
        —— 少了它 `assert_mermaid_assets()` 会让构建直接失败（CI 两个 job 都已内置这一步）
    - app     → APK `build/app/outputs/flutter-apk/app-debug.apk` + `app-release.apk`（**两个 APK 都必须真机安装验证**）
    - web     → Docker 镜像 `jflove-web:<version>`（可选 `--save` 导出 tar）
- **版本号单一来源（强制）**：版本号唯一真相是仓库根 `version.json`，其余位置由 `python scripts/sync_version.py` 派生/同步：
  - 服务端：`main.py` + `Dockerfile LABEL`
  - 桌面端：`settings.py APP_VERSION`
  - 移动端：`pubspec.yaml`（versionCode 由版本号派生 `major*1e6+minor*1e3+patch`）+ `settings_page.dart`（「关于」页显示）
  - Web 端：`package.json` + `constants.ts`
  - 三个 `build.py` 不再硬编码版本号，构建前校验模块内版本号与 `version.json` 一致，**不一致直接中止构建**（防「应用内版本号未更新」）
  - 改版本号：`echo '{"version":"x.y.z"}' > version.json && python scripts/sync_version.py`
- 生产库 DDL 变更必须有**升级 + 回滚**两个脚本，并先按「生产库表结构同步（发布阻塞项，强制）」章节核对 / 对齐 prod 结构；Docker 镜像内置空表结构 DB，支持 `-v /data` 挂载持久化

## 线上构建（GitHub Actions，本地构建的云端镜像）

> 各端已支持 GitHub Actions 线上打包（`.github/workflows/`），与本地构建并存、互不影响。
> 二者复用同一套 `build.py`，因此版本一致性校验、prod DB 空校验、R8 禁令、secret 扫描等安全宪法约束在云端同样生效。

| 模块 | Workflow | 触发 | 产物 |
|------|----------|------|------|
| jflove-server | `build-server.yml` | `workflow_dispatch` / 打 tag `v*` | GHCR `ghcr.io/<owner>/jflove-server:<ver>` + `latest` |
| jflove-web | `build-web.yml` | 同上 | GHCR `ghcr.io/<owner>/jflove-web:<ver>` + `latest` |
| jflove-desktop | `build-desktop.yml` | 同上 | Artifact（Windows `*-setup.exe` + `*-portable.zip`；Linux/Fedora `*.rpm`） |
| jflove-app | `build-app.yml` | 同上 | Artifact（`app-release.apk`） |

**使用要点**：
- CI 从 tag 提取版本并校验 `tag == version.json` 与模块内版本号，不一致直接失败；发版前本地 `python scripts/sync_version.py` 同步后再打 tag。
- 桌面端 macOS 仍在本地构建；移动端 iOS/鸿蒙未启用。
- 手动触发：仓库 Actions 页 → 对应 workflow → Run workflow；自动触发：推送 `v*` tag。

## 发布方式分叉（强制询问，不可跳过）

> ⚠️ **每次执行发布，第一步必须用 `AskUserQuestion` 强制询问用户发布方式，不得默认、不得跳过**，二选一：

1. **发布到 Git 仓库** —— 提交代码 → 推送 main → 打 tag → 推送 tag，触发 GitHub Actions 自动构建（云端产出镜像 / APK / 桌面端产物）
2. **仅限本地构建与发布** —— 只用根 `python build.py` 本地打包，**不做任何 git 提交 / 推送 / 打 tag**

- 本项目开源，`main` 即开发分支（无其他分支），每次发布都用 main 打 tag，不建 release 分支、不走 PR。
- 用户的日常提交（含写到一半的代码）由用户自行手动提交；devops 只负责「发布提交」。
- 选「发布到 Git 仓库」→ 走下方「Git 发布操作序列」；选「仅限本地」→ devops 不碰 git，产物与发布记录留本地。

## Git 发布操作序列（仅当选择「发布到 Git 仓库」）

> 触发线上构建的唯一方式是用 `git push` 推送 `v*` tag；GitHub 网页「Create release」自动生成的 tag **不会**触发 `on: push: tags`，必须手动 `git push origin <tag>`。

```bash
# 1. 版本号定稿并同步（同步后的 7 处版本号文件要随发布一起提交）
python scripts/sync_version.py --check   # 先确认一致，不一致先同步

# 2. 提交并推送 main（先推 main，再推 tag，顺序不可颠倒）
git add -A
git commit -m "chore: 发布 v<version>"
git push origin main

# 3. 打 tag 并推送（这一步触发 4 个 workflow）
git tag v<version>
git push origin v<version>

# 4. 观察 CI：Actions 页看对应 run，失败修复后重新打 tag 重推
#    打 tag 会同时触发 release.yml 自动创建 Draft Release（含 GHCR 命令 + 各端产物说明），
#    desktop/app 构建完成后会把产物自动上传为 release Assets；server/web 镜像推 GHCR。
```

**硬约束**：
- **顺序不可颠倒**：必须先 `push origin main`，再 `git tag` + `push tag`（tag 要指向已上远程的 commit）。
- **tag 名 = `v` + `version.json` 版本号**，去掉 `v` 后必须与 `version.json` 严格相等，否则 CI 版本校验直接 fail。
- 打 tag 前 `python scripts/sync_version.py --check` 必须通过；改了版本号却没同步 7 处，CI 照样挂。
- 命令中的 `<version>` 替换为实际版本号（如 `1.4.2`）。
- commit message 统一用约定式：`chore: 发布 v<version>`。

## 生产库表结构同步（发布阻塞项，强制）

> 对应 `AGENTS.md §5.1.1` / `§5.1.2`。**`jflove-db/jflove-dev.db` 是表结构基准**，`jflove-prod.db` 的结构发布前必须与它对齐 —— 但前提是 **dev 自己先等于"当前程序代码期望的结构"**，否则两边会一起被带偏。
> 本项目**没有任何脚本会自动把 dev 结构同步到 prod** —— 这是发布时的显式步骤，**不得凭记忆跳过、不得默认"上次对齐过"**。
>
> ⚠️ **不要指望 `init_db()` 兜底**：它的运行时迁移只会"加表 / 加列 / 建索引"，**管不到"需要去掉约束"这类既有结构变更**。v1.5.0 就差点踩中：`users.username` 要去掉列级 `UNIQUE`（改成「普通索引 + 活跃行部分唯一索引」），prod 库直到人工核查时仍是旧结构，若直接发布，prod 就会带着旧约束上线（被软删账号的用户名被永久占用，同名重建报 `UNIQUE constraint failed`）。

### 三道关（脚本内置，退出码即判据）

| 关卡 | 工具/命令 | 判据 | 失败含义 |
|------|-----------|------|----------|
| ① **代码 → dev** | `check_db_schema.py` 内置前置检查（自动执行） | dev 必须等于代码 `expected_schema()` / `expected_indexes()` 建出的结构 | dev 库**陈旧**（没跑过迁移）或**含代码已不建的死表**；此时对齐 prod 只会把两边一起带偏 |
| ② **dev → prod** | `python scripts/check_db_schema.py` | 退出码必须为 0 | prod 缺对象或约束不一致 → 必须 `--align` |
| ③ **复验** | `--align` 结束后自动重跑 ② | 退出码必须为 0 | 对齐没生效 ⇒ 仍需人工处理 |

- 关卡 ① **可以用 `--skip-code-check` 跳过，但这是不推荐操作**：跳过就意味着把"dev 是否可信"这件事交给记忆。只有在明确知道 dev 落后于代码、并且只想看 dev/prod 相对差异时才用。
- 关卡 ① 报错时，先让 dev 库跑一次当前代码的迁移再重跑工具：
  ```bash
  cd jflove-server
  venv-win\Scripts\python.exe -c "import asyncio; from src.models.database import init_db; asyncio.run(init_db())"
  ```
  （只加表 / 加列 / 建索引，不删数据；**去掉约束这类变更它做不了**，那属于 ② 的对齐范围。）

### 核对（必做，不可省略）

> ⚠️ 这一步**没有任何自动化兜底**：根 / 模块 `build.py` 与 `.github/workflows/` 都不会调用 `scripts/check_db_schema.py`（CI 只复用 `jflove-server/build.py` 的"prod 库空校验"—— 那只看有没有业务数据，**不看结构**），而 prod 库 `jflove-db/jflove-prod.db` 是随仓库提交的（CI 打镜像用的就是仓库里这份）。**结构核对只能在发布时人工执行**，这正是 v1.5.0 差点漏掉的地方。

```bash
python scripts/check_db_schema.py
```

| 退出码 | 含义 | 处置 |
|--------|------|------|
| **0** | 结构一致 | 可继续后续发布步骤 |
| **1** | 存在结构差异 | **发布阻塞项**：必须对齐完才能继续打包 / 发版 |
| **2** | 环境 / 参数错误（库文件不存在、`--dev` / `--prod` 路径写错、遗留表非空而中止清理等） | 先修环境；**不得**把退出码 2 当成"没差异"放行 |

预期输出（一致时，实测）：

```text
------------------------------------------------------------------------
前置检查：dev 库 vs 当前程序代码的表结构
dev 库与当前代码一致 ✓
========================================================================
dev / prod 表结构核对
  dev  = D:\github\jflove\jflove-db\jflove-dev.db
  prod = D:\github\jflove\jflove-db\jflove-prod.db
========================================================================
结构一致 ✓（发布前检查通过）
```

存在差异时逐条打印（下例为在 prod 副本上删掉 `users_username_active_uidx` 后的实测输出）：

```text
发现 1 处差异：
  [missing_index] users_username_active_uidx
      dev 有、prod 无
      → 按 dev 的索引定义创建

提示：执行 `python scripts/check_db_schema.py --align` 可自动对齐。
```

**判据（以 dev 为准，且只朝"基准要求而目标不满足"的方向判定）**：

| 差异类型 | 含义 | 是否阻塞发布 |
|----------|------|--------------|
| `missing_table` | dev 有表、prod 无 | ✅ 阻塞 |
| `missing_column` | dev 有列、prod 无 | ✅ 阻塞 |
| `missing_index` | dev 有索引、prod 无 | ✅ 阻塞 |
| `table_ddl_mismatch` | **结构化**建表语句不一致（列定义 / 表级约束），**典型就是约束差异**，如 v1.5.0 的 `username UNIQUE` | ✅ 阻塞 |
| `index_ddl_mismatch` | 索引定义不一致 | ✅ 阻塞 |
| `extra_table` / `extra_column` / `extra_index` | prod 有、dev 无（历史遗留） | ⛔ 工具不自动删；人工确认后在发布记录中说明 |

### 历史遗留表（不参与比对）

代码**早已不再创建**的死表（`scripts/check_db_schema.py::_LEGACY_TABLES`，当前含 `notes_permissions`、`sync_configs`）**不参与比对，也不会被复制进 prod** —— 否则工具会把 dev 库里的历史垃圾搬进生产库，还报"结构一致 ✓"（v1.5.0 实测发生过一次）。

- 工具会在输出末尾**显式列出**被忽略的遗留表（含行数），不会静默跳过；
- prod 上若残留且**为空**，可清理：
  ```bash
  python scripts/check_db_schema.py --prune-legacy
  ```
  **非空的遗留表会被拒绝删除**（退出码 2，打印行数）—— 死表也可能在某次老部署里真的存着数据，绝不替用户决定丢弃，需人工确认后手动 `DROP`。

### 对齐方式（二选一）

**方式 A：自动对齐（推荐）**

```bash
python scripts/check_db_schema.py --align
```

- 会**先自动备份** prod 到 `<prod>.bak-align-<时间戳>`（如 `jflove-prod.db.bak-align-20260912-112222`），备份留在同目录，必要时直接还原；
- 只做结构操作：建缺失表 / 索引、加缺失列、按 dev 重建结构不一致的表（**保留全部行与主键 `id`** —— 其它表按 `id` 整数引用，`id` 绝不能变）；
- **不删除** prod 上多出来的表 / 列 / 索引，只报告（避免误删真实数据）；重建表时 **prod 自有的列会连同数据一起保留下来**（把它的列定义拼进新表再拷值）；
- 任何异常**整体回滚**，不留半对齐状态；结束后**自动复验**并打印结果；
- 幂等：对齐后再跑一次 `python scripts/check_db_schema.py` 应为退出码 0。

实测输出（改造过的真实样例：prod 缺表 + 缺列 + 缺索引，且 prod 自带一列 `legacy_flag` 与一张历史表 `legacy_thing`）：

```text
开始对齐 prod 结构到 dev …

已备份 prod → jflove-prod.db.bak-align-20260912-113100
  [+] 建表 media_repair_tasks
  [=] 保留 extra_table legacy_thing（需人工确认）
  [+] 加列 users.notes_path
  [=] 保留 extra_column virtual_disks.legacy_flag（需人工确认）
  [+] 建索引 media_repair_tasks_disk_id_idx
  [+] 建索引 media_repair_tasks_user_id_idx
  [+] 建索引 users_username_active_uidx
  [+] 建索引 users_username_idx

已应用 8 项结构变更
```

**方式 B：手动对齐**

- 执行本版本的升级 DDL：`sqlite3 jflove-prod.db < jflove-db/migrate-<版本号>.sql`（v1.5.0 即 `jflove-db/migrate-v1.5.0.sql`），并准备好对应的 `jflove-db/rollback-<版本号>.sql`；
- 执行完**必须**再跑一次 `python scripts/check_db_schema.py` 复验，退出码 0 才算完成。

**硬约束**：

- 发布前 `python scripts/check_db_schema.py` 退出码**必须是 0**，否则**阻断发布**（与移动端 R8 禁令同级）。
- 只允许上面两条对齐路径；**禁止**用 `PRAGMA writable_schema` 之类的取巧手段直接改写 `sqlite_master`（v1.5.0 实测会留下悬空自动索引，之后任何访问都报 `malformed database schema ... orphan index`，且当场不抛异常）。
- 重建表的操作**必须保留主键 `id`**；拷列时只拷两边共有的列，**且必须把 prod 独有的列一起保留**（工具已按此实现，手写 SQL 时同样要求）。
- 对齐后 prod 仍必须是"无业务数据"状态（见下方「prod 库『空库』校验口径」）——对齐只同步结构，**不得**把 dev 的业务数据带过去。
- **无论用哪种方式，发布记录里必须写明**：执行了哪条命令 / 哪个脚本、对齐**前后**的结构差异、**复验结果**。
- 改过 `scripts/check_db_schema.py` 或做过结构迁移后，**必须**跑通两条反向验证（都要求退出码 0）：
  一条证明"喂坏数据会拦住、喂纯格式差异会放行"，一条证明"对齐不丢数据、不删 prod 自有对象、幂等"。

### 版本内有表结构变更时的必备交付物（强制）

| 交付物 | 要求 |
|--------|------|
| `jflove-db/migrate-<版本号>.sql` | 升级 DDL；**幂等、可重复执行、不触碰任何业务数据**；文件头写清执行方式（`sqlite3 jflove-prod.db < migrate-<版本号>.sql`）与本次变更点（参考 `jflove-db/migrate-v1.5.0.sql` 的写法） |
| `jflove-db/rollback-<版本号>.sql` | 回滚 DDL；**若回滚会因数据冲突失败，必须在脚本里写明回滚前的冲突检查 SQL** —— 参照 `jflove-db/rollback-v1.5.0.sql`：恢复列级 `UNIQUE` 前先跑 `SELECT username, COUNT(*) AS n FROM users GROUP BY username HAVING n > 1;`，命中「同名活跃行 + 已软删除历史行」时先处理历史行（物理删除或改名）再回滚 |
| 发布记录「生产库 DDL（升级+回滚）」章节 | 不止贴脚本内容，还要写**实测结果**：执行的命令 / 脚本、对齐前后的结构差异、复验输出、回滚脚本是否实测过 |

### prod 库「空库」校验口径

`jflove-server/build.py::assert_prod_db_empty()` 在**打镜像前**校验 prod 不含业务数据（`AGENTS.md §5.1`）：

- **允许**：`config` 表存在 `init_db()` 首次运行幂等写入的默认键 —— `media_repair_enabled=0`、`media_repair_allow_transcode=0`（值必须等于默认值，属"初始化数据"）；
- **中止构建**：其余任何表非空，或 `config` 出现白名单外的键 / 被改过的值；
- **发布前若被这条校验拦下，不要绕过校验、不要临时注释代码**，先查清哪张表带了数据、清理生产库后再重跑构建。

## 发布步骤

1. 核查全部交付物归档
2. 版本号单一来源核查：`version.json` 为唯一真相，`python scripts/sync_version.py` 同步全部位置；`python scripts/sync_version.py --check` 校验一致
3. §9 安全发布清单逐条通过
4. **生产库表结构同步（阻塞项，不可省略）**：先 `python scripts/check_db_schema.py`（退出码必须 0）→ 有差异按「生产库表结构同步（发布阻塞项，强制）」章节对齐（`--align` 或本版本 `jflove-db/migrate-<版本号>.sql`）→ 复验退出码 0 后再进入下一步；交付物与发布记录写法见该章节
5. **环境检查**：根 `python build.py -m <模块>` 自动逐模块环境检查（也可人工核对「发布前环境检查」表格），不满足的模块打印原因并跳过
6. **统一打包**：`python build.py -m all`（或 `-m server,desktop,web,app` 指定）
   - 环境不满足的模块会被自动跳过并给出原因，在版本发布记录中标记「❌ 环境未就绪」
   - 移动端若报 Gradle 锁冲突：`pkill -f java` 或 Windows `taskkill /F /IM java.exe` → 删除 `android/.gradle` → 重试
   - 移动端若报 flutter 缓存异常：`cd jflove-app && flutter clean` 后重跑
   - Web 端构建失败排查：`package-lock.json` 是否缺失（先 `npm install`）、Docker daemon 状态
7. 冒烟测试：
    - 启动容器 → 客户端连接 → 密钥交换 → 登录 → 功能抽样
    - **Web 端**：`docker run -p 8080:80 jflove-web:<version>` → 浏览器访问 → 登录页渲染 → SPA 路由 fallback 正常 → **设置页「关于」显示版本号 = <version>**
    - **桌面端**：**装安装包再验**（`JFLove-<version>-win64-setup.exe` 双击 / Linux `sudo dnf install ./jflove-desktop-<version>-1.fc<N>.x86_64.rpm`）→ 从开始菜单/应用菜单启动 → 窗口标题/关于对话框显示版本号 = <version>；便携 zip 与 onedir 目录仅作旁证，**不以它们作为交付形态**
      （若本机无 Inno Setup：`build.py` 只降级出 zip 并打印指引 —— 这是**降级**，不是标准产物）
    - **服务端**：访问 `/docs` 或 `/health` 确认版本号 = <version>
    - **移动端**：debug 和 release 两个 APK 都必须在真机安装并完成：启动 → 登录 → 文件浏览 → 笔记编辑 → 「关于」显示版本号 = <version>
8. 输出 `文档记录/版本发布记录/<版本号>.md`
9. **分叉收尾**：按「发布方式分叉」的选择——选「发布到 Git 仓库」则执行上方「Git 发布操作序列」，并把 `文档记录/版本发布记录/<版本号>.md` 与根 `README.md` 一并提交。打 tag 后 CI 会通过 `release.yml` 自动创建 **Draft Release**（正文含 GHCR 拉取/运行命令 + 各端产物下载说明），desktop/app 产物自动挂为 Assets；devops 需在 Releases 页 review 草稿无误后点 **Publish** 正式发布。选「仅限本地构建与发布」则到此为止，不碰 git。

## 文档更新范围

- 路径：`文档记录/版本发布记录/<版本号>.md`
- 根目录 `README.md`（每次发布后必须同步维护，与版本发布记录一并交付）：
  - 「版本变化」章节：追加本次版本条目，并将 `（当前版本）` 标记移到最新版本
  - 「功能特性」章节：本次新增 / 去除 / 调整的功能同步增删改，按需标注引入版本号（如 `（v1.5.0+）`）
- 必须包含：版本号/发布时间、交付物清单（含 debug + release 两个 APK）、构建产物（路径+校验值）、依赖变更、生产库结构核对与对齐（`check_db_schema.py` 命令 + 对齐前后差异 + 复验结果）、生产库 DDL（升级 + 回滚脚本，并附**实测执行结果**而非只贴脚本内容）、部署步骤（含 Docker 启动命令）、冒烟结果、回滚预案、加密协议版本

## 常见问题（发布相关）

- **Windows GBK 控制台下脚本打印 `✓` / `→` 会抛 `UnicodeEncodeError` 并中断**：发布脚本输出非 ASCII 符号时，GBK 控制台会直接抛异常终止 —— 危险的不是乱码，而是**中断点之后的校验（复验、清理、备份确认）全部不执行**，看起来却像"跑过了"。`scripts/check_db_schema.py` 已显式把 stdout / stderr 重配为 UTF-8 作为示范：

  ```python
  for _stream in (sys.stdout, sys.stderr):
      try:
          _stream.reconfigure(encoding="utf-8", errors="replace")
      except (AttributeError, ValueError):
          pass
  ```

  **新写的发布相关脚本应当照做**（或直接避免在输出里用非 ASCII 符号）。跑既有脚本撞上该报错时，临时兜底可设 `PYTHONIOENCODING=utf-8`（PowerShell：`$env:PYTHONIOENCODING="utf-8"`）。
- **别靠肉眼看建表语句判断约束**：`users` 等表的 DDL 可能带中文注释（如 `-- 去掉了原来的 UNIQUE`），人工比对时容易把注释当成真实定义（v1.5.0 排查时被坑过一次）。`scripts/check_db_schema.py` 会先剥离 `--` / `/* */` 注释、去掉 `IF NOT EXISTS` 与引号差异、按**列名对齐**比较列定义，**以脚本结论为准**。
- **"以 dev 为准"里的 dev 也可能是错的**：dev 库是长期滚出来的，可能**陈旧**（没跑过迁移）或**含代码早已不建的死表**。工具已内置「代码 → dev」前置检查并把死表排除在比对之外（见「三道关」与「历史遗留表」），**不要用 `--skip-code-check` 图省事**；被这两道关拦下时，按提示先修 dev 库或清理遗留表。
- **检查脚本"报通过"不等于真的比过**：任何门禁类脚本改动后都要跑**反向验证**——喂坏数据（少列 / 缺表 / 缺索引 / 列定义变化）必须拦住，喂纯格式差异（表头写法、列顺序）必须放行。工具已配套  与 （依据 `LESSONS.md` L15–L17）。
- **对齐工具本身会"忠实执行错误口径"**：v1.5.0 曾出现"承诺不删 prod 自有列、实际却把该列连数据删掉并报成功"（根因是用建表语句整串文本比较 ⇒ prod 多一列被误判成需要重建表）。现已改为**结构化比较 + 只朝"基准要求"方向判定**，并且重建表时会保留 prod 自有列；改用例前先确认这一点没有被回退。
- **对齐后 prod 结构变了但发布记录没写**：等于没做。对齐命令、前后差异、复验结果三项缺一不可（见「生产库表结构同步」章节的硬约束）。

## 安全宪法

详见 `AGENTS.md §9`。你的角色约束见 §9.6 表格 `devops` 行。发布前额外检查 `_PLAIN_PATHS` 白名单未扩大、`_session_store` 仍为内存字典。
