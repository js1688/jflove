# jflove-server

JFLove 后端服务，基于 FastAPI 构建，提供文件管理、笔记管理、用户权限管理及端到端加密通信能力。

---

## 技术架构

### 分层架构

```
HTTP Request → Controller → Service → Repository → SQLite
                    ↓
              Middleware
    (decrypt_request_body / encrypt_response)
```

| 层 | 目录 | 职责 |
|----|------|------|
| **Controller** | [`src/controllers/`](jflove-server/src/controllers/) | HTTP 路由层，处理请求/响应序列化，调用 Service 层 |
| **Service** | [`src/services/`](jflove-server/src/services/) | 业务逻辑层，实现核心业务规则 |
| **Repository** | [`src/repositories/`](jflove-server/src/repositories/) | 数据访问层，纯 SQL 操作（无 ORM） |
| **Model** | [`src/models/database.py`](jflove-server/src/models/database.py) | 数据库建表与连接管理（aiosqlite） |
| **Middleware** | [`src/utils/middleware.py`](jflove-server/src/utils/middleware.py) | 请求体解密/响应加密中间件（ChaCha20-Poly1305） |
| **Utils** | [`src/utils/`](jflove-server/src/utils/) | 加密工具、JWT 工具、日志、全局异常处理器 |

### 加密中间件架构

```
Client → Encrypted Request {"nonce":"...","ciphertext":"..."}
              ↓
    decrypt_request_body(request) → 解密后的 JSON body
              ↓
    Controller 处理业务逻辑
              ↓
    encrypt_response(session_id, data) → {"nonce":"...","ciphertext":"..."}
              ↓
Client ← Encrypted Response
```

明文白名单（三条）：`GET /health`、`POST /api/v1/auth/key-exchange`、`GET /api/v1/auth/admin-exists`

### 运行环境

- **语言**：Python 3.14+
- **Web 框架**：FastAPI 0.115
- **数据库**：SQLite（aiosqlite 异步驱动）
- **认证**：PyJWT（ES256 签名）
- **加密**：cryptography（X25519 ECDH + HKDF-SHA256 + ChaCha20-Poly1305）
- **密码哈希**：bcrypt

---

## 快速开始

```bash
# 1. 创建虚拟环境
cd jflove-server
python3 -m venv venv

# 2. 安装依赖（首次需先安装 pydantic-core 预编译包）
venv/bin/pip install pydantic-core --pre
venv/bin/pip install -r requirements.txt

# 3. 启动开发服务
venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8989 --reload
```

启动后访问 API 文档：`http://localhost:8989/docs`

---

## 数据库

| 类型 | 路径 | 说明 |
|------|------|------|
| 开发库 | [`../jflove-db/jflove-dev.db`](../jflove-db/) | 开发期间使用，自动初始化表结构 |
| 生产库 | [`../jflove-db/jflove-prod.db`](../jflove-db/) | 仅发布时同步表结构，不同步业务数据 |

### 表结构

| 表名 | 说明 |
|------|------|
| `users` | 用户信息（含角色、启用状态） |
| `virtual_disks` | 虚拟磁盘配置 |
| `permissions` | 用户对虚拟磁盘的访问权限 |
| `sessions` | 审核日志/会话记录 |
| `configs` | 服务端配置 |

> 所有表均包含 `id`、`created_at`、`updated_at`、`deleted_at` 字段（软删除）。

---

## Docker 部署

### 挂载点

| 挂载点 | 用途 |
|--------|------|
| `/data` | SQLite 业务数据库持久化目录 |
| `/storage` | 物理大容量磁盘挂载点 |

### 单盘挂载

```bash
docker run -d \
    --name jflove-server \
    -p 8989:8989 \
    -v /opt/jflove/data:/data \
    -v /mnt/big-disk:/storage \
    --restart=always \
    jflove-server:1.1.6
```

### 多盘挂载

```bash
docker run -d \
    --name jflove-server \
    -p 8989:8989 \
    -v /opt/jflove/data:/data \
    -v /mnt/disk-a:/storage/disk-a \
    -v /mnt/disk-b:/storage/disk-b \
    --restart=always \
    jflove-server:1.1.6
```

### SELinux / Podman

Fedora / RHEL 系系统需加 `:Z` 后缀：

```bash
-v /opt/jflove/data:/data:Z -v /mnt/big-disk:/storage:Z
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JFLOVE_HOST` | `0.0.0.0` | 监听地址 |
| `JFLOVE_PORT` | `8989` | 监听端口（容器内） |
| `JFLOVE_DB_PATH` | 自动 | 一般无需手动设置 |

### 镜像构建

```bash
# 构建并打 tag
cd jflove-server
venv/bin/python build.py

# 构建后导出离线包
venv/bin/python build.py --save
```

构建脚本会自检：`jflove-prod.db` 必须 0 行业务数据（防止数据泄漏到镜像）。

---

## API 规范

### 加密协议

所有接口（除明文白名单外）需：

1. 请求头携带 `X-Session-ID`（通过密钥交换获得）
2. 请求体为加密格式：`{"nonce": "<Base64>", "ciphertext": "<Base64>"}`
3. 成功响应同样为加密信封格式
4. 错误响应统一通过全局异常处理器加密

### 完整接口文档

启动服务后访问 `http://localhost:8989/docs`（Swagger UI）。

---

## 依赖清单

核心依赖（详见 [`requirements.txt`](jflove-server/requirements.txt)）：

| 依赖 | 用途 |
|------|------|
| `fastapi>=0.115.0` | Web 框架 |
| `uvicorn[standard]` | ASGI 服务器 |
| `aiosqlite` | 异步 SQLite 驱动 |
| `cryptography` | X25519 / ChaCha20-Poly1305 加密 |
| `PyJWT` | JWT 签发与验证（ES256） |
| `bcrypt` | 密码哈希 |
| `python-multipart` | 文件上传支持 |
| `pydantic` | 请求体验证 |
| `pytest` | 测试框架 |
| `flake8` | 代码风格检查 |

---

## 目录结构

```
jflove-server/
├── src/
│   ├── config/         # 配置项（DB 路径、JWT 参数、会话参数）
│   ├── controllers/    # HTTP 路由层
│   ├── models/         # 数据库建表与连接
│   ├── repositories/   # 数据访问层（纯 SQL 操作）
│   ├── services/       # 业务逻辑层
│   ├── utils/          # 加密工具、JWT 工具、日志、中间件
│   └── main.py         # 入口文件
├── tests/              # 测试代码
├── build/              # 构建输出
├── build.py            # Docker 镜像构建脚本
├── Dockerfile          # Docker 镜像定义
├── entrypoint.sh       # Docker 容器入口
├── requirements.txt    # 依赖清单
└── README.md           # 本文件
```
