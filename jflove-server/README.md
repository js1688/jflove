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
- **媒体修复（v1.4.0）**：imageio-ffmpeg（内置静态 FFmpeg 二进制，无需系统安装）

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
| `users` | 用户信息（含角色、启用状态、笔记目录 `notes_disk_id` / `notes_path` 字段） |
| `virtual_disks` | 虚拟磁盘配置 |
| `user_permissions` | 用户对虚拟磁盘的访问权限（读/写/删） |
| `sessions` | 会话记录（session_id、JWT hash、过期时间） |
| `config` | 服务端配置（key-value） |

> 所有表均包含 `id`、`created_at`、`updated_at`、`deleted_at` 字段（软删除）。
> 索引命名：`{field}_idx`；外键字段命名 `{related_table}_id`（不建外键约束）。
> v1.1.6：`sync_configs` 表已移除（同步配置改为客户端本地存储）。

---

## 媒体修复（v1.4.0）

对损坏/非流式媒体文件（普通 MP4 moov 在尾部、MKV、AVI、MOV、FLV 等）做无损修复，使浏览器 MSE 可正常边下边播：

- **服务端配置（config 表，三端共享）**：
  - `media_repair_enabled`：总开关，默认 `"0"`（关闭）
  - `media_repair_allow_transcode`：重编码子开关，默认 `"0"`（关闭，仅 `-c copy`）
  - `media_repair_max_concurrent`：并发数上限，未设置时按 CPU 核数自动推导，硬上限 8
- **行为**：开关关闭时零开销（不探测、不修复）；开启时健康文件直接原文件流（`stream_mode=byte`），需修复文件走 ffmpeg `-c copy` 重封装为 fMP4 的 **stdout 管道直出（不落盘）**（`stream_mode=time`，时间 range）
- **安全**：只读源文件、永不写回；ffmpeg 走 asyncio 子进程，客户端中断 kill+wait 回收；并发动态限流
- **接口**：`/api/v1/files/stream` 保持加密帧协议（`X-Encrypted-Stream: v2`），meta 帧新增 `stream_mode`，请求体新增可选 `range_start_seconds`

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
    jflove-server:1.3.1
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
    jflove-server:1.3.1
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
# 构建并打 tag（版本号与 build.py VERSION 一致；构建前自动校验三处版本号一致性）
cd jflove-server
venv/bin/python build.py

# 指定新版本号：自动同步 main.py / build.py / Dockerfile 三处后构建
venv/bin/python build.py --version 1.3.2

# 构建后导出离线包
venv/bin/python build.py --save
```

构建脚本会自检：`jflove-prod.db` 必须 0 行业务数据（防止数据泄漏到镜像）；版本号三处（`main.py` / `build.py` / `Dockerfile`）不一致时中止构建。

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

核心依赖（详见 [`requirements.txt`](jflove-server/requirements.txt)，仅运行时依赖；`pytest`/`flake8` 为开发依赖，不在 requirements.txt 中）：

| 依赖 | 用途 |
|------|------|
| `fastapi==0.115.12` | Web 框架 |
| `uvicorn[standard]==0.34.2` | ASGI 服务器 |
| `aiosqlite==0.21.0` | 异步 SQLite 驱动 |
| `aiofiles==24.1.0` | 异步文件 IO |
| `cryptography==44.0.3` | X25519 / ChaCha20-Poly1305 加密 |
| `PyJWT[crypto]==2.10.1` | JWT 签发与验证（ES256） |
| `bcrypt==4.3.0` | 密码哈希 |
| `python-multipart==0.0.20` | 文件上传支持 |
| `pydantic>=2.0` | 请求体验证 |

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
