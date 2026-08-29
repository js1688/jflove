# 项目工作手册（jflove）

> 本文件是项目的总纲。具体角色的工作细则放在 `.claude/skills/<角色名>/SKILL.md`（兼容 GitHub Copilot / Claude Code / Cursor / Windsurf 等），本文件只做规则定义与角色调度。

## 1. 项目模块与开发状态

| 模块 | 路径 | 说明 | 当前状态 |
| --- | --- | --- | --- |
| jflove-server | `jflove-server/` | 后端服务（FastAPI） | 在研 |
| jflove-desktop | `jflove-desktop/` | 跨平台桌面应用（PySide6） | 在研 |
| jflove-web | `jflove-web/` | 浏览器 Web 端（React + TypeScript），支持 PC 端与移动端浏览器双布局 | 在研 |
| jflove-app | `jflove-app/` | 移动端（Flutter + Dart）首版 Android-only，iOS/鸿蒙预留 | 在研 |

**模块边界（强制）**：

- 用户明确要求开发某模块时，只能在该模块目录内编码，不得越界改动其他模块。
- 标记为"暂不开发"的模块，未获用户明确要求一律不动。
- 永远不要自动修改 `.github`、`.vscode`、`.gitignore`、`.git`、`.idea`，除非用户明确要求。

## 1.1 计划先行（强制）

- **任何任务开始前，必须先列出完整计划**（包含每个步骤的具体内容、涉及的文件、依赖关系），经用户确认无误后，再逐步执行。
- **在处理新任务时也必须先列出 todo 清单**（写入 `plan.md`），经用户确认后再动手。
- **禁止跳过计划步骤直接编码**，即使是小改动也需要先说明改什么、改哪里、为什么改。
- **上下文截断风险防范**：长对话中，AI 应主动做阶段性总结并重新对齐计划，防止因上下文窗口截断导致工作不稳定。

### 1.1.1 计划文件管理（强制）

- **所有计划必须写入 `plans/` 目录**，每份计划独立一个文件，文件名格式：`plans/<模块>-<任务简述>.md`（如 `plans/mobile-crypto-layer.md`、`plans/server-bugfix-login-ttl.md`）。
- **每份计划文件必须包含**：
  - 任务目标（简要描述要完成什么）
  - 当前完成状态（已完成步骤 / 进行中步骤 / 待完成步骤，使用 `[x]` / `[-]` / `[ ]` 标记）
  - 涉及的源文件列表（预估会被修改的文件路径）
  - 依赖关系（此任务依赖哪些前置任务/文档）
- **任务中断与续接**：
  - 每次开始工作时，AI 必须先读取 `plans/` 下最近相关的计划文件，了解当前进度。
  - 工作过程中及时更新计划文件的完成状态，确保即使上下文截断、新对话重新加载后也能准确续接。
  - 所有步骤完成并验证通过后，将计划文件移至 `plans/归档/`（或直接在文件名前加 `DONE-` 前缀）。
- **避免计划间相互干扰**：每份计划文件只覆盖一个明确的任务范围。多个独立任务并行时，分别建立独立计划文件，不混在同一份文件中。

## 2. 技术栈

| 模块 | 语言 | 框架 / 关键库 | 详细技术栈 |
|------|------|-------------|-----------|
| jflove-server | Python 3.14+ | FastAPI + SQLite3 + PyJWT(ES256) + cryptography | `backend/SKILL.md` |
| jflove-desktop | Python 3.14+ | PySide6 6.8 + Fluent-Widgets + requests | `cross-platform-desktop/SKILL.md` |
| jflove-app | Dart 3.6+ | Flutter 3.27 + Riverpod + dio + pointycastle | `cross-platform-mobile/SKILL.md` |

> **加密协议三端统一**：X25519 ECDH + ChaCha20-Poly1305 + HKDF-SHA256（盐 `b"jflove-v1"`，32B）。

## 3. 项目目录结构

### jflove-server

```jflove-server/
├── src/
│   ├── controllers/    # 控制器层，处理 HTTP 请求
│   ├── services/       # 业务逻辑层
│   ├── repositories/   # 数据访问层
│   ├── models/         # 数据模型
│   ├── utils/          # 工具与公共模块
│   ├── config/         # 配置项
│   └── main.py         # 入口
├── tests/              # 测试代码
├── venv/               # 虚拟环境
├── build/              # 构建输出
├── build.py            # 构建脚本
├── requirements.txt
└── README.md
```

### jflove-desktop

```jflove-desktop/
├── src/
│   ├── components/     # UI 组件
│   ├── services/       # 与后端交互的服务层
│   ├── utils/          # 工具与公共模块
│   ├── config/         # 配置项
│   ├── ui/             # UI 页面与样式
│   └── main.py         # 入口
├── tests/
├── venv/
├── build/
├── build.py
├── requirements.txt
└── README.md
```

### jflove-app

```jflove-app/
├── lib/
│   ├── main.dart                         # 入口，ProviderScope + App widget
│   ├── app.dart                          # MaterialApp.router + 主题
│   ├── config/                           # 应用配置（常量、主题）
│   ├── models/                           # 数据模型（不可变类）
│   ├── providers/                        # Riverpod 状态管理
│   ├── services/                         # 业务服务层（9 个 service）
│   ├── pages/                            # 页面（按路由组织）
│   ├── widgets/                          # 可复用 UI 组件
│   └── utils/                            # 工具模块（crypto / http / session）
├── test/                                 # 单元 + Widget 测试
├── integration_test/                     # 集成测试
├── android/                              # Android 原生壳
├── ios/                                  # iOS 原生壳（预留）
├── ohos/                                 # 鸿蒙原生壳（预留，flutter_ohos）
├── pubspec.yaml                          # 依赖声明
├── analysis_options.yaml                 # 静态分析规则
├── build.py                              # 构建脚本
└── README.md
```

### jflove-web

```jflove-web/
├── src/
│   ├── main.tsx                    # 入口，ReactDOM.createRoot + 全局 Provider
│   ├── App.tsx                     # 根组件，RouterProvider + 全局布局
│   ├── config/                     # 配置项
│   ├── layouts/                    # 布局组件（DesktopLayout / MobileLayout / AuthLayout）
│   ├── pages/                      # 页面组件（按路由组织）
│   ├── components/                 # 可复用 UI 组件
│   ├── hooks/                      # 自定义 Hooks
│   ├── services/                   # 业务服务层（8 个 service）
│   ├── stores/                     # Zustand 状态管理
│   ├── utils/                      # 工具模块（crypto / http-client / session / stream-frame / responsive）
│   └── types/                      # TypeScript 类型定义
├── tests/                          # 单元测试 + 组件测试
├── index.html                      # Vite HTML 入口
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── eslint.config.ts
├── Dockerfile                      # 多阶段 Docker 构建
├── nginx.conf                      # Nginx SPA 配置
└── README.md
```

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
├── 项目管理记录/           # pmo 输出
├── 版本发布记录/           # devops 输出
└── 版本文档更新记录/        # 各版本文档变更汇总
```

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

### 5.2 表设计

- 表名：复数 + snake_case
- 字段名：snake_case，避免保留字
- 主键：`id`，自增整数
- 外键字段命名：`{related_table}_id`（**禁止建外键约束**）
- 索引：`{field_name}_idx`
- 必备字段（每张表都要有）：`id`、`created_at`、`updated_at`、`deleted_at`（软删除）

## 6. 角色与命令对应表

每次工作如果命中技能时,应当主动告知用户当前命中了哪些技能

| 角色（agent） | 命令文件 | 模块归属 | 输出文档目录 |
| --- | --- | --- | --- |
| product | `.claude/skills/product/SKILL.md` | 产品 | `文档记录/需求文档/` |
| designer | `.claude/skills/designer/SKILL.md` | 架构设计 | `文档记录/设计文档/` |
| backend | `.claude/skills/backend/SKILL.md` | jflove-server | `文档记录/后端开发记录/` + `jflove-server/README.md` |
| cross-platform-desktop | `.claude/skills/cross-platform-desktop/SKILL.md` | jflove-desktop | `文档记录/桌面端开发记录/` + `jflove-desktop/README.md` |
| web-frontend | `.claude/skills/web-frontend/SKILL.md` | jflove-web | `文档记录/Web端开发记录/` + `jflove-web/README.md` |
| cross-platform-mobile | `.claude/skills/cross-platform-mobile/SKILL.md` | jflove-app | `文档记录/移动端开发记录/` + `jflove-app/README.md` |
| code-review | `.claude/skills/code-review/SKILL.md` | 跨模块审查 | `文档记录/代码审查报告/` |
| testing | `.claude/skills/testing/SKILL.md` | 跨模块测试 | `文档记录/测试报告/` |
| pmo | `.claude/skills/pmo/SKILL.md` | 项目管理 | `文档记录/项目管理记录/` |
| devops | `.claude/skills/devops/SKILL.md` | 构建发布 | `文档记录/版本发布记录/` + 根 `README.md` |

**调度原则**：

- 角色定位、行为规范、技术栈细节均以 `.claude/skills/<角色名>/SKILL.md` 为准；本文件不重复展开。
- 命中具体流程后，必须在该流程内工作，不得越界处理其他流程的事。

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
| **Phase 8** 版本发布 | "发布" | devops | 审查/测试通过（Phase 5+6） | 构建产物 + `文档记录/版本发布记录/<版本号>.md` + 根 `README.md` |

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

1. 校验审查报告（Phase 5）和测试报告（Phase 6）均已通过
2. 版本号单一来源核查：`version.json` 为唯一真相，`python scripts/sync_version.py` 同步全部位置（含移动端 versionCode 派生）
3. 同步生产库表结构（带回滚脚本）
4. **统一打包所有已开发模块**：`python build.py -m all`（根目录统一入口，自动同步版本 + 逐模块环境检查 + desktop 切 venv）
   - 产物：服务端/Web 端 Docker 镜像、桌面端 PyInstaller 单文件、移动端 debug+release 两个 APK
5. 执行冒烟测试
6. 输出 `文档记录/版本发布记录/<版本号>.md`，并同步更新根 `README.md` 的「版本变化」与「功能特性」章节

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

- 每个版本号对应一份独立文件；新版本必须列出与上一版本的差异。
- 版本号策略：主版本号代表重大变更，次版本号代表小迭代。
- **版本号单一来源（强制）**：版本号唯一真相是仓库根 `version.json`，其余位置（server `main.py`/`Dockerfile`、desktop `settings.py`、web `package.json`/`constants.ts`、app `pubspec.yaml`/`settings_page.dart`）全部由 `python scripts/sync_version.py` 派生/同步，各角色**禁止手动改版本号字段**。改版本只改 `version.json` + 跑同步脚本；移动端 versionCode 由版本号派生（`major*1e6+minor*1e3+patch`），无需单独维护。
- **统一打包入口（强制）**：本地打包一律走根 `python build.py`（`-m` 参数或交互多选），禁止直接调用各模块 build.py / flutter build。
- 文档应同时具备：架构 / 模块 / 接口 / 数据库 / 使用部署 / 变更日志。
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
| cross-platform-desktop | 所有 HTTP 调用走 `http_client`；流式响应通过 `parse_stream_frame` 解密；不引入任何加密相关的硬编码常量 |
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
