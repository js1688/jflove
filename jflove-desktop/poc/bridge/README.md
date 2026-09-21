# P0-2 PoC：JS ↔ Python 桥（QWebChannel）+ 真实加密调用

> 对应计划：`plans/desktop-webui-migration-v1.5.0.md` §3 Phase 0 / P0-2
> 状态：**已验证通过（30/30 自动断言）**，可交互体验

## 1. 这个 PoC 要回答什么

| 问题 | 结论 |
| --- | --- |
| JS 能不能拿到后端的真实数据？ | ✅ 走的是**既有的** `src/services/*` + `src/utils/http_client.py`，加密链路一份都没重写 |
| 加密是不是真的在工作？ | ✅ 报文层实测：6/6 加密请求体、6/6 加密响应体、key-exchange 走明文、错误响应也是信封 |
| 未登记的方法会不会被调用到？ | ✅ 白名单拦截（负向用例实测拒绝） |
| Python 能不能主动推事件给 JS？ | ✅ `demo.progress` 5 条事件 5/5 到达页面 |
| JS 拿不拿得到 token / session_key？ | ✅ 拿不到 —— 会话视图刻意只有 `has_token` 等布尔/文案字段 |

## 2. 文件构成

```
poc/bridge/
├── bridge.py               # 桥本体：白名单分发 + 异步 Worker + 事件推送（N4 的原型）
├── main.py                 # 交互入口（桥自检控制台）
├── _temp_account.py        # 临时测试账号的建/删（需 server venv，因为它要 bcrypt）
├── test_headless.py        # 自动化自检（30 条断言 + 报文录制）
├── web/                    # 控制台页面（index.html / app.css / app.js）
├── out/traffic_summary.json# 报文结构摘要（只有结构与状态码，无内容）
└── README.md
```

## 3. 怎么跑

```powershell
# ① 自动化自检（会自己建/删临时账号）
jflove-desktop\venv-win\Scripts\python.exe jflove-desktop\poc\bridge\test_headless.py

# ② 交互式控制台（点按钮看真实数据；窗口带系统标题栏，正常关闭即可）
jflove-desktop\venv-win\Scripts\python.exe jflove-desktop\poc\bridge\main.py
```

> 控制台的「一键登录」用的是 `demo.login_temp_account`：它在 **Python 侧**读临时凭据并登录，
> **口令全程不下发到 JS**。该方法是 PoC 脚手架，N4 正式形态不应保留
> （正式实现里口令由用户输入、经桥传给 Python 后立即使用）。

## 4. 桥协议

```
下行 JS → Python：  bridge.request(requestId, service, method, paramsJson)   void
上行 Python → JS：  responseReady(requestId, payloadJson)
                    eventPushed(topic, payloadJson)

payloadJson = {"ok": true, "result": ...} | {"ok": false, "error": "..."}
```

三个设计决定及其理由：

1. **requestId 由 JS 生成** —— `bridge.request` 是 void 槽，不需要回传值，也就没有
   「先收到响应、后拿到 id」的竞态。若让 Python 生成并返回 id，就得再引入一层配对缓冲。
2. **必须异步** —— `services/` 里是同步阻塞的 `requests` 调用。直接在 GUI 线程执行会冻结界面。
   故复用既有的 `src/utils/worker.py::Worker`（QThread），完成后经信号回推。
   > `worker.py` 属「零改动」清单，直接拿来用；信号连接显式指定 `QueuedConnection`，
   > 保证回调在主线程执行。
3. **白名单而非反射** —— `WHITELIST = {service: {method: 处理函数}}` 是显式表，
   未登记一律拒绝。桥绝不能变成「JS 可调用任意 Python 函数」的后门。

### 当前白名单

| service | 方法 | 说明 |
| --- | --- | --- |
| `meta` | `ping` / `methods` / `server_history` | 连通性、白名单自描述、本地历史（不出网） |
| `auth` | `key_exchange` / `login` / `logout` / `session` | 会话（`session` 刻意不含令牌内容） |
| `users` | `list` | 管理员，加密接口 |
| `disks` | `accessible` / `all` | 加密接口 |
| `files` | `list` | 加密接口 |
| `notes` | `list` | 加密接口 |
| `config` | `all` | 加密接口 |
| `demo` | `progress` / `login_temp_account` | 事件推送演示 / PoC 专用登录 |

## 5. 安全设计（对照 AGENTS.md §9）

| 约束 | 本 PoC 的落点 |
| --- | --- |
| §9.5.16 桌面端 HTTP 只走 `http_client.py` | JS **没有任何网络出口**；全部数据由 Python 取回后经桥交付 |
| §9.3 JWT 只走加密 body | 桥不透传令牌；`session_view` 只有 `has_token: bool` |
| §9.14 日志不记用户内容 | 桥只记录 `service.method` 与**参数名**，绝不记录参数值（登录参数含口令） |
| §9.1.3 错误响应必须加密 | 实测：唯一一条 400 错误响应是加密信封，且客户端正确解密出中文 detail |
| §9.1.4 URL 不带业务参数 | 实测：全部请求 URL 无查询串 |
| 不新增第二套加密实现 | 加密只在 `src/utils/crypto.py` + `http_client.py`，桥只做分发 |

## 6. 对用户环境的保护（重要）

桌面端会话文件在 `%APPDATA%\JFLove\storage\session.json`，**本机已存在**（用户处于登录态）。
若不处理，登录临时账号会**覆盖用户真实会话**。实现统一在
`poc/common/session_guard.py`（`SessionGuard`）：

- `activate()`（**必须在任何登录之前**）：记录真实路径与哈希，把
  `auth_service._SESSION_FILE` 重定向到 PoC 临时路径（只打补丁，不改 `src/`）
- `verify_unchanged()`：结束时校验真实文件哈希未变
- `cleanup()`：删除临时会话文件并恢复原指向

`test_headless.py` 与 `main.py` **都**走这一份实现。

> ### 事故记录（2026-09-20）
> 本 PoC 的控制台最初**漏加**了这层护栏，用户点「一键登录」后
> **临时账号的会话被覆盖写进真实 `session.json`**，用户原本的登录态丢失。
> 测试做对了、控制台漏了 —— 所以护栏现在只有**一个实现**，两个入口强制共用。
> 教训：**「同一个保护写两遍」等于「一定漏一处」。**

其它保护：临时账号与凭据文件由测试/控制台结束流程自动回收（实测 dev 库无 `poc_*` 残留）。

## 7. 实测结果（30/30 通过）

关键几条：

| 断言 | 实测值 |
| --- | --- |
| 真实 ECDH 握手 | `session_ready=true`（对活着的 `127.0.0.1:8989` 完成 X25519 交换） |
| 加密登录 | `logged_in=true / is_admin=true`，端到端 325ms（含 ECDH 之后的密钥交换+加密+解密） |
| 真实业务数据 | `users.list` 3 行（`admin` / `p1probe` / `poc_bridge_probe`）、`disks.all` 1 个、`disks.accessible` 1 个 |
| 令牌不下发 | 会话视图字段 = `has_token/is_admin/logged_in/role/server_url/session_ready/username`；Python 侧持有 244 字符 JWT，**JS 侧全部数据中不含该串** |
| 事件推送 | 页面收到 5/5 条 `demo.progress` |
| 报文层 | 7 次交换：6 加密请求 + 6 加密响应 + 1 明文 key-exchange；1 条 400 错误响应亦为信封；URL 全部无查询串 |

## 8. 实测发现（越出本节点范围，仅记录）

1. **`config_service.get_all_config()` 的类型注解与实际返回不符**：注解是 `-> list[dict]`，
   实测返回 **dict**（`{"media_repair_allow_transcode": "0", "media_repair_enabled": "1"}`）。
   `config_service.py` 在「零改动」清单里，故**未修**；N4 定义桥的返回类型时需要按真实形状处理。
2. **`notes.list` 需要用户先配置笔记磁盘**，否则服务端返回 400「笔记目录未配置」。
   本 PoC 把它当**错误路径**证据使用（异常穿透桥 + detail 正确解密）。
3. 临时账号（`role=admin`、无笔记磁盘配置、无磁盘权限分配）能看到 1 个磁盘 —— 说明
   「管理员可见全部磁盘」这条服务端逻辑真实生效，不是空列表。

## 9. 不在本 PoC 范围

- 前端 UI（P0-3 起）
- 无边框窗口与标题栏（P0-1 已验证，本节点刻意用系统标题栏）
- 业务页面的桥方法全集（N4 才定稿；这里只挑了几个读操作 + 登录做验证）
- 写操作（上传/删除/移动）**刻意未进白名单** —— PoC 不碰用户数据
- 流式下载（`stream_request` / `download_stream`）与桥的结合方式
