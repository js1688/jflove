# API 调用契约（官网接口文档的正文来源）

> **唯一依据**：全部从 `jflove-server/src/` 实测提取，evidence 为 `文件:行`。
> 官网「接口文档 / 调用流程」页面**只能**使用本文件的内容。
> **不修改后端任何代码** —— 本文只是把既有实现如实写清楚。

---

## 0. 三条明文接口（其余全部加密）

`jflove-server/src/main.py:36-40` 的 `_PLAIN_PATHS`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查，返回 `{"status":"ok"}`（`main.py:155-157`） |
| `POST` | `/api/v1/auth/key-exchange` | 密钥交换，**唯一不加密的 POST** |
| `GET` | `/api/v1/auth/admin-exists` | 是否存在管理员，返回 `{"exists": bool}`（`auth_controller.py:83-87`） |

> 除这三条外，`/api/v1/*` 的**请求体、成功响应、错误响应**一律是加密信封。

---

## 1. 完整调用流程（五步，照做即可）

```
① 生成自己的 X25519 密钥对
② POST /api/v1/auth/key-exchange   （明文，带 client_public_key）
③ 用 ECDH + HKDF 派生出 32 字节 session_key
④ 之后每个请求：Header 带 X-Session-ID，Body 是加密信封
⑤ 登录拿 JWT（也在信封里），后续请求把 token 放进信封内的字段
```

### 步骤 ① 生成密钥对

客户端本地生成 **X25519** 密钥对，把公钥做 **Base64** 编码。

### 步骤 ② 密钥交换（明文）

```
POST /api/v1/auth/key-exchange
Content-Type: application/json

{
  "client_public_key": "<Base64 编码的 X25519 公钥>",
  "refresh": false
}
```

- 首次交换：**不带** `X-Session-ID`，`refresh` 传 `false`
- 密钥刷新：**带旧** `X-Session-ID`，`refresh` 传 `true`
- 证据：`auth_controller.py:22-26`（请求模型）、`:38-47`（分支逻辑）

响应（**明文**）：

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "server_public_key": "<Base64 编码的服务端 X25519 公钥>"
}
```

证据：`auth_service.py:59-80`（`key_exchange` 的返回）

### 步骤 ③ 派生会话密钥

```
shared_secret = X25519(client_private_key, server_public_key)
session_key   = HKDF-SHA256(shared_secret, salt=b"jflove-v1", length=32, info=b"")
```

- 盐是固定字面量 **`b"jflove-v1"`**（`config/settings.py:33`）
- HKDF 的 `info` 是**空字节串**（`crypto.py:53-58`）
- 输出 **32 字节**，作为 ChaCha20-Poly1305 的密钥

> 服务端私钥用完即销毁，session_key 只存内存 —— **服务重启后所有客户端必须重新交换**（`auth_service.py:9-12`）。

### 步骤 ④ 加密请求 / 解密响应

**每个请求头**：

```
X-Session-ID: <上一步拿到的 session_id>
Content-Type: application/json
```

**请求体**（加密信封）：

```json
{
  "nonce": "<Base64，12 字节随机>",
  "ciphertext": "<Base64，密文 + 16 字节认证标签>"
}
```

- 算法：**ChaCha20-Poly1305**
- `nonce` 每次请求都要**重新随机生成 12 字节**，绝不复用
- 明文是原始 JSON 的 UTF-8 字节
- 证据：`crypto.py:62-79`（`encrypt`）、`middleware.py:15-56`（`decrypt_request_body`）

**成功响应**同样是一个信封：

```json
{
  "nonce": "<Base64>",
  "ciphertext": "<Base64>"
}
```

用**同一个 session_key** 解开即可。证据：`middleware.py:57-78`（`encrypt_response`）。

**错误响应也是加密的**：业务异常、404/405、422 校验失败、500 兜底，全部经全局处理器包成信封
（`main.py:189-247`）。所以出错时不要去找明文 `detail`，要先解密。

### 步骤 ⑤ 登录拿 JWT

请求（信封内明文）：

```json
{
  "username": "your-user",
  "password": "your-password"
}
```

响应（信封内明文）：

```json
{
  "token": "<JWT>",
  "role": "admin | user",
  "username": "...",
  "user_id": 1,
  "expires_in": 3600
}
```

证据：`auth_service.py:login`、`auth_controller.py:105-134`。

**之后每个需要身份的接口**，把 token 放进信封内的 `token` 字段：

```json
{ "token": "<JWT>", "其他业务字段": "..." }
```

> **JWT 只走加密 body 的 `token` 字段**，服务端**不从** `Authorization` 头读取
> （`file_controller.py:_get_user` 注释明确此点）。这是刻意的安全设计，不是遗漏。

---

## 2. 三个容易踩的细节

### 2.1 GET 请求也走信封（可用 query 传）

浏览器的 GET 无法携带 body，所以中间件**额外支持**把信封放到 URL 查询串：

```
GET /api/v1/files/list?nonce=<Base64>&ciphertext=<Base64>
X-Session-ID: <session_id>
```

优先读请求体；体为空时才读 `nonce` / `ciphertext` 查询参数。
证据：`middleware.py:36-50`。

> 这是**兼容手段**。非浏览器客户端建议一律用请求体，避免长密文撑爆 URL。

### 2.2 文件下载 / 预览是**流式分帧**，不是整体信封

响应体不是 JSON，而是一串二进制帧，逐帧解密：

```
每帧：[4B 大端帧长 L][12B nonce][N B 密文 + 16B 认证标签]
其中 L = 12 + N + 16 = 28 + N
```

- 明文每帧 **64KB**（`STREAM_PLAINTEXT_CHUNK_SIZE`，`crypto.py:23`）
- 每帧独立随机 nonce，自带认证标签，可防篡改与截断
- 证据：`crypto.py:97-120`（`encrypt_stream_chunk`）

涉及接口：`GET /api/v1/files/download`、`/api/v1/files/preview`、`/api/v1/files/stream`。

### 2.3 上传分片是「信封内套 base64」

分片上传不另开通道，而是把二进制塞进加密请求体的字段里：

```
信封( {"token": "...", "upload_id": "...", "chunk_index": 0, "chunk_data": "<base64 二进制>"} )
```

即**外层 ChaCha20-Poly1305 信封**，内层 `chunk_data` 是 base64 的原始分片字节。

---

## 3. 接口清单（11 组 / 75 个操作）

> 数据源：`app.openapi()`（OpenAPI 3.1.0，可离线生成，见 §4）。

| 分组（tag） | 数量 | 路径前缀 |
| --- | --- | --- |
| 文件管理 | 17 | `/api/v1/files` |
| 远程连接 | 11 | `/api/v1/ssh` |
| 离线下载 | 10 | `/api/v1/offline` |
| 笔记 | 7 | `/api/v1/notes` |
| 虚拟磁盘 | 6 | `/api/v1/virtual-disks` |
| 媒体修复 | 6 | `/api/v1/files/repair` |
| 认证 | 5 | `/api/v1/auth` |
| 用户管理 | 5 | `/api/v1/users` |
| 权限管理 | 3 | `/api/v1/permissions` |
| 系统配置 | 3 | `/api/v1/config` |
| 同步 | 1 | `/api/v1/sync` |
| （无标签） | 1 | `/health` |

---

## 4. 数据从哪来（生成方式）

```python
import sys
sys.path.insert(0, "jflove-server")
from src.main import app
schema = app.openapi()      # OpenAPI 3.1.0，无需启服务
```

实测结果：

| 项 | 值 |
| --- | --- |
| OpenAPI 版本 | 3.1.0 |
| paths | 69 |
| operations | **75** |
| 有 `summary` | 75 / 75 |
| 有 `description` | 73 / 75（仅 `/health` 缺） |
| 有 `requestBody` | **1 / 75**（仅密钥交换） |

> **为什么只有一个 requestBody**：74 个接口都直接接收原始 `Request` 再手动解密，
> FastAPI 无法推断结构。**请求体字段只写在 `description` 的中文散文里**。
> 官网照实展示这些散文即可，不要假装有一套机器可校验的 schema。

**交叉验证**：官网另有正则抽取器（`website/scripts/extract_sources.py`）直接从
controller 装饰器数路由，实测 74 条，与 OpenAPI 的 75 条**只差 `/health`，其余零差异**。

---

## 5. 官方 Swagger UI（运行时可查）

后端用 FastAPI 默认配置，因此部署后自带：

| 地址 | 内容 |
| --- | --- |
| `http://<服务器>:8989/docs` | Swagger UI（含「Try it out」） |
| `http://<服务器>:8989/openapi.json` | 原始 OpenAPI 3.1.0 文档 |
| `http://<服务器>:8989/redoc` | ReDoc 渲染版 |

> 这三条都是 FastAPI 默认提供、**未加鉴权**的（服务端只挂了 CORS 中间件，
> 见 `main.py:133-141`）。官网**不关闭、不改变**这些端点，只在文档里告诉用户它们在。
> 注意：加密接口在 Swagger 里「Try it out」直接发是**调不通的** —— 需要先拿 session_key
> 并在本地加密，所以官网自己**不提供在线调试**，只给调用流程。

---

## 6. 使用规则（强制）

1. 官网接口页面的**结构**来自 OpenAPI（分组 / 方法 / 路径 / summary / description）。
2. **调用流程与加密细节**来自本文件，且必须有 `文件:行` 证据。
3. 不得声称提供 SDK、参数校验、在线调试 —— 这些都不存在。
4. 后端代码一行不改；本文与代码冲突时**以代码为准**，先修本文。
