# jflove-server

JFLove 后端服务，基于 FastAPI 构建，为个人数字工作台提供虚拟磁盘文件管理、笔记管理、用户与磁盘权限、媒体修复任务及端到端加密通信能力。

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
- **媒体修复（v1.4.2）**：imageio-ffmpeg（内置静态 FFmpeg 二进制，无需系统安装；系统已装 ffmpeg 时优先用之）

---

## 快速开始

```bash
# 1. 创建虚拟环境（路径约定见 AGENTS.md §3.1：Windows=venv-win，Linux=venv-linux）
cd jflove-server
python -m venv venv-win            # Windows
python3 -m venv venv-linux         # Linux

# 2. 安装依赖（首次需先安装 pydantic-core 预编译包）
venv-win\Scripts\pip install pydantic-core --pre              # Windows
venv-win\Scripts\pip install -r requirements.txt
venv-linux/bin/pip install pydantic-core --pre                # Linux
venv-linux/bin/pip install -r requirements.txt

# 3. 启动开发服务
venv-win\Scripts\uvicorn src.main:app --host 0.0.0.0 --port 8989 --reload      # Windows
venv-linux/bin/uvicorn src.main:app --host 0.0.0.0 --port 8989 --reload        # Linux
```

启动后访问 API 文档：`http://localhost:8989/docs`

---

## 数据库

| 类型 | 路径 | 说明 |
|------|------|------|
| 开发库 | [`../jflove-db/jflove-dev.db`](../jflove-db/) | 开发期间使用，自动初始化表结构 |
| 生产库 | [`../jflove-db/jflove-prod.db`](../jflove-db/) | 仅发布时同步表结构，不同步业务数据 |

> **发布前强制核对（`AGENTS.md §5.1.1`，发布阻塞项）**：`jflove-dev.db` 是表结构基准，
> prod 结构必须与它对齐，且**没有任何脚本会自动同步** —— 发布前必须执行
> `python scripts/check_db_schema.py`（退出码 0 = 一致；1 = 存在差异 → 阻塞发布；
> 2 = 环境/参数错误）。有差异用 `python scripts/check_db_schema.py --align` 对齐
> （先自动备份 prod，只改结构、不删 prod 多出的对象，重建表时保留 prod 自有列）。
> `init_db()` 的运行时迁移只会加表 / 加列 / 建索引，**管不到"去掉约束"这类既有结构变更**
> （如 v1.5.0 去掉 `users.username` 的列级 `UNIQUE`），不能拿它替代核对。
>
> 工具内置**三道关**：① 代码 `expected_schema()` / `expected_indexes()` 在内存库建出的结构
> vs dev 库（拦住"dev 陈旧"或"dev 含代码已不建的死表"）→ ② dev vs prod → ③ `--align` 后复验。
> 代码已不再创建的历史遗留表（当前为 `notes_permissions`）登记在
> `scripts/check_db_schema.py::_LEGACY_TABLES`，**不参与比对、不会被复制进 prod**；
> prod 上若残留空表可用 `--prune-legacy` 清理（非空会拒绝删除）。
> 改动本工具后必须跑通反向验证：`python scripts/verify_check_db_schema.py`（退出码 0 = 工具可信、
> 1 = 有断言失败；它用坏数据证明门禁拦得住、用纯格式差异证明不误报）。

### 表结构

| 表名 | 说明 |
|------|------|
| `users` | 用户信息（含角色、启用状态、笔记目录 `notes_disk_id` / `notes_path` 字段） |
| `virtual_disks` | 虚拟磁盘配置 |
| `user_permissions` | 用户对虚拟磁盘的访问权限（读/写/删） |
| `sessions` | 会话记录（session_id、JWT hash、过期时间） |
| `config` | 服务端配置（key-value） |
| `media_repair_tasks` | 媒体修复任务记录（v1.4.0+） |

> 所有表均包含 `id`、`created_at`、`updated_at`、`deleted_at` 字段（软删除）。
> 索引命名：`{field}_idx`；外键字段命名 `{related_table}_id`（不建外键约束）。
> v1.1.6：`sync_configs` 表已移除（同步配置改为客户端本地存储）。
> v1.5.0：`users.username` 去掉列级 `UNIQUE`，改为「普通索引 `users_username_idx` + 活跃行部分唯一索引 `users_username_active_uidx`」（软删除账号的用户名可被复用）；老库需按上方的「发布前强制核对」对齐结构。
> 代码不再创建的 `notes_permissions`（早期"笔记目录权限"概念的遗留表）在**全新部署中不会存在**，`init_db()` 也不删它；属历史遗留对象，不参与结构核对。

---

## 媒体修复（v1.4.2 手动离线修复）

v1.4.2 起废弃「播放时实时修复」：播放路径零 ffmpeg，只输出健康文件字节流；
损坏文件播放被拒（415 + `[MEDIA_NEEDS_REPAIR]`），由用户手动发起异步离线修复：

- **修复入口**：文件管理右键「修复损坏媒体」（三端）；播放失败弹窗「立即修复」直达
- **修复流程**：任务入队（`pending → running → verifying → success`）→ 产物落盘
  原文件同目录 `.jflove-repair/`（隐藏目录，文件列表不展示、路径访问拒绝）→
  用户验证播放 → 二次确认后覆盖原文件（`os.replace` 原子替换，**不留备份**）
- **权限门槛**：发起修复/覆盖/删除产物均要求磁盘**写+删权限并存**；任务列表
  全平台共享（所有登录用户可见，只读账号可看不可操作）
- **服务端配置（config 表）**：
  - `media_repair_max_concurrent`：离线修复队列并发上限，未设置时按 CPU 核数自动推导，硬上限 8
  - `media_repair_enabled`、`media_repair_allow_transcode`：**均已废弃**（自 v1.4.2 起服务端任何代码都不再读取，
    见 `src/services/media_repair_service.py:11-13`；键保留在库中不删，`init_db()` 只写入默认值 `"0"`）
- **安全**：修复只读源文件；产物路径由服务端任务记录推导（客户端不可传路径）；
  ffmpeg 参数列表（无 shell）；日志不记路径/文件名明文
- **接口**：`/api/v1/files/repair/create|tasks|cancel|override|delete-artifact|delete-record`
  （全部加密 body）；`/api/v1/files/stream` 可选 `repair_task_id` 验证播放产物，
  meta 帧 `stream_mode` 恒为 `"byte"`

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
    jflove-server:1.5.1
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
    jflove-server:1.5.1
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
# 仓库根目录执行（唯一对外入口）
python build.py -m server          # 构建并打 tag jflove-server:<version> / latest
python build.py -m all             # 四端统一入口（缺 docker 时 server/web 会响亮跳过）
```

> 模块脚本 `jflove-server/build.py` 的 `--tag <tag>` / `--no-cache` / `--save`（导出
> `build/jflove-server-<version>.tar` 离线包）仅供单步调试，**不作为交付路径**；
> 版本号只从仓库根 `version.json` 读取、由 `python scripts/sync_version.py` 同步，
> **不存在 `--version` 选项**（也不自动同步模块内版本号）。

构建脚本会自检：`jflove-prod.db` 必须不含业务数据（防止数据泄漏到镜像）——**仅放行** `config` 表里 `init_db()` 写入的默认键 `media_repair_enabled=0` / `media_repair_allow_transcode=0`，其余任何表非空、或 `config` 出现白名单外的键 / 被改过的值都会中止构建；模块内版本号（`main.py` / `Dockerfile`）与 `version.json` 不一致时同样中止构建。

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

### 离线下载接口（v1.8.0）

服务端按用户给的「无需登录网页地址」分析视频/音频/图片，下载到**该账号自己配置的**虚拟磁盘目录。
10 个接口均加密、无路径参数、按账号隔离（他人 task_id 一律 404）：

| 接口 | 用途 |
|------|------|
| `POST /api/v1/offline/analyze` | 创建分析任务（先校验总开关/账号目录/SSRF） |
| `GET /api/v1/offline/tasks` | 自己的任务列表 |
| `GET /api/v1/offline/detail` | 任务详情 + 清单 |
| `POST /api/v1/offline/select` | 勾选并开始下载（磁盘写权限/余量/体积上限） |
| `POST /api/v1/offline/next-page` | 图片追加下一页 |
| `POST /api/v1/offline/cancel` / `retry` / `delete-record` | 取消 / 重试 / 删除记录（不删文件） |
| `GET /api/v1/offline/meta` | 初始化元信息（自己的默认目录与可写磁盘） |
| `POST /api/v1/offline/settings` | 保存/清除自己的默认目录 |

- **出站收敛**：只有 `src/utils/outbound_http.py` 直接 import `httpx`；每一跳重定向都过 `ssrf_guard`；
  yt-dlp/浏览器经 `egress_proxy`（127.0.0.1 + 随机令牌）出网；ffmpeg/ffprobe 只读本地文件并带
  `-protocol_whitelist file,crypto`。
- **产物**：先写目标目录下 `.jflove-download/<task_id>/`，成功后 `os.replace` 原子改名；同一文件
  再次下载默认 `Range` 续传；同名不同内容追加 `_<sha1[:8]>`。
- **签名过期自愈（AC-225）**：下载遇到 401/403/404/410（`url_expired`）时，自动对**原页面**重新分析
  **一次**并刷新清单地址，再从断点继续；重分析后仍失败 → 任务 `failed` + 「媒体地址已过期」，由用户手动重试。
  分析队列已满（≥20 条排队/分析中）时不自动重分析；已成功项与用户勾选在重分析后保持原样。
- **配置**：全局策略（开关/深度分析/并发/上限/超时）在 `config` 表，仅管理员；账号默认目录在
  `offline_download_settings`，普通账号可用但必须有该磁盘的 write 权限。
- **音视频合并（v1.8.2）**：清单项带 `aux_url`（音视频分离站点，如 B 站）时，下载器会同时下载音轨并用
  ffmpeg `-c copy` 无损合并，`ffprobe` 断言含音轨后才落盘；合并失败按 `merge_failed` 失败，不产出无声文件。
- **超时可配（v1.8.2）**：`offline_download_analyze_timeout_s`（默认 90s，30~180）、
  `offline_download_connect_timeout_s`（默认 15s，5~30）、`offline_download_read_timeout_s`（默认 60s，15~120）；
  解析遇瞬态网络错误快速失败时自动重试 1 次；超时前已抓到的清单项会保留（`level=partial`）。
- **已知限制**：需要登录的站点/DRM/直播/代理不做；深度分析需镜像带 Chromium（`WITH_BROWSER=1`）；
  `max_items` 只约束非图片项（图片由 `max_images` 单独约束）。

### 服务器目录浏览接口（v1.8.1）

添加 / 编辑虚拟磁盘时，「服务端真实路径」可以直接从目录树点选。该接口**仅管理员**可用，
只读、**只列目录**（不返回文件名 / 文件内容 / 文件大小），请求与响应都走加密信封：

| 接口 | 鉴权 | 用途 |
|------|------|------|
| `GET /api/v1/virtual-disks/server-dirs` | 管理员 | 列出指定绝对路径下的子目录；用于三端「浏览…」选择器 |

- **请求**（GET query 加密信封）：`token`（管理员 JWT）、`path`（绝对路径，可空 = 从 roots 第一项开始）。
- **响应**（加密信封）：`path` / `parent` / `dirs`（`[{name, path}]`）/ `roots`（常见起点）/ `truncated` /
  `limit`（v1.8.2：单层目录数量上限，客户端据此渲染提示文案，不再硬编码 2000）。
- **roots**：POSIX 为 `/`、`/storage`、`/data`、`~`；Windows 为各存在的盘符根 + `~`（去重且只保留真实存在的目录）。
- **过滤**：隐藏目录（`.` 开头）不返回；按名称排序；单次最多 2000 条，超限 `truncated=true`。
- **错误**：401 缺少 / 无效令牌；403 非管理员或目录无权限；400 路径不是绝对路径；404 目录不存在。
  detail **不回显完整路径**，日志也不记录路径。
- 不在明文白名单内；无路径参数；不新增算法 / 密钥 / KDF。

### 挂载云盘增量接口（变更日志）

桌面端「云盘挂载」依赖两个只做增量的接口（均为加密信封 + 需磁盘读权限）：

| 接口 | 用途 |
|------|------|
| `POST /api/v1/files/journal` | 按 `disk_id + prefix + since_rev` 拉增量，**只返回该子目录范围** |
| `POST /api/v1/files/scan` | 全量对账（只 stat），发现绕过 API 直接写入磁盘目录的变更 |

- `rev` = `file_changes.id`（全局单调递增，删除不回收号）；
- `reset_required=true` 表示游标过旧（历史记录已被清理），客户端应改走 `scan`；
- `POST /api/v1/sync/snapshot` 为过渡期保留接口，新客户端不再使用。

### 上传临时目录与残留清理

- 分片先写入 `UPLOAD_TEMP_DIR`（默认 `jflove-server/build/upload_tmp/`，Docker 部署在容器内），
  `complete` 时才合并并**原子替换**到虚拟磁盘目录 —— 所以上传过程中目标目录里看不到文件是正常的。
- 后台每小时清理一次：
  1. **空闲 24 小时**的上传会话（客户端崩溃 / 强杀后没人续传）连同其临时目录；
  2. 不在会话中的孤儿分片目录（服务端重启后内存会话丢失）；
  3. 磁盘目录里遗留的 `.<文件名>.jflove-upload-<id>.part`（合并时进程被杀留下）。
- **进程启动后的首轮**用 1 小时阈值：此时内存会话必然为空，1 小时前的目录都是上次运行遗留的。
- 需手动立即清理时（确认没有正在上传的任务）：停止服务端后执行
  `rm -rf <UPLOAD_TEMP_DIR>/*`，再启动服务端即可。

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
| `pydantic-settings>=2.0` | 配置项加载（环境变量 / `.env`） |
| `imageio-ffmpeg>=0.5.1` | 内置静态 FFmpeg/ffprobe 二进制（媒体修复用，无需系统安装 FFmpeg） |

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
