# 项目工作手册（jflove）

> 本文件是项目的总纲。具体角色的工作细则放在 `.agents/skills/<角色名>/SKILL.md`（DeepSeek Harness 原生技能格式，`skill` 工具直接加载），本文件只做规则定义与角色调度。

## 1. 项目模块与开发状态

| 模块 | 路径 | 说明 | 当前状态 |
| --- | --- | --- | --- |
| jflove-server | `jflove-server/` | 后端服务（FastAPI） | 在研 |
| jflove-desktop | `jflove-desktop/` | 跨平台桌面应用（PySide6 外壳）；界面为 **Web UI**（React + TS 渲染于 QtWebEngine，Python 侧仅原生外壳） | 在研 |
| jflove-web | `jflove-web/` | 浏览器 Web 端（React + TypeScript），支持 PC 端与移动端浏览器双布局 | 在研 |
| jflove-app | `jflove-app/` | 移动端（Flutter + Dart）首版 Android-only，iOS/鸿蒙预留 | 在研 |

**模块边界（强制）**：

- 用户明确要求开发某模块时，只能在该模块目录内编码，不得越界改动其他模块。
- 标记为"暂不开发"的模块，未获用户明确要求一律不动。
- 永远不要自动修改 `.github`、`.vscode`、`.gitignore`、`.git`、`.idea`，除非用户明确要求。

## 1.1 计划先行（强制）

- **任何任务开始前，必须先列出完整计划**（每个步骤的具体内容、涉及的文件、依赖关系），经用户确认无误后再逐步执行；
  **禁止跳过计划步骤直接编码** —— 即使小改动也要先说明改什么、改哪里、为什么改。
- **长对话要主动做阶段性总结并重新对齐计划**，防止上下文截断导致工作不稳定。

### 1.1.1 计划文件管理（强制）

- **所有计划写入 `plans/`**，一个任务一个文件，命名 `plans/<模块>-<任务简述>.md`（如 `plans/server-bugfix-login-ttl.md`）。
- **每份计划必须含**：任务目标 / 完成状态（`[x]` 已完成、`[-]` 进行中、`[ ]` 待完成）/ 涉及的源文件清单 / 依赖关系。
- **中断与续接**：每次开工先读 `plans/` 下最近相关的计划文件对齐进度；过程中及时更新状态，保证新对话也能准确续接。
- **一个计划只覆盖一个任务范围**，并行任务各建各的文件，不混写；完成即加 `DONE-` 前缀或移入 `plans/归档/`。

**`plans/` 的定位与使用边界（强制）**：

> **`plans/` 是「本次版本工作」的临时计划，属于本地草稿**（`.gitignore` 整个目录排除），
> **不跨设备同步、不是长期文档、不是对外契约**。

1. **受控文件一律不得按路径引用 `plans/` 下的具体文件** —— 包括源码、打包资产、CI 配置、
   以及 `文档记录/` 下的正式文档。理由：`plans/` 不入库 ⇒ 换机器 / 新克隆后**引用必然悬空**，
   等于把"单一真相"放在一台机器上。
   - **需要长期留存的结论写进 `文档记录/`**（需求 / 设计 / 开发记录 / 审查 / 测试 / 发布记录）
     或本文件；计划里只留过程与勾选状态。
   - 代码注释要说明设计依据时，引用**入库的**设计文档（如 `文档记录/设计文档/<版本>.md`）
     或直接引 `AGENTS.md` 章节号，不要引 `plans/`。
2. **计划不承担"续接入口"职责**。跨会话 / 跨设备的状态延续靠**入库的**开发记录
   （`文档记录/<模块>开发记录/<版本号>.md`）；换机器后按 §7.4 先读 `文档记录/` 与
   `plans/` 下**本机尚存**的计划，两边对不上时**以 `文档记录/` 为准**。

## 2. 技术栈

| 模块 | 语言 | 框架 / 关键库 | 详细技术栈 |
|------|------|-------------|-----------|
| jflove-server | Python 3.14+ | FastAPI + SQLite3 + PyJWT(ES256) + cryptography | `backend/SKILL.md` |
| jflove-desktop | Python 3.14+ | PySide6 6.11 + QtWebEngine（界面为 Web UI：React + TS + Vite + Tailwind，与 Web 端同源快照、独立演进） + requests | `cross-platform-desktop/SKILL.md` |
| jflove-web | TypeScript 5.7+ | React 18 + Vite 6 + Tailwind CSS 4 + Zustand 5（浏览器 Web 端；加密走 Web Crypto API + @noble/ciphers） | `web-frontend/SKILL.md` |
| jflove-app | Dart 3.12+ | Flutter 3.44 + Riverpod + dio + pointycastle | `cross-platform-mobile/SKILL.md` |

> **加密协议三端统一**：X25519 ECDH + ChaCha20-Poly1305 + HKDF-SHA256（盐 `b"jflove-v1"`，32B）。

## 3. 项目目录结构

> **各模块的目录树 / 分层 / 文件清单，真身在各角色 `SKILL.md`（见 §6 角色表），本文件不重复展开。**
> 下面只保留「跨模块必知」的顶层排布与易踩的排布陷阱。

| 模块 | 顶层排布 | 排布陷阱（会踩到的） |
| --- | --- | --- |
| jflove-server | `src/`（controllers → services → repositories → models / utils / config / main.py）+ `tests/` + `build.py` + `requirements.txt` | `venv-win/`（Linux `venv-linux/`）与 `build/` 不入库、不要提交 |
| jflove-desktop | `src/`（原生外壳 `ui/` / 桥 `bridge/` / `components/` / `services/` / `utils/` / `config/` / `main.py`）+ **前端工程 `ui/`**（React+TS+Vite+Tailwind）+ `packaging/` + `build.py` | **源码树里没有 `webui/`** —— 它只是 `build.py` 把 `ui/dist/` 打进包内的资源目录名；`ui/dist/` 是构建产物 |
| jflove-app | `lib/`（main.dart / app.dart / config / models / providers / services / pages / widgets / utils）+ `test/` + `android/` + `assets/` + `pubspec.yaml` | 模块内**没有** `build.py`（构建入口在仓库根）；`integration_test/`、`ios/`、`ohos/` 是**预留**方向，仓库内**暂无**这些目录 |
| jflove-web | `src/`（main.tsx / App.tsx / config / layouts / pages / components / hooks / services / stores / utils / types）+ `tests/` + `Dockerfile` + `nginx.conf` + `vite.config.ts` | Tailwind CSS 4 为 **CSS-first**：工程内**没有** `tailwind.config.ts`，主题与工具类由 `@tailwindcss/vite` + `src/index.css` 承担 |

## 3.1 开发环境变量约定

以下为系统级环境变量，**所有角色统一使用**，不在 SKILL.md 中硬编码路径。

| 变量名 | 值 | 用途 |
|--------|-----|------|
| `FLUTTER_HOME` | `D:\flutter\flutter_3.44.6-stable` | Flutter SDK 根目录，用于移动端开发 |
|  | `%FLUTTER_HOME%\bin` 已追加到 `PATH` | 确保 `flutter` / `dart` 命令可直接执行 |

新增 Flutter/Dart SDK 后依此模式添加 `FLUTTER_HOME` 及其 `bin` 目录到 PATH。

### Python venv 路径约定

后端与桌面端遵循统一 venv 路径模式（各 SKILL.md 不再重复描述）：
- **Windows**：`<模块>/venv-win/Scripts/python.exe`
- **Linux**：`<模块>/venv-linux/bin/python`

自测命令（统一使用对应平台的 Python 解释器）：
- flake8：`python -m flake8 src/ tests/ --max-line-length=99`
- pytest：`python -m pytest tests/ -v`

---

### 文档目录（统一在仓库根 `文档记录/` 下）

```text
文档记录/
├── 需求文档/              # product 输出
├── 设计文档/              # designer 输出
├── 后端开发记录/           # backend 输出
├── 桌面端开发记录/         # cross-platform-desktop 输出
├── 移动端开发记录/         # cross-platform-mobile 输出
├── Web端开发记录/          # web-frontend 输出
├── 代码审查报告/           # code-review 输出
├── 测试报告/              # testing 输出
├── 项目管理记录/           # pmo 输出（含"下个版本候选台账"小节，见下）
└── 版本发布记录/           # devops 输出
```

> **⚠ 开工新版本前先读**：`文档记录/项目管理记录/<最新版本号>.md` 的「**下个版本候选台账**」小节
> （技术债 / 下个版本候选的唯一去处）。每条都带**证据（文件:行 + 命令）+ 建议做法 + 验收判据**，
> 避免下个版本重新取证；版本内计划仍按 §1.1.1 放 `plans/`。

## 4. 通用编码规范

### 4.1 命名

- 类 / 接口：PascalCase
- 方法 / 变量：camelCase
- 常量：UPPER_SNAKE_CASE
- 文件：kebab-case

### 4.2 代码风格

- 遵循 SOLID、DRY；合理应用设计模式，避免过度设计
- 使用类型注解，确保类型安全
- 关键逻辑、接口、方法、字段必须有**中文注释**
- 遵循 PEP 8，flake8 必须通过
- 提交给用户的代码必须能编译运行；耗时操作异步化

### 4.3 安全

- 前后端通过 RESTful API 通信，遵循 OpenAPI 规范
- 受保护接口必须使用 JWT 鉴权（ES256）
- 敏感数据使用 cryptography 加密（对称 ChaCha20-Poly1305 / 非对称 ECDH X25519）

## 5. 数据库规范

### 5.1 库使用

- 开发：`jflove-db/jflove-dev.db` —— 开发期间**只允许**操作此库
- 生产：`jflove-db/jflove-prod.db` —— 仅在发布时同步**表结构**，**不同步业务数据**（初始化数据除外）

#### 5.1.1 结构唯一真相与发布前强制核对（强制）

**`jflove-dev.db` 是表结构基准**；`jflove-prod.db` 的结构必须与它对齐，且 **dev 自己
必须先等于"当前程序代码期望的结构"**（否则两边会一起被带偏）。
**没有任何脚本会自动把 dev 结构同步到 prod** —— 这是发布时的显式步骤。

发布前**必须**执行（devops 技能发布步骤内；退出码即判据）：

| 关卡 | 命令 | 判据 | 失败含义 |
| --- | --- | --- | --- |
| ① 代码 → dev | `check_db_schema.py` 内置前置检查（自动） | dev 等于代码 `expected_schema()` / `expected_indexes()` 建出的结构 | dev **陈旧**或**含代码已不建的死表**，直接对齐会把 prod 带偏 |
| ② dev → prod | `python scripts/check_db_schema.py` | **退出码 0** | **退出码 1 = 发布阻塞项**：prod 缺对象或约束不一致 → 必须 `--align` |
| ③ 复验 | `--align` 后自动重跑 ② | 退出码 0 | 对齐没生效 ⇒ 仍需人工处理（退出码 2 = 环境/参数错误，**不得当成"没差异"放行**） |

- 判据是**以 dev 为准**，且只朝"**基准要求而目标不满足**"的方向判定：
  dev 有而 prod 无的对象必须补齐；**prod 多出来的表/列/索引一律只报告、不自动删**，
  也**不允许**因为它们的出现而触发"重建表"（重建会连数据一起丢）。
- **遗留表不参与比对**（`scripts/check_db_schema.py::_LEGACY_TABLES`）：代码已不创建的表
  **不会被复制进 prod**；prod 残留空表可 `--prune-legacy` 清理。
- **不要指望 `init_db()` 兜底**：它的运行时迁移只会"加表 / 加列 / 建索引"，
  **管不到"需要去掉约束"的既有结构变更**；也不能用它来替代发布前的核对。
- 对齐方式与结果（含命令、前后差异、复验）必须写进 `文档记录/版本发布记录/<版本号>.md`
  的「生产库 DDL（升级+回滚）」章节。

> 命令细节、差异类型判据、对齐方式（`--align` / 手写 DDL）、历史遗留表与空库校验口径，
> 见 `.agents/skills/devops/SKILL.md`「生产库表结构同步」章节。
> 修改过本工具或做过结构迁移后，**必须**跑一遍反向验证（喂坏数据要拦住、喂纯格式差异要放行），
> 复验退出码都要为 `0` 才算工具可信（依据：`.agents/skills/lessons/LESSONS.md` L15–L17）。


#### 5.1.2 版本内有表结构变更时的必备交付物（强制）

| 交付物 | 要求 |
| --- | --- |
| `jflove-db/migrate-<版本号>.sql` | 升级 DDL；**幂等、可重复执行、不触碰业务数据** |
| `jflove-db/rollback-<版本号>.sql` | 回滚 DDL；若回滚会因数据冲突失败，**必须在脚本里写明回滚前的冲突检查 SQL** |

#### 5.1.3 生产库"空库"校验

`jflove-server/build.py::assert_prod_db_empty()` 在打镜像前校验 prod **不含业务数据**：

- **允许** `config` 表存在 `init_db()` 写入的默认键
  （`media_repair_enabled=0`、`media_repair_allow_transcode=0`，属 §5.1 的"初始化数据"）；
- 其余任何表非空、或 config 出现白名单外的键/被改过的值 → **构建中止**；
- 被拦时**不要绕过校验**，先清理生产库。

### 5.2 表设计

- 表名：复数 + snake_case
- 字段名：snake_case，避免保留字
- 主键：`id`，自增整数
- 外键字段命名：`{related_table}_id`（**禁止建外键约束**）
- 索引：`{field_name}_idx`
- 必备字段（每张表都要有）：`id`、`created_at`、`updated_at`、`deleted_at`（软删除）

## 6. 角色与命令对应表

**技能调用（强制）**：命中下表任一角色时，必须**先调用 `skill` 工具加载同名技能**（技能名 = 角色名），
再开始该角色的工作；并在回复开头说明本次命中了哪些技能。

> 需要跨角色通用经验时加载 `lessons`（= `.agents/skills/lessons/LESSONS.md`，踩坑记录 + 交付前自查清单）。
> 技能目录里看不到项目技能时，按 §6.2 第 3 条的退化路径直接 `read` 对应 `SKILL.md`——**约束不因此放松**。

| 角色（agent） | 命令文件 | 模块归属 | 输出文档目录 |
| --- | --- | --- | --- |
| product | `.agents/skills/product/SKILL.md` | 产品 | `文档记录/需求文档/` |
| designer | `.agents/skills/designer/SKILL.md` | 架构设计 | `文档记录/设计文档/` |
| backend | `.agents/skills/backend/SKILL.md` | jflove-server | `文档记录/后端开发记录/` + `jflove-server/README.md` |
| cross-platform-desktop | `.agents/skills/cross-platform-desktop/SKILL.md` | jflove-desktop | `文档记录/桌面端开发记录/` + `jflove-desktop/README.md` |
| web-frontend | `.agents/skills/web-frontend/SKILL.md` | jflove-web | `文档记录/Web端开发记录/` + `jflove-web/README.md` |
| cross-platform-mobile | `.agents/skills/cross-platform-mobile/SKILL.md` | jflove-app | `文档记录/移动端开发记录/` + `jflove-app/README.md` |
| code-review | `.agents/skills/code-review/SKILL.md` | 跨模块审查 | `文档记录/代码审查报告/` |
| testing | `.agents/skills/testing/SKILL.md` | 跨模块测试 | `文档记录/测试报告/` |
| pmo | `.agents/skills/pmo/SKILL.md` | 项目管理 | `文档记录/项目管理记录/` |
| devops | `.agents/skills/devops/SKILL.md` | 构建发布 | `文档记录/版本发布记录/`（根 `README.md` 只维护「功能特性」，**不再维护版本历史**，见 §8） |

**调度原则**：

- 角色定位、行为规范、技术栈细节均以 `.agents/skills/<角色名>/SKILL.md` 为准；本文件不重复展开。
- 命中具体流程后，必须在该流程内工作，不得越界处理其他流程的事。

### 6.1 子 agent 委派规约（强制）

> **完整契约见 `subagent-delegation` 技能**（`.agents/skills/subagent-delegation/SKILL.md`，派活前 / 接活后必读）。
> 本节只留**不可变的两条事实**与**模板**，细则不在此重复。

- **事实①**：子 agent **确实**继承了本文件（以 `Instructions from: AGENTS.md` 注入），但注入语气的效力只是
  *"may be relevant … as guidance when applicable"*，**弱于**父 agent 在委派 prompt 里的显式要求。
- **事实②**：DSH 的 `subagent` / `subagent_fork` / `workflow` **没有权限参数**，写盘边界无法在工具层硬隔离。
  **实测反例**：prompt 里写明「严禁创建、修改、删除任何文件」的探针子 agent，**仍然写了文件**。
  ⇒ 只在 prompt 里写"禁止"不牢靠，**必须配「委派前后各跑一次 `git status --porcelain`」的事后核对**。

**技能与记忆的编写原则（强制）**：

- **只写"当前是什么、怎么判"，不写"以前是什么、哪个版本改的、谁推翻过什么"。**
  历史沿革属于 `文档记录/`（版本发布记录 / 开发记录），技能与记忆**没有历史版本，也不记历史版本**。
  参考手册里每多一段沿革，就多一份**永久 token 成本**并稀释真正要执行的内容。
- **唯一例外**：该信息能**防止错误搜索或错误假设**时保留，如「`src/sw/` 不存在，不要去找」。
  自检判据：*删掉它，读者会不会去翻不存在的东西？* 会 → 留；不会 → 删。
- 删除沿革时**不要把规则一起删掉**：只压缩"为什么当初这么定"的叙述，保留可执行判据。

**父 agent 五条不变量**：① 首段**点名角色**并要求 `skill` 加载 + `read` 真身；② prompt **自包含**
（目标 + 可判定判据 + 目录白名单/黑名单 + 必读清单 + 产出路径）；③ 只读任务**写明只读**；
④ 事后核对 `git status --porcelain`，越界产物立即清理；⑤ 回执**带证据**（文件:行 / 命令 / 输出）并核对后才采信。
**子 agent 三条不变量**：① **禁止嵌套委派**；② 模块边界同等有效（§1）；③ 回执四段式
（结论 → 证据 → 未决问题 → 是否越界写盘）。

### 6.2 技能组织与发现（DSH 原生格式）

1. **唯一真相 = `.agents/skills/<技能名>/SKILL.md`**（DSH **工作区级**扫描根，**热扫描**：新增 / 改名 / 删除立即生效）。
   形态只认顶层 `<name>/SKILL.md` 或平铺 `<name>.md`，**不递归**；项目根 = 含 `.git` 的最近祖先。
   技能名 = **角色名**（`backend` / `code-review` / …），另有 `lessons`（经验库）与 `subagent-delegation`（委派契约）。
   扫描根优先级（rank 小者优先）：`<项目根>/.dsh/skills` → `<项目根>/.agents/skills` → profile `customSkillDirs`
   → `<dshHome>/skills` → 随包内置。**规则存两处必然漂移，不要引入任何"指路代理"层。**
2. **frontmatter 必须合法 YAML**：必填 `name`（kebab-case，**与目录名一致**）+ `description`；
   `description` 含 `: `（如 `Use when: …`）时**必须加双引号**，否则 DSH **静默丢弃整个技能**
   （技能目录查不到、`skill("<name>")` 只回 `unknown or no longer available`，**不报错不告警**）。
   校验（退出码即判据，**必须用带 PyYAML 的解释器**，PATH 上的 `python` 没有 PyYAML 会退化为形状检查并打 `[WARN]`）：

   ```bash
   jflove-server\venv-win\Scripts\python.exe scripts\check_skills.py
   ```
3. **技能不可加载时的退化路径（必须走）**：`read .agents/skills/<技能名>/SKILL.md` 全文。
   `.agents/skills/**` 永远是**唯一真相**；技能目录只是入口 —— 换机器 / 新克隆 / 清理后**照样按本节执行**。
4. **大文件技能按条目读**：`lessons` 入口是 `.agents/skills/lessons/SKILL.md`，按其场景索引**只读相关条目**，
   不要整篇塞进上下文。

### 6.3 委派 prompt 模板（复制即用）

```text
角色：<role>（先调用 skill 工具加载 `<role>`，再 read `.agents/skills/<role>/SKILL.md` 全文并遵守）

任务：<一句话目标>
背景：<为什么做 / 对应需求或设计文档路径>
必须先读：<文件清单>
允许改动：<目录白名单>            禁止改动：<目录黑名单>
验收判据：<可判定的条件，如"命令 X 退出码 0"、"文件 Y 含 Z">
产出：<文档路径 / 代码路径>        写盘约束：<只读 或 仅允许写上述白名单>
回执格式：1) 结论 2) 证据（文件:行 / 命令 / 输出）3) 未决问题 4) 是否越界写盘
```

### 6.4 落地自查（"写进文档"不等于"被执行"）

每次委派结束后，父 agent 自查（依据 `LESSONS.md` L12 / L43 / L44）：

- [ ] 委派 prompt 首段**点名了角色**，并要求 `skill` 加载 + `read` 真身？
- [ ] 给了**目录白名单**与**可判定的验收判据**？
- [ ] 委派前后 `git status --porcelain` 核对过，越界产物已清理？
- [ ] 子 agent 回执**带证据**，且父 agent 核对后才采信？

## 7. 版本迭代流程（核心流水线）

> **每个版本迭代必须按以下固定阶段顺序执行，前一阶段完成后才能进入下一阶段。**

### 7.1 完整流水线

```
Phase 1              Phase 2              Phase 3
┌──────────┐        ┌──────────┐        ┌──────────┐
│ 📋 需求编写 │   →   │ 🏗️ 设计文档 │   →   │ 🔧 后端开发 │
└──────────┘        └──────────┘        └──────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │ 选择开发目标        │
                                    ├──────────────────┤
                                    │ Phase 4a: 🖥️ 桌面端 │
                                    │ Phase 4b: 📱 移动端 │
                                    │ Phase 4c: 🌐 Web端  │
                                    │ （可并行或多选）     │
                                    └────────┬─────────┘
                                             │
                                             ▼
Phase 8              Phase 7              Phase 6              Phase 5
┌──────────┐        ┌──────────┐        ┌──────────┐        ┌──────────┐
│ 🚀 版本发布 │   ←   │ 📊 PMO管理  │   ←   │ 🧪 测试报告 │   ←   │ 🔍 代码审查 │
└──────────┘        └──────────┘        └──────────┘        └──────────┘
```

### 7.2 各阶段详细说明

| 阶段 | 触发关键词 | 角色 | 输入 | 产出 |
|------|-----------|------|------|------|
| **Phase 1** 需求编写 | "产品设计" / "开启新版本" | product | 用户原始需求 | `文档记录/需求文档/<版本号>.md` |
| **Phase 2** 设计文档 | "技术设计" | designer | 需求文档（Phase 1） | `文档记录/设计文档/<版本号>.md` |
| **Phase 3** 后端开发 | "后端开发" | backend | 设计文档（Phase 2） | 后端代码 + `文档记录/后端开发记录/<版本号>.md` |
| **Phase 4a** 桌面端开发 | "桌面端开发" | cross-platform-desktop | 设计文档 + 后端记录 | 桌面端代码 + `文档记录/桌面端开发记录/<版本号>.md` |
| **Phase 4b** 移动端开发 | "移动端开发" | cross-platform-mobile | 设计文档 + 后端记录 | 移动端代码 + `文档记录/移动端开发记录/<版本号>.md` |
| **Phase 4c** Web 端开发 | "Web端开发" / "前端开发" | web-frontend | 设计文档 + 后端记录 | Web 端代码 + `文档记录/Web端开发记录/<版本号>.md` |
| **Phase 5** 代码审查 | "代码审查" | code-review | 所有代码变更 + 需求/设计文档 | `文档记录/代码审查报告/<版本号>.md` |
| **Phase 6** 测试 | "测试" | testing | 需求/设计文档 + 代码 | 补全测试用例 + `文档记录/测试报告/<版本号>.md` |
| **Phase 7** 项目管理 | "项目管理" | pmo | 版本范围与任务列表 | `文档记录/项目管理记录/<版本号>.md` |
| **Phase 8** 版本发布 | "发布" | devops | 审查/测试通过（Phase 5+6） | 构建产物 + `文档记录/版本发布记录/<版本号>.md` |

### 7.3 各阶段关键步骤

#### Phase 1：需求编写

1. 确认本次版本号（遵循 §8 版本号策略）
2. 多轮对话挖掘需求，明确功能边界
3. 边聊边写 `文档记录/需求文档/<版本号>.md`，列出与上一版本的差异
4. 明确验收标准（AC-1 ~ AC-n）
5. 用户确认收尾

#### Phase 2：设计文档

1. 对齐最新版需求文档作为基线
2. 设计系统架构、数据库表结构、API 接口
3. 对照 §9 安全宪法逐接口标注鉴权方式、加密策略
4. 输出 `文档记录/设计文档/<版本号>.md`，包含 Mermaid 架构图
5. 用户确认收尾

#### Phase 3：后端开发

1. 阅读最新设计文档 + 上一版本代码审查/测试报告
2. 在 `jflove-server/` 内开发，不越界
3. 自测：flake8 0 警告 + pytest 全通过
4. 更新 `文档记录/后端开发记录/<版本号>.md` 与 `jflove-server/README.md`

#### Phase 4a：桌面端开发

1. 阅读最新设计文档 + 最新后端开发记录
2. 在 `jflove-desktop/` 内开发，不越界
3. 自测：flake8 0 警告 + pytest 全通过
4. 更新 `文档记录/桌面端开发记录/<版本号>.md` 与 `jflove-desktop/README.md`

#### Phase 4b：移动端开发

1. 阅读最新设计文档 + 最新后端开发记录
2. 在 `jflove-app/` 内开发，不越界
3. 自测：`dart analyze lib/` 零错误 + `flutter test` 全通过
4. 更新 `文档记录/移动端开发记录/<版本号>.md` 与 `jflove-app/README.md`

#### Phase 4c：Web 端开发

1. 阅读最新设计文档 + 最新后端开发记录
2. 在 `jflove-web/` 内开发，不越界
3. 自测：`npm run lint` 零警告 + `npm run test` 全通过
4. 更新 `文档记录/Web端开发记录/<版本号>.md` 与 `jflove-web/README.md`

#### Phase 5：代码审查

1. 对照最新需求/设计文档逐文件审查
2. **不修改代码**，仅输出审查结论
3. 对照 §9 安全宪法逐条核查，违反标记严重/中等
4. 输出 `文档记录/代码审查报告/<版本号>.md`

#### Phase 6：测试

1. 对齐最新需求/设计文档
2. 在 `tests/` 下补全 pytest 用例
3. **必须包含 §9 安全宪法要求的全部安全用例**（任一缺失或失败即视为不完整，不得出报告）
4. 输出 `文档记录/测试报告/<版本号>.md`

#### Phase 7：项目管理

1. 确认版本号与范围
2. 拆解任务并标注角色
3. 跟踪状态/风险/里程碑
4. 维护 `文档记录/项目管理记录/<版本号>.md`

#### Phase 8：版本发布

0. **先问用户：本次是「真实发布」还是「仅本地构建」？**（强制，不得自行假定）

   | 用户选择 | 该怎么做 |
   | --- | --- |
   | **仅本地构建** | 只调用该模块的 `build.py`（如 `python build.py -m desktop`）。**不做** tag / push / Release，也不写发布记录 |
   | **真实发布** | **本地不打包**（不要跑 `build.py -m all`）。只产出 `文档记录/版本发布记录/<版本>.md` → commit → 打 tag `v<版本>` 并 push → **由 GitHub Actions 在云端构建并创建 Release**，产物与哈希**以 CI 为准** |

   > 为什么强制先问：一旦方向搞错，代价是双重的 —— 既白跑几分钟、产出 GB 级本地垃圾，
   > 又会把**本地构建的哈希**写进发布记录（与 CI 产物不符，让人无法核对）。

1. 校验审查报告（Phase 5）和测试报告（Phase 6）均已通过
2. 版本号单一来源核查：`version.json` 为唯一真相，`python scripts/sync_version.py` 同步全部位置（含移动端 versionCode 派生）
3. **核对并对齐生产库表结构**（阻塞项）：`python scripts/check_db_schema.py`（退出码必须为 0；工具会先自动做「代码 → dev」前置检查）→ 有差异则 `python scripts/check_db_schema.py --align` 对齐 → 复验；prod 残留空的历史遗留表可用 `--prune-legacy` 清理；做法与交付物见 §5.1.1 / §5.1.2
4. **构建**（按第 0 步的选择，二者不混用）：
   - **仅本地构建** → `python build.py -m all` 或 `-m <模块>`（统一入口，自动同步版本 + 逐模块环境检查 + desktop 切 venv）
   - **真实发布** → **不在本地构建**；推 tag 后由 `.github/workflows/build-*.yml` 在云端构建并挂到 Release
   - 产物：服务端/Web 端 Docker 镜像、桌面端 **onedir 目录 + 安装包**（Windows `*-setup.exe` + 便携 `*-portable.zip`、Linux/Fedora `*.rpm`）、移动端 debug+release 两个 APK
5. 执行冒烟测试
6. 输出 `文档记录/版本发布记录/<版本号>.md`（**它是版本历史的唯一去处** —— 根 `README.md` 已移除「版本变化」章节，
   只有「功能特性」需要按新增/去除/调整同步维护，且**不写具体版本号字面量**，避免每版改 README）

### 7.4 全局规则

- 对话开始主动确认用户意图；已说明则直接进入对应流程。
- 开始任何任务前，先阅读项目根 `README.md` 与本文件 `AGENTS.md`。
- 命中某个阶段后，只做该阶段的事；交叉需求需先得到用户确认。
- **严格顺序执行**：前一阶段完成后才能进入下一阶段。如果跳过某个阶段，后续阶段必须补上。

### 7.5 BUG 修复分流流程

> 用户使用产品后反馈问题时，AI 必须先做**问题分类**，再决定执行路径。不能无条件走完整版本迭代流程。

#### 7.5.1 决策树

```
用户反馈问题
     │
     ▼
┌────────────────┐
│ 这是产品体验问题    │
│ 还是程序 BUG？     │
└───┬────────┬───┘
    │        │
    ▼        ▼
产品体验     程序 BUG
    │        │
    │        ▼
    │   ┌──────────────────┐
    │   │ BUG 根因是哪一层？   │
    │   └──┬───┬───┬──────┘
    │      │   │   │
    │      ▼   ▼   ▼
    │   需求  设计  代码
    │   BUG  BUG  BUG
    │
    ▼
┌──────────────────┐          ┌──────────────────┐
│ 走完整版本迭代流程   │          │ 走完整版本迭代流程   │
│ Phase 1 → Phase 8 │          │ Phase 1 → Phase 8 │
│ (新小版本号)        │          │ (当前版本号)        │
└──────────────────┘          └──────────────────┘

                                    ［只有代码 BUG］
                                        │
                                        ▼
                              ┌──────────────────────┐
                              │ 快速修复通道            │
                              │ Phase 3/4 → 5 → 6 → 8 │
                              │ 跳过：需求/设计/PMO     │
                              │ 版本号：当前版本号       │
                              └──────────────────────┘
```

#### 7.5.2 分类标准

| 类别 | 子类 | 典型特征 | 处理路径 | 触发关键词示例 |
|---|---|---|---|---|
| **产品体验** | — | "这里应该可以点击" / "流程太复杂" / "如果支持拖拽就更好了" / "这个提示不够友好" | **完整版本迭代**（新小版本号，Phase 1→8） | "产品体验" / "功能建议" / "交互优化" |
| **程序 BUG** | 需求 BUG | 需求文档与预期行为描述不符、遗漏边界条件 | **完整版本迭代**（当前版本号，Phase 1→8） | "按需求说应该…但实际…" |
| | 设计 BUG | 设计文档中的架构、接口、表结构与需求不一致、遗漏鉴权标注 | **完整版本迭代**（当前版本号，Phase 1→8） | "接口设计有问题" / "数据库表结构不对" |
| | 代码 BUG | 代码实现未遵循设计文档、逻辑错误、崩溃、异常未处理、安全漏洞 | **快速修复通道**（当前版本号，跳过 Phase 1/2/7） | "报错" / "崩溃" / "闪退" / "返回不对" / "这个 BUG" |

#### 7.5.3 快速修复通道（代码 BUG）

当判定为**纯代码 BUG**（需求正确、设计正确、仅实现有误）时，按以下精简流程：

```
触发：用户报告代码 BUG
  │
  ▼
Step 1：确认 BUG 根因（必须定位到具体文件/函数/行）
  │
  ▼
Step 2：调用 backend / cross-platform-desktop / cross-platform-mobile 技能修复代码
  │
  ▼
Step 3：代码审查（code-review）
  │  └─ 仅审查 BUG 修复涉及的变更文件
  │
  ▼
Step 4：测试（testing）
  │  └─ 补全 BUG 相关的回归用例 + 安全用例
  │
  ▼
Step 5：发布（devops）
  │  └─ 校验审查/测试通过 → flake8 + pytest（后端/桌面端）或 dart analyze + flutter test（移动端）+ 构建 + 冒烟
  │
  ▼
完成（版本号不变，记录在对应版本的开发记录中）
```

**快速修复通道规则**：

- **版本号不变**：纯代码 BUG 修复不产生新版本号，直接更新当前版本
- **跳过 Phase 1（需求）**：需求文档无变动
- **跳过 Phase 2（设计）**：设计文档无变动
- **跳过 Phase 7（PMO）**：不重新拆解任务
- **必须保留 Phase 5/6/8**：代码审查 + 测试 + 发布一个不能少
- **开发记录**：在 `文档记录/后端开发记录/<版本号>.md` 或 `桌面端开发记录/<版本号>.md` 或 `文档记录/移动端开发记录/<版本号>.md` 中追加 BUG 修复记录（标注"BUG 修复"标签）

### 7.6 版本迭代前置（通用规则）

> **适用于所有开发、审查、测试角色。在开始本版本工作前，必须先阅读上一版本的代码审查报告和测试报告：**
> - 「严重」和「中等」级别的问题列为**必须修复**项，不得遗漏
> - 「轻微」问题酌情修复
> - 开发记录 / 审查报告 / 测试报告中逐条注明每个问题的处理方式（已修复 / 已忽略及原因）

## 8. 文档与版本管理

### 8.1 版本号单一来源（强制）

- **唯一真相 = 仓库根 `version.json`**；其余位置**全部由 `python scripts/sync_version.py` 派生/同步**，
  **各角色禁止手动改任何模块内的版本号字段**。改版本只有一个动作：
  `echo '{"version":"x.y.z"}' > version.json && python scripts/sync_version.py`。
- **完整位置清单以 `scripts/sync_version.py::POSITIONS` 为准**（当前 9 处，按 scope 归属）：

  | scope | 位置 | 数量 |
  | --- | --- | --- |
  | server | `src/main.py`（`/docs` 显示）、`Dockerfile`（`LABEL version`） | 2 |
  | desktop | `src/config/settings.py`、**`ui/src/config/constants.ts`**（用户可见：登录页页脚 / 设置-关于）、`ui/package.json` | 3 |
  | web | `package.json`、`src/config/constants.ts`（设置页-关于显示） | 2 |
  | app | `pubspec.yaml`（`version: x.y.z+<code>`）、`lib/pages/settings/settings_page.dart`（关于页显示） | 2 |

  > 移动端 versionCode `+<code>` **由版本号派生**：`major*1e6 + minor*1e3 + patch`，不允许单独维护。
- **校验**：`python scripts/sync_version.py --check`（不一致非 0 退出）；server / desktop / web 三个模块
  `build.py` 在构建前校验模块内版本号，**不一致直接中止构建**；CI 从 tag 提取版本号并校验
  `tag == version.json` 与上述全部位置。各模块**只管自己那几处**，细则见对应 `SKILL.md`。
- 版本号策略：主版本号代表重大变更，次版本号代表小迭代；每个版本号对应一份独立文档，新版本必须列出与上一版本的差异。

### 8.2 打包与交付形态（强制）

- **统一打包入口**：本地打包一律走根 `python build.py`（`-m` 参数或交互多选），**禁止**直接调用各模块 build.py / flutter build。
- **桌面端交付形态 = 安装包（强制）**：任何触发路径（根入口 / 模块 `build.py` / 交互式多选 / GitHub Actions）都必须以安装包为标准产出，**不得以单体 exe 交付**：
  - Windows：`JFLove-<ver>-win64-setup.exe`（Inno Setup；另附便携 `*-portable.zip` 与 onedir 目录）
  - Linux/Fedora：`jflove-desktop-<ver>-1.fc<N>.x86_64.rpm`
  - `--mode onefile`（单体 exe）仅为兼容保留的**显式**选项，禁止作为默认或交付形态
  - 工具链缺失时**降级但必须响亮报警**（Inno Setup 缺失 → 只出 zip；rpmbuild 缺失 → 只出 onedir），
    且**不得把降级产物当成标准交付**
  - CI 侧由 `build-desktop.yml` 的 `if-no-files-found: error` 强制：缺 `*setup.exe` / `*.rpm` 时该 job 直接失败
  - 原因：onefile 每次启动要解压数百 MB（实测「启动→首窗可见」中位 9.58s → onedir 1.31s，快约 7.3×），
    且 onedir 才配得上"正常安装模式"（开始菜单 / 卸载器）

### 8.3 文档职责边界（避免同一内容多处维护）

| 内容 | 唯一去处 | 谁维护 |
| --- | --- | --- |
| 版本历史（每版改了什么、产物与校验值） | `文档记录/版本发布记录/<版本号>.md` | devops |
| 当前能力清单（不带版本号） | 根 `README.md`「功能特性」 | devops（发布时按需增删） |
| 各模块技术文档（架构 / 启动 / 路由表 / 已知限制） | 各模块 `README.md` | 对应开发角色 |
| 跨版本技术债 / 下个版本候选台账 | `文档记录/项目管理记录/<最新版本>.md` 的「下个版本候选台账」小节 | pmo |

- **根 `README.md` 不维护版本历史**：版本历史统一看 `文档记录/版本发布记录/`，发布产物看 GitHub Releases。
  为此 README 内**不写具体版本号字面量**（写 `x.y.z` / `<版本号>` 占位），避免每版都要回来改 README。
- 文档应同时具备：架构 / 模块 / 接口 / 数据库 / 使用部署；变更日志归发布记录。
- 定期更新依赖、做性能优化与代码重构，但**不要在不相关的任务里夹带**这些工作。

## 9. 安全宪法（强制条款，不可降级）

> 本节由用户与 Claude 协商后固化，**所有角色（product / designer / backend / cross-platform-desktop / cross-platform-mobile / code-review / testing / devops 等）默认遵守**，不需要在每次对话中重新提及。任何违背本节的设计或实现都视为缺陷，必须修复。
>
> 详细安全审查报告见 `文档记录/代码审查报告/v1.1-安全专项-MITM抗性-修复后复查.md`。

### 9.1 通信加密（应用层端到端）

1. **加密通道是默认值**：除以下**明文白名单**外，所有 `/api/v1/*` 接口的请求体、成功响应、错误响应一律走 ChaCha20-Poly1305 加密信封 `{"nonce": "<Base64>", "ciphertext": "<Base64>"}`。
   - `GET /health`
   - `POST /api/v1/auth/key-exchange`
   - `GET /api/v1/auth/admin-exists`
2. **文件传输也是端到端加密**：
   - 上传分片：`chunk_data` 字段嵌入加密请求体（外层 ChaCha20-Poly1305 加密信封内部携带 base64 二进制）
   - 下载 / 预览：服务端用 `StreamingResponse`，文件按 `STREAM_PLAINTEXT_CHUNK_SIZE = 64KB` 分片，每片独立加密成 `[4B 大端长度][12B nonce][密文+16B Poly1305 tag]` 帧。**禁止** `FileResponse` 直返裸文件流。
3. **错误响应必须加密**：服务端 `main.py` 必须注册并维持以下三种全局异常处理器（任何新增异常类型都要加进来）：
   - `HTTPException`（业务代码主动抛）
   - `StarletteHTTPException`（路由 404 / 方法 405 等 starlette 自抛的）
   - `RequestValidationError`（Pydantic 422）
   - `Exception`（兜底，统一包成 500「服务器内部错误」）
4. URL 上**不允许**携带任何业务参数值。GET 请求体也走加密 JSON（`requests.get(url, json=payload)`）。

### 9.2 密钥交换（保持现状）

5. 使用 **X25519 ECDH 临时密钥交换** + HKDF-SHA256（盐 `b"jflove-v1"`，长度 32B）派生 session_key + 12B 随机 nonce。
6. **保留前向保密 (PFS)**：每次会话生成临时密钥对，session_key 仅存内存（`_session_store: dict[str, bytes]`），私钥用完即销毁。
7. **不引入服务端长期身份密钥 / 公钥固定（pinning）**——已与用户协商决策保持现状。原因：
   - 当前威胁模型主要是**被动 MITM**（公司 HTTPS 解密做 DLP），ECDH 已经数学上挡住
   - 主动 MITM（实时改写流量）极少见且需要精确针对，性价比不高
   - 保持现状可以无负担地把项目开源到 GitHub，不需要管理身份密钥的分发
8. 任何"长期密钥 / 持久化 session_key / 写死对称密钥"的设计都需要**用户明确同意**才能引入。

### 9.3 鉴权与权限

9. **JWT 只走加密 body 的 `token` 字段**，禁止从 `Authorization` HTTP header 读取。所有 controller 的 `_get_user` / `_require_admin` 实现：
    ```python
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    ```
10. **URL 路径参数允许使用数字 ID / UUID**（如 `/users/{user_id}`、`/upload/{upload_id}`），但**每条带路径参数的路由必须做权限校验**：
    - admin-only 路由：`_require_admin`
    - 用户资源路由：业务层验证资源归属（如 sync_configs 验证 user_id 匹配，upload 会话验证 owner_user_id 匹配）
    - 涉及磁盘 / 笔记的路由：调用 `permission_service.check_disk_permission` / `check_notes_permission`
11. **任何新增的带路径参数路由都必须在设计文档显式标注鉴权方式**，并在审查/测试中验证「换 ID 不能绕过权限」。

### 9.4 不暴露内容

12. **响应头不允许出现文件名 / 路径 / 用户标识**：禁止 `Content-Disposition: filename="..."` 等做法。客户端从已加密的请求 path 字段自己决定保存名。
13. **错误 detail 中可以含用户输入**（被全局 handler 加密包裹），但**不要在异常 message 里 echo 完整路径 / 文件内容片段**——保留必要信息即可，例如「目录不存在」而非「目录不存在: foo/bar/secret.txt」。
14. **日志不记录用户内容明文**：filename / path 等字段在日志中可截断或哈希化；token / 密码 / session_key 严禁记录。

### 9.5 加密原语清单（不可绕过）

15. 服务端只通过 `src/utils/middleware.py` 的 `decrypt_request_body` / `encrypt_response` 处理普通 JSON 接口；只通过 `src/utils/crypto.py` 的 `encrypt_stream_chunk` + `StreamingResponse` 处理文件流。
16. 客户端只通过 `src/utils/http_client.py` 与后端通信，禁止在 `services/` / `ui/` 层直接 `import requests`。错误响应统一走 `_decrypt_envelope_or_none` 解密。
17. **新增加密相关代码必须复用已有原语**，不允许引入新算法 / 新模式 / 新 KDF。

### 9.6 各角色的延伸约束

| 角色 | 安全相关额外约束 |
| ---- | ---------------- |
| product | 不在需求文案中要求"在 URL 中显示文件名"、"明文传输文件以提升性能"等违反本节的写法 |
| designer | 设计新接口时必须显式标注：是否在明文白名单、是否有路径参数、归属校验逻辑、加密信封策略 |
| backend | 新增 controller 必须用 `decrypt_request_body` + `encrypt_response`；新增文件流接口必须用 `StreamingResponse + encrypt_stream_chunk` |
| cross-platform-desktop | 所有 HTTP 调用走 `http_client`；流式响应通过 `parse_stream_frame` 解密；不引入任何加密相关的硬编码常量。**Web UI 迁移后新增**：渲染层（JS）**无网络出口**，一切与后端的通信只经 `src/bridge/bridge.py` 的白名单桥（`api.request` 有路径白名单，并拒绝 `SESSION_MANAGED_PATHS`）；**文件字节与密钥严禁进入渲染层** —— 上传/下载/预览都在 Python 侧完成，媒体由 `StreamProxy` 解密后交给 **OS 解码器**（`QMediaPlayer` / Qt 图像解码），渲染层只拿到本地 URL 或本地文件路径 |
| cross-platform-mobile | 所有 HTTP 调用走 `http_service.dart`；流式响应通过 `stream_frame.dart` 帧解析器解密；不引入任何加密相关的硬编码常量；session_key 与 JWT 严禁出现在调试日志 |
| web-frontend | 所有 HTTP 调用走 `http-client.ts`；流式响应通过 `stream-frame.ts` 帧解析器解密；加密使用 `@noble/ciphers`（ChaCha20） + Web Crypto API（X25519 ECDH + HKDF），禁止引入其他加密库；session_key 与 JWT 严禁出现在 console.log / DOM 属性 |
| code-review | 把本节当成硬规则逐条核查，发现违反一律标记**严重**；重点核查"路径参数路由是否能用伪造 ID 绕过权限" |
| testing | 必测三类用例：① 加密信封往返；② 路径参数权限绕过（伪造他人 user_id / upload_id 应得 403）；③ 文件下载流可被客户端正确解密、篡改后认证失败 |
| devops | 打包前确认 `_PLAIN_PATHS` 白名单未被扩大；版本发布记录中记录加密协议版本（当前 `X-Encrypted-Stream: v1`）；Web 端 Docker 镜像构建时确保 nginx.conf 中 SPA fallback 配置正确 |

### 9.7 引入新 API / 新功能时的安全清单（自查）

设计或实现一个新接口前，逐条勾选：

- [ ] 路径上是否有可被枚举的 ID？如有，是否做了归属/角色校验？
- [ ] 请求 body 是否走 `decrypt_request_body`？
- [ ] 成功响应是否走 `encrypt_response`？
- [ ] 错误是否通过 `HTTPException` 抛出（自动被全局 handler 加密）？
- [ ] 是否需要返回文件流？如是，必须用 `StreamingResponse + encrypt_stream_chunk`，不能用 `FileResponse`
- [ ] 响应头是否含敏感元数据（filename、用户名等）？必须移除
- [ ] 是否在日志中明文记录了用户内容？必须截断或哈希
- [ ] 是否新增长期密钥 / 静态盐 / 写死对称密钥？严禁，除非用户明确批准
