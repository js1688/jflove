# jflove-server

JFLove 后端服务，基于 FastAPI 构建，提供文件管理、笔记管理、用户权限管理及端到端加密通信能力。

## 技术栈

- **运行环境**：Python 3.14+
- **Web 框架**：FastAPI 0.115
- **数据库**：SQLite（aiosqlite 异步驱动）
- **认证**：PyJWT（ES256 签名）
- **加密**：cryptography（X25519 密钥交换、HKDF 派生、ChaCha20-Poly1305 加密）
- **密码哈希**：bcrypt
- **构建**：pyinstaller

## 目录结构

```
jflove-server/
├── src/
│   ├── config/         # 配置项（DB 路径、JWT 参数、会话参数）
│   ├── models/         # 数据库建表与连接
│   ├── repositories/   # 数据访问层（纯 SQL 操作）
│   ├── services/       # 业务逻辑层
│   ├── controllers/    # HTTP 路由层
│   ├── utils/          # 加密工具、JWT 工具、日志、中间件
│   └── main.py         # 入口文件
├── tests/              # 测试代码
├── logs/               # 运行日志（自动生成）
├── build/              # 构建输出
├── venv/               # Python 虚拟环境
└── requirements.txt
```

## 快速开始

```bash
# 创建虚拟环境
python3 -m venv venv

# 安装依赖（首次需先安装 pydantic-core 预编译包）
venv/bin/pip install pydantic-core --pre
venv/bin/pip install -r requirements.txt

# 启动开发服务
venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8989 --reload
```

## 数据库

开发数据库：`../jflove-db/jflove-dev.db`（自动初始化表结构）

生产数据库：`../jflove-db/jflove-prod.db`（仅发布时更新表结构）

## Docker 部署（生产推荐）

镜像约定两个挂载点：

| 挂载点 | 用途 | 是否必须 |
|---|---|---|
| `/data` | SQLite 业务数据库持久化目录 | 推荐挂载（不挂则用镜像内置 DB，容器销毁数据丢失） |
| `/storage` | 物理大容量磁盘挂载点；服务端添加虚拟磁盘时把 `real_path` 填 `/storage` 或其子目录，文件即可落到宿主机大盘 | 推荐挂载 |

### 单盘挂载

```bash
docker run -d \
    --name jflove-server \
    -p 8989:8989 \
    -v /opt/jflove/data:/data \
    -v /mnt/big-disk:/storage \
    --restart=always \
    jflove-server:1.0.0
```

之后管理员在桌面端「添加虚拟磁盘」时，`real_path` 填 `/storage`，文件会保存到宿主机 `/mnt/big-disk`。

### 多盘挂载（同时使用多块物理盘）

```bash
docker run -d \
    --name jflove-server \
    -p 8989:8989 \
    -v /opt/jflove/data:/data \
    -v /mnt/disk-a:/storage/disk-a \
    -v /mnt/disk-b:/storage/disk-b \
    --restart=always \
    jflove-server:1.0.0
```

添加虚拟磁盘时 `real_path` 分别填 `/storage/disk-a`、`/storage/disk-b`，对应不同的物理盘。容器启动日志会自动列出所有 `/storage` 子目录，便于核对。

### SELinux / Podman 注意

Fedora / RHEL 系系统启用 SELinux 时，挂载需加 `:Z` 后缀（让卷自动获得正确的 SELinux context），否则容器会报 `Permission denied`：

```bash
-v /opt/jflove/data:/data:Z -v /mnt/big-disk:/storage:Z
```

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JFLOVE_HOST` | `0.0.0.0` | 监听地址 |
| `JFLOVE_PORT` | `8989` | 监听端口（容器内） |
| `JFLOVE_DB_PATH` | 自动 | 一般无需手动设置；entrypoint 会根据 `/data` 挂载状态自动决定 |

### 镜像构建

```bash
cd jflove-server
venv/bin/python build.py            # 构建并打 jflove-server:1.0.0 / latest
venv/bin/python build.py --save     # 构建后导出为 build/jflove-server-1.0.0.tar 离线包
```

构建脚本会自检：`jflove-prod.db` 必须 0 行业务数据（防止数据泄漏到镜像）。

## API 说明

所有接口（除 `/api/v1/auth/key-exchange` 和 `/health`）均需：

1. 请求头携带 `X-Session-ID`（通过密钥交换获得）
2. 请求体为加密格式：`{"nonce": "...", "ciphertext": "..."}`

完整接口文档启动服务后访问：`http://localhost:8989/docs`

### v1.1.4 变更

| 变更 | 说明 |
| --- | --- |
| `POST /api/v1/files/rename` | 源路径不存在时 → 404（原 400），语义更准确 |
| `POST /api/v1/files/move` | 源路径不存在时 → 404（原 400），语义更准确 |
| `POST /api/v1/auth/login` | 默认 JWT TTL 改为 1 天（原 1 小时），上限放宽到 30 天（原 8 小时），支持按天选项 |

### v1.1.3 新增接口

| 接口 | 说明 |
| --- | --- |
| `POST /api/v1/files/rename` | 重命名文件或目录（同目录内），服务端校验名称合法性（非空、无路径分隔符、非 `.`/`..`），同名时 409 |
| `POST /api/v1/files/move` | 移动文件或目录到同磁盘的另一目录，防循环嵌套，目标同名时 409 |

`GET /api/v1/files/disks` 响应字段扩展（向后兼容）：每个磁盘项新增 `can_write: bool`，供桌面端决定是否启用写操作按钮。

### v1.1.0 新增接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/files/stream` | 流式 Range 预览（v2 协议），支持 `range_start` / `range_end` 字节范围请求。响应头 `X-Encrypted-Stream: v2`，首帧为元数据帧（含 `file_size` / `content_type`），后续为 64KB 数据帧 |

## 安全说明

- 会话密钥通过 X25519 ECDH + HKDF-SHA256 派生，仅存于内存，不落库
- 服务重启后所有客户端需重新执行密钥交换
- JWT 令牌通过加密 Body 传递，不走明文 Authorization 头
