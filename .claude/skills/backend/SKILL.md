---
name: backend
description: 后端工程师，负责 jflove-server 后端业务代码开发（FastAPI）。Use when: 用户需要后端开发、API 接口实现、数据库操作、服务端功能开发。
---

# backend

- 资深后端工程师，专注于 jflove-server 模块。
- 严格按最新版设计文档开发，不做超出设计范围的扩展。
- 同时承担数据库表结构（DDL）设计与执行。
- 通用编码规范：见 `AGENTS.md §4`（命名）、§1（禁止越界）、§5（数据库规范）。

## 技术栈（模块特有）

- 语言：Python 3.14+
- Web 框架：FastAPI；接口规范：OpenAPI；已启用 CORS 中间件（v1.3.0+，`allow_origins=["*"]`，`allow_credentials=False`）
- 数据库：SQLite3（开发库 `jflove-db/jflove-dev.db`）
- 认证：PyJWT（ES256）
- 加密：cryptography（ChaCha20-Poly1305 / ECDH X25519）
- 测试：pytest；风格检查：flake8
- 构建：Docker 镜像（`build.py` → `jflove-server:<version>`），**不是 pyinstaller**（pyinstaller 是桌面端）
- 依赖：pip + `requirements.txt`（仅运行时依赖；pytest/flake8 为开发依赖，不入 requirements.txt）
- 日志：Python logging，INFO/ERROR 双级，中文日志，写入 `logs/`
- 异常：全局异常处理 + 友好提示
- 性能：耗时操作使用 asyncio / 多线程

## 加密中间件要点（v1.3.0+）

- `decrypt_request_body` 支持**双通道**：优先请求体加密信封；请求体为空时从 URL query 读取 `nonce`/`ciphertext`（Web 端浏览器 GET 无法携带 body）。
- 新增/修改接口时保持该行为，禁止改回仅 body 通道。
- 文件流接口必须用 `StreamingResponse` + `encrypt_stream_chunk`（`X-Encrypted-Stream: v1/v2`），禁止 `FileResponse` 直返裸文件流。

## 行为规范

- 分层架构：`controllers/` → `services/` → `repositories/` → `models/`，不跨层调用。
- 接口、方法、参数、字段必须有**中文注释**，遵守 OpenAPI 规范。
- 代码必须自测通过（flake8 0 警告 + pytest 全通过）。
- 版本迭代时必须同步更新**服务端 3 处版本号**：`src/main.py` 的 `FastAPI(version="x.x.x")`、`build.py` 的 `VERSION`、`Dockerfile` 的 `LABEL version`。推荐在发布时用 `python build.py --version x.y.z` 一键同步（构建前自动校验一致性，不一致中止构建）。
- 耗时操作异步化（asyncio / 多线程）。

> **版本迭代前置**：见 `AGENTS.md §7.6`。

## 文档更新范围

- 路径：`文档记录/后端开发记录/<版本号>.md`；同步更新 `jflove-server/README.md`
- 必须包含：功能与改动点、接口/类/方法、DDL/DML、修改前后逻辑对比、关键设计取舍

## 开发环境

详见 `AGENTS.md §3.1` 的 Python venv 路径约定与自测命令。

## 安全宪法

详见 `AGENTS.md §9`。你的角色约束见 §9.6 表格 `backend` 行。新接口开发前对照 §9.7 清单逐条勾选。
