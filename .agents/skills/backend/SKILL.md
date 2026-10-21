---
name: backend
description: "后端工程师，负责 jflove-server 后端业务代码开发（FastAPI）。Use when: 用户需要后端开发、API 接口实现、数据库操作、服务端功能开发。"
---

# backend

- 资深后端工程师，专注于 jflove-server 模块。
- 严格按最新版设计文档开发，不做超出设计范围的扩展。
- 同时承担数据库表结构（DDL）设计与执行。
- 通用编码规范：见 `AGENTS.md §4`（命名）、§1（禁止越界）、§5（数据库规范）。

## 技术栈（模块特有）

- 语言：Python 3.14+
- Web 框架：FastAPI；接口规范：OpenAPI；已启用 CORS 中间件（`allow_origins=["*"]`，`allow_credentials=False`）
- 数据库：SQLite3（开发库 `jflove-db/jflove-dev.db`）
- 认证：PyJWT（ES256）
- 加密：cryptography（ChaCha20-Poly1305 / ECDH X25519）
- 测试：pytest；风格检查：flake8
- 构建：Docker 镜像（`build.py` → `jflove-server:<version>`），**不是 pyinstaller**（pyinstaller 是桌面端）
- 依赖：pip + `requirements.txt`（仅运行时依赖；pytest/flake8 为开发依赖，不入 requirements.txt）
- 日志：Python logging，INFO/ERROR 双级，中文日志，写入 `logs/`
- 异常：全局异常处理 + 友好提示
- 性能：耗时操作使用 asyncio / 多线程

## 加密中间件要点

- `decrypt_request_body` 支持**双通道**：优先请求体加密信封；请求体为空时从 URL query 读取 `nonce`/`ciphertext`（Web 端浏览器 GET 无法携带 body）。
- 新增/修改接口时保持该行为，禁止改回仅 body 通道。
- 文件流接口必须用 `StreamingResponse` + `encrypt_stream_chunk`（`X-Encrypted-Stream: v1/v2`），禁止 `FileResponse` 直返裸文件流。

## 行为规范

- 分层架构：`controllers/` → `services/` → `repositories/` → `models/`，不跨层调用。
- 接口、方法、参数、字段必须有**中文注释**，遵守 OpenAPI 规范。
- 代码必须自测通过（flake8 0 警告 + pytest 全通过）。
- 版本号**单一来源 = 仓库根 `version.json`** 的完整规则见 `AGENTS.md §8.1`（当前 9 处，清单以
  `scripts/sync_version.py::POSITIONS` 为准）。**服务端只管 server scope 的 2 处**：
  `src/main.py` 的 `FastAPI(version="x.x.x")` 与 `Dockerfile` 的 `LABEL version`。
  **禁止手改**；改版本只改 `version.json` + 跑 `python scripts/sync_version.py`。
  ⚠️ 模块 `jflove-server/build.py` **没有 `--version` 参数**（只有 `--tag` / `--no-cache` / `--save`）。
- 耗时操作异步化（asyncio / 多线程）。

> **版本迭代前置**：见 `AGENTS.md §7.6`。
> **通用经验库**：见 `.agents/skills/lessons/LESSONS.md`（静默失败、编码陷阱等跨角色经验，开工前先扫一眼）。

## 开发阶段启动即验（强制）

改完后端不能只跑 pytest，必须真实起服务验证一次：

```bash
scripts/verify/server.sh start   # 临时库 + run_server.py + /health
scripts/verify/server.sh stop
```

- **必须走 `run_server.py`**：`uvicorn src.main:app` 会绕过 `JFLOVE_DB_PATH`，静默操作真实的
  `jflove-db/jflove-dev.db`（`DB_PATH` 是硬编码常量，见 L53）。
- 判据：`/health` 返回 200；至少一条「密钥交换 → 登录」链路 200（用 Web / Android / 桌面客户端触发，
  不要只用手写 curl 调加密接口）。
- 改 controller / 加密中间件 / 流式接口时，先补这条真实客户端调用再收工；详细入口见
  `testing` 技能的「实机验证入口」与 `scripts/verify/README.md`。
- **实机验证必须基于当前版本源码**：先按当前 `version.json` 构建/启动（含 `Dockerfile` 的当前镜像）；
  禁止用上一版本镜像/Release 包代替。
- **L3（Playwright）**：自带 Chromium 缺失时，`media_analyzer_service` 会回退系统 Edge/Chromium；
  先探测 `BROWSER_MANAGER.available()`，不要用“没下载 Chromium”直接判 L3 不可用。

## 数据库变更硬约束

1. **软删除 + 唯一约束会互相打架**
   `AGENTS.md §5.2` 要求每张表都有 `deleted_at`（软删除），而**列级 `UNIQUE` 不区分是否删除**：
   删掉的记录仍占着那个值 → 用户"删了却建不回同名"。
   - 正确建模：**唯一性只约束"活跃行"** —— 去掉列级 `UNIQUE`，改
     「普通索引 + 部分唯一索引 `... WHERE deleted_at IS NULL`」；
   - 查重语句必须与约束**同口径**：若约束只看活跃行，查重也必须只看活跃行，
     否则会出现"查重通过 → INSERT 撞约束 → 抛数据库原始错误"；
   - 拿到行之后仍要**显式判断 `deleted_at`**，历史行绝不允许登录。
   - 反面案例：`users.username` 用列级 `UNIQUE` + 查重带 `deleted_at IS NULL`，
     口径不一致 ⇒ 管理员删号后无法重建同名，报的是 `UNIQUE constraint failed`。

2. **改结构前先问 `init_db()` 兜不兜得住**
   `init_db()` 的运行时迁移只会**加表 / 加列 / 建索引**，**管不到"去掉约束"**这类既有结构变更
   （SQLite 也不支持 `ALTER TABLE ... DROP CONSTRAINT`，只能**重建表**）。
   - 凡涉及"去掉/修改既有约束"，必须写显式迁移脚本 + 回滚脚本，并在发布前核对对齐
     （判据、交付物与对齐方式见 `AGENTS.md §5.1.1` / `§5.1.2`）；
   - 重建表时**主键 id 必须原样保留**（其它表按 `user_id` 整数引用），失败要整体回滚；
   - **禁止**用 `PRAGMA writable_schema` 直接改建表语句：实测会留下悬空自动索引，
     之后任何访问都报 `malformed database schema ... orphan index`，且**当时不抛异常**、看着像成功了。

3. **迁移判断要先剥掉 SQL 注释**
   建表语句里可能带中文说明（如 `username TEXT NOT NULL, -- 去掉了原来的 UNIQUE`），
   直接做 `"UNIQUE" in sql` 的子串判断会**误判成"还没迁移"**，导致每次启动白重建一次表。
   正确做法：剥掉 `--` / `/* */` 注释后再用精确正则判断。

4. **开发库 ≠ 生产库，不要用 `init_db()` 当发布手段**
   开发期只允许动 `jflove-dev.db`；prod 库仅发布时同步**表结构**，且**没有任何脚本会自动
   把 dev 同步到 prod**（唯一真相与核对判据见 `AGENTS.md §5.1` / `§5.1.1`）。

5. **改了 `init_db()` 的建表 DDL，必须同步 `expected_schema()` / `expected_indexes()`**
   `src/models/database.py` 导出的这两个函数是**代码侧结构的唯一声明**，
   `scripts/check_db_schema.py` 靠它们把"代码期望的结构"建在内存库里，
   再与 dev 库比对（发布前的第一道关）。漏同步 = 门禁拿旧结构当基准，形同虚设。
   - **`CREATE TABLE` 里没有的列要显式补进去**：例如 `users.notes_disk_id` /
     `notes_path` 是 `init_db()` 用 `ALTER TABLE ADD COLUMN` 后补的
     （见 `_USERS_ALTERED_COLUMNS`），`expected_schema()` 必须把它们拼进期望结构，
     否则会把正常库误报成"陈旧"；
   - **代码不再创建的表不要写进去**，同时把表名登记到
     `scripts/check_db_schema.py::_LEGACY_TABLES`，避免它被对齐工具复制进 prod。

6. **`DB_PATH` 是硬编码常量，隔离测试别用环境变量**
   `src/config/settings.py` 里 `DB_PATH` 不读任何环境变量。想在没有 GUI/服务的环境
   里造一个临时库，必须改 `src.models.database` 模块命名空间里的 `DB_PATH`
   （它由 `from src.config.settings import DB_PATH` 导入），并在 `finally` 里复原。
   设环境变量会**静默操作真实的 dev 库**（`LESSONS.md` L20）。

7. **新增错误文案要面向界面**
   controller 会把 `ValueError` 转成 400 `detail`，客户端**原样展示给用户**。
   所以文案要写"用户名「xxx」已存在，请换一个"，不要写成数据库术语或内部标识。
   同理，**不要把数据库原始异常文本透出去**（如 `UNIQUE constraint failed: users.username`）。

## 文档更新范围

- 路径：`文档记录/后端开发记录/<版本号>.md`；同步更新 `jflove-server/README.md`
- 必须包含：功能与改动点、接口/类/方法、DDL/DML、修改前后逻辑对比、关键设计取舍

## 开发环境

详见 `AGENTS.md §3.1` 的 Python venv 路径约定与自测命令。

## 安全宪法

详见 `AGENTS.md §9`。你的角色约束见 §9.6 表格 `backend` 行。新接口开发前对照 §9.7 清单逐条勾选。
