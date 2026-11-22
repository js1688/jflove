# 桌面端 UI 文案事实清单 · 第 2 部分

> 全部从代码实测提取，evidence 为 `文件:行`。官网指引只能引用本文件出现过的文案。
> 动态变量一律以 `XXX` 代替；文案**原样照抄**（含中文省略号「…」、全角括号与「」引号）。
> 源码根：`jflove-desktop/ui/src/pages/`（下文 evidence 中文件名均相对该目录）。

## 1. 离线下载（pages/offline-download/OfflineDownloadPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | 离线下载 | OfflineDownloadPage.tsx:90 |
| 页面副标题 | 服务端分析网页并下载视频 / 音频 / 图片到磁盘 | OfflineDownloadPage.tsx:91 |
| 页签（Segmented） | 任务中心 | OfflineDownloadPage.tsx:108 |
| 页签（Segmented） | 新建任务 | OfflineDownloadPage.tsx:109 |
| 按钮（图标按钮 title） | 离线下载设置（账号级默认目录） | OfflineDownloadPage.tsx:99 |
| 按钮 | 设置 | OfflineDownloadPage.tsx:101 |
| 按钮 | 查看设置 | OfflineDownloadPage.tsx:141 |
| 按钮 | 去设置 | OfflineDownloadPage.tsx:156 |
| 按钮 | 重试 | OfflineDownloadPage.tsx:126、OfflineDownloadPage.tsx:168 |
| 加载文案 | 加载离线下载配置… | OfflineDownloadPage.tsx:116 |
| 错误提示（兜底） | 加载离线下载配置失败 | OfflineDownloadPage.tsx:123（fallback，另见 :52） |
| 硬不可用提示 | 离线下载功能已关闭，请联系管理员开启。 | OfflineDownloadPage.tsx:30 |
| 硬不可用提示 | 你没有可写入的磁盘，无法使用离线下载。请联系管理员为你的账号分配可写磁盘。 | OfflineDownloadPage.tsx:32 |
| 配置失效提示 | 离线下载目录已失效，请重新配置；失效期间不能发起新的分析。 | OfflineDownloadPage.tsx:153 |

> 任务列表 / 新建任务面板 / 目录设置面板的文案在 `TaskList` / `NewTaskDialog` / `OfflineSettingsPanel` 子组件内，不在本清单 10 文件范围内。

## 2. 远程连接管理（pages/remote/RemotePage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 左栏标题 | 连接 | RemotePage.tsx:148 |
| 树根节点 | 会话 | RemotePage.tsx:153 |
| 树根节点 | 本地 | RemotePage.tsx:177 |
| 树根节点 | 远程 | RemotePage.tsx:198 |
| 空态（会话下） | 暂无打开的会话 | RemotePage.tsx:163 |
| 空态（本地加载中） | 加载中… | RemotePage.tsx:193 |
| 树叶提示（回退值） | 默认 shell | RemotePage.tsx:187、RemotePage.tsx:616 |
| 内容区标题（h2，随选中侧切换） | 本地 / 远程 | RemotePage.tsx:220 |
| 搜索框 placeholder | 搜索名称 / 主机 / 用户 | RemotePage.tsx:227 |
| 新建按钮（随选中侧切换） | 新建本机配置 / 新建连接 | RemotePage.tsx:237 |
| 树叶行内按钮 | 打开 | RemotePage.tsx:452 |
| 会话行关闭按钮 title | 关闭会话 | RemotePage.tsx:509 |
| 会话行关闭按钮 aria-label | 关闭 XXX | RemotePage.tsx:508 |
| 会话状态文案 | 已连接 / 已断开 / 连接中 | RemotePage.tsx:471 |
| 连接卡片模式徽标 | 仅 SFTP / SSH 终端 | RemotePage.tsx:543 |
| 连接卡片认证徽标 | 密码 / 密钥 / 密码 + 密钥 / 密码 + OTP | RemotePage.tsx:538-541 |
| 连接卡片跳板徽标 | ↳ 跳板 XXX | RemotePage.tsx:585 |
| 卡片图标按钮 title | 打开 | RemotePage.tsx:561、RemotePage.tsx:620 |
| 卡片图标按钮 title | 编辑 | RemotePage.tsx:564、RemotePage.tsx:623 |
| 卡片图标按钮 title | 复制 | RemotePage.tsx:567 |
| 卡片图标按钮 title | 删除 | RemotePage.tsx:570、RemotePage.tsx:626 |
| 本机卡片命令徽标 | 启动: XXX | RemotePage.tsx:639 |
| 空态（远程） | 还没有远程连接 / 点击右上角新建 | RemotePage.tsx:251 |
| 空态（本地） | 还没有本机配置 / 新建一个，选择 shell、工作目录与默认命令 | RemotePage.tsx:284-285 |
| 确认弹窗标题 | 删除连接 | RemotePage.tsx:269 |
| 确认弹窗标题 | 删除本机配置 | RemotePage.tsx:136 |
| 确认弹窗消息 | 确定删除「XXX」？ | RemotePage.tsx:270（远程）、RemotePage.tsx:137（本机） |
| 新建类型弹窗标题 | 新建远程连接 | RemotePage.tsx:335 |
| 新建类型弹窗说明 | 先选择要建立的连接类型 | RemotePage.tsx:336 |
| 连接类型选项 | 远程终端（SSH） | RemotePage.tsx:339 |
| 连接类型选项说明 | 打开终端，也可在同一条连接里浏览文件（SFTP） | RemotePage.tsx:340 |
| 连接类型选项 | 仅 SFTP | RemotePage.tsx:347 |
| 连接类型选项说明 | 只浏览/上传/下载文件，不建立 shell（适合只开 SFTP 的服务器） | RemotePage.tsx:348 |
| 侧滑表单标题 | 编辑本机配置 / 新建本机配置 | RemotePage.tsx:663 |
| 表单标签 | 名称 | RemotePage.tsx:668 |
| 表单 placeholder | 例如：PowerShell / WSL | RemotePage.tsx:672 |
| 表单标签 | Shell | RemotePage.tsx:677 |
| 表单标签 | 工作目录（可选） | RemotePage.tsx:691 |
| 表单 placeholder | /home/user/project | RemotePage.tsx:695 |
| 表单标签 | 默认命令（可选） | RemotePage.tsx:700 |
| 表单 placeholder | cd ~/apps && ls | RemotePage.tsx:704 |
| 表单说明 | 打开后自动执行，仅允许单行 | RemotePage.tsx:707 |
| 表单按钮 | 取消 | RemotePage.tsx:712 |
| 表单按钮 | 保存 | RemotePage.tsx:715 |

> 删除确认弹窗的按钮文字在共享组件 `components/remote/ConfirmModal.tsx`（不在本清单 10 文件范围内）；新建/编辑远程连接表单在 `components/remote/RemoteConnectionForm.tsx`（同不在范围内）。

## 3. 会话工作区（pages/remote/SessionPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 会话种类徽标 | 本机 / SSH / SFTP | SessionPage.tsx:47-51 |
| 终端类型徽标（SSH 标签与状态栏） | xterm-256color | SessionPage.tsx:54（常量，渲染于 :209、:433） |
| 标签栏尾部图标按钮 title | 返回连接列表 | SessionPage.tsx:188 |
| SSH 视图切换按钮 | 终端 | SessionPage.tsx:228 |
| SSH 视图切换按钮 | 文件 | SessionPage.tsx:240 |
| 会话状态文案 | 已断开 / 已连接 / 连接中 | SessionPage.tsx:266-270 |
| 工具条图标按钮 label | 复制选中内容 | SessionPage.tsx:277 |
| 工具条图标按钮 label | 粘贴 | SessionPage.tsx:283 |
| 工具条图标按钮 label | 清屏（保留会话） | SessionPage.tsx:289 |
| 工具条图标按钮 label | 重绘整屏（TUI 画面卡住时用） | SessionPage.tsx:295 |
| 字号按钮 title | 缩小字号 | SessionPage.tsx:302 |
| 字号按钮 title | 放大字号 | SessionPage.tsx:315 |
| 工具条按钮 | 断开 | SessionPage.tsx:327 |
| 跳板链路起点 | 本机 | SessionPage.tsx:336 |
| 失败提示（兜底） | 连接失败 | SessionPage.tsx:354 |
| 失败提示按钮 | 复制详情 | SessionPage.tsx:362 |
| 行数不足提示 | 窗口高度不足：终端只有 XXX 行，全屏 TUI 需要至少 XXX 行。请拉高窗口（字号已缩到最小）。 | SessionPage.tsx:373-374 |
| 状态栏 | 通道就绪 / 通道未连接 | SessionPage.tsx:429-431 |
| 状态栏 | UTF-8 | SessionPage.tsx:434 |
| 状态栏 | 行 XXX 列 XXX | SessionPage.tsx:439 |
| 状态栏 | ⌥ 字号 XXX | SessionPage.tsx:442 |
| 状态栏（字号自适应时追加） | →XXX（自适应） | SessionPage.tsx:443 |
| 状态栏 | 终端 / 文件 | SessionPage.tsx:445 |
| 空态 | 没有打开的会话 / 返回连接列表，双击连接或本机配置重新打开 | SessionPage.tsx:452-453 |
| 空态按钮 | 返回连接列表 | SessionPage.tsx:456 |
| 主机指纹提示标题 | 确认主机指纹 | SessionPage.tsx:467 |
| 主机指纹提示说明 | 首次连接 XXX，请与服务器管理员核对指纹 | SessionPage.tsx:470 |
| 主机指纹按钮 | 拒绝 | SessionPage.tsx:475 |
| 主机指纹按钮 | 接受 | SessionPage.tsx:478 |
| 继续认证提示标题 | 服务器要求继续认证 | SessionPage.tsx:487 |
| 继续认证输入说明（兜底） | 请输入服务器要求的密码或验证码 | SessionPage.tsx:489 |
| 继续认证提示说明 | 服务器要求继续认证（SSH password 方法）时在此输入**服务器账号登录密码** | SessionPage.tsx:492（含字面 `**`） |
| 继续认证输入框 placeholder | 密码 / 验证码 | SessionPage.tsx:498 |
| 继续认证按钮 | 确定 | SessionPage.tsx:505 |
| Toast | 失败详情已复制 / 复制失败，可到日志中查看诊断 | SessionPage.tsx:44 |
| Toast | 剪贴板为空或不可读，无法粘贴 | SessionPage.tsx:124 |

## 4. 本机终端管理页（pages/remote/LocalTerminalPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题（h2） | 本机终端 | LocalTerminalPage.tsx:83 |
| 标题旁徽标 | 仅本地存储 | LocalTerminalPage.tsx:85 |
| 按钮 | 打开默认终端 | LocalTerminalPage.tsx:89 |
| 按钮 | 新建终端配置 | LocalTerminalPage.tsx:99 |
| 空态 | 还没有本机终端配置 / 新建一个，指定 shell、工作目录和启动命令 | LocalTerminalPage.tsx:107-108 |
| 卡片 shell 回退值 | 默认 shell | LocalTerminalPage.tsx:125 |
| 卡片图标按钮 title | 打开 | LocalTerminalPage.tsx:132 |
| 卡片图标按钮 title | 编辑 | LocalTerminalPage.tsx:140 |
| 卡片图标按钮 title | 删除 | LocalTerminalPage.tsx:156 |
| 删除确认（window.confirm） | 删除「XXX」？ | LocalTerminalPage.tsx:158 |
| 卡片命令徽标 | 启动: XXX | LocalTerminalPage.tsx:175 |
| 侧滑表单标题 | 编辑终端配置 / 新建终端配置 | LocalTerminalPage.tsx:189 |
| 表单标签 | 名称 | LocalTerminalPage.tsx:193 |
| 表单标签 | Shell | LocalTerminalPage.tsx:201 |
| 表单标签 | 工作目录（可选） | LocalTerminalPage.tsx:215 |
| 表单 placeholder | /home/user/project | LocalTerminalPage.tsx:219 |
| 表单标签 | 连接后执行（可选） | LocalTerminalPage.tsx:224 |
| 表单 placeholder | cd ~/apps && ls | LocalTerminalPage.tsx:228 |
| 表单说明 | 打开后自动回显并执行；仅允许单行 | LocalTerminalPage.tsx:231 |
| 表单按钮 | 取消 | LocalTerminalPage.tsx:236 |
| 表单按钮 | 保存 | LocalTerminalPage.tsx:239 |
| 默认会话标签标题（代码内固定名） | 本机终端 | LocalTerminalPage.tsx:58 |

## 5. 安全状态（pages/SecurityPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | 安全状态 | SecurityPage.tsx:51（独立页 PageHeader；嵌入设置页时 h3 同名，:49） |
| 页面副标题 | 会话与加密通道 | SecurityPage.tsx:51 |
| 状态行标签 | 会话状态 | SecurityPage.tsx:56 |
| 状态值 | 已加密（ChaCha20-Poly1305） / 未建立加密会话 | SecurityPage.tsx:60 |
| 状态行标签 | Session ID | SecurityPage.tsx:65 |
| 状态值 | XXXXXXXX…（截取前 8 位 + …） | SecurityPage.tsx:66 |
| 状态行标签 | 密钥交换时间 | SecurityPage.tsx:69 |
| 状态值 | XXX（持续 XXX） | SecurityPage.tsx:72 |
| 状态行标签 | 当前用户 | SecurityPage.tsx:77 |
| 状态值（管理员追加） | XXX（管理员） | SecurityPage.tsx:78 |
| 状态行标签 | 加密算法 | SecurityPage.tsx:80 |
| 状态值 | X25519 ECDH + HKDF-SHA256 | SecurityPage.tsx:80 |
| 状态行标签 | 前向保密 | SecurityPage.tsx:81 |
| 状态值 | 已启用（临时密钥、私钥用完即销毁） | SecurityPage.tsx:81 |
| 按钮 | 刷新会话密钥 / 刷新中… | SecurityPage.tsx:92 |
| 按钮下说明 | 刷新后原会话密钥立即失效，需重新进行密钥交换 | SecurityPage.tsx:96 |
| Toast | 会话密钥已刷新（副标题：后续通信使用新密钥） | SecurityPage.tsx:37 |
| Toast | 刷新失败 | SecurityPage.tsx:39 |

## 6. 设置页（pages/SettingsPage.tsx）

页面标题：**设置**（SettingsPage.tsx:169）。代码内共 **8 个 Section title**，按渲染顺序：

| # | Section 标题 | 渲染条件 | evidence |
| --- | --- | --- | --- |
| 1 | 服务端 | 无条件 | SettingsPage.tsx:177 |
| 2 | 笔记目录 | 无条件 | SettingsPage.tsx:221 |
| 3 | 终端 | 无条件 | SettingsPage.tsx:246 |
| 4 | 账号 | 无条件 | SettingsPage.tsx:286 |
| 5 | 管理面板 | 仅 `isAdmin && !isPC`（移动端视口） | SettingsPage.tsx:312 |
| 6 | 外观 | 无条件 | SettingsPage.tsx:331 |
| 7 | 文本编辑器 | 无条件 | SettingsPage.tsx:352 |
| 8 | 关于 | 无条件 | SettingsPage.tsx:383 |

> 另有「安全状态」嵌入块仅 `!isPC` 渲染（SettingsPage.tsx:141-145）；PC 视口下安全状态走侧栏独立菜单。
> PC 视口实际渲染 7 个 Section（不含「管理面板」）。

### 6.1 服务端 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 按钮（PC） | 保存并重新连接 / 连接中… | SettingsPage.tsx:198 |
| 按钮（移动端） | 修改服务器地址 | SettingsPage.tsx:210 |
| Toast | 服务端地址已更新（副标题：请使用新服务端重新登录） | SettingsPage.tsx:118 |
| Toast | 连接失败 | SettingsPage.tsx:124 |
| 错误兜底 | 连接失败，请检查地址 | SettingsPage.tsx:122 |
| 底部弹窗标题（移动端） | 修改服务器地址 | SettingsPage.tsx:468 |
| 底部弹窗说明（移动端） | 保存后需重新密钥交换并登录 | SettingsPage.tsx:469 |
| 输入框 placeholder（移动端） | http://localhost:8989 | SettingsPage.tsx:476 |
| 历史地址标签（移动端） | 历史地址 | SettingsPage.tsx:480 |

### 6.2 笔记目录 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 当前配置展示 | 当前：XXX / XXX | SettingsPage.tsx:226 |
| 当前配置回退值 | 磁盘 XXX / 根目录 / 未配置 | SettingsPage.tsx:226-228 |
| 按钮 | 浏览选择 | SettingsPage.tsx:241 |
| 选目录弹窗标题 | 选择磁盘 | SettingsPage.tsx:415 |
| 弹窗关闭按钮 label | 关闭 | SettingsPage.tsx:417 |
| 弹窗空态 | 暂无可用磁盘 | SettingsPage.tsx:432 |
| 弹窗按钮 | 取消 | SettingsPage.tsx:436 |

### 6.3 终端 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 设置项标题 | 复制 / 粘贴快捷键 | SettingsPage.tsx:249 |
| 设置项说明 | 默认「智能」：有选区时 Ctrl+C 复制，无选区时照常中断（SIGINT） | SettingsPage.tsx:251 |
| 选择器选项 | Ctrl+C / Ctrl+V（复制粘贴） | SettingsPage.tsx:263 |
| 选择器选项 | 智能：有选区复制，否则中断 | SettingsPage.tsx:264 |
| 选择器选项 | Ctrl+Shift+C / Ctrl+Shift+V | SettingsPage.tsx:265 |
| 设置项标题 | 窗口偏矮时自动缩小字号 | SettingsPage.tsx:270 |
| 设置项说明 | 保证终端至少 24 行：全屏 TUI（dsh / vim / htop）在更矮的终端里会画丢输入行 | SettingsPage.tsx:272 |
| 开关 label | 窗口偏矮时自动缩小字号 | SettingsPage.tsx:281 |

### 6.4 账号 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 角色标注 | 管理员 / 普通用户 | SettingsPage.tsx:290 |
| 凭证信息 | 登录凭证过期时间：XXX（剩余 XXX） | SettingsPage.tsx:295 |
| 剩余时长到期值 | 已过期 | SettingsPage.tsx:138 |
| 按钮 | 退出登录 | SettingsPage.tsx:305 |
| 确认弹窗标题 | 退出登录 | SettingsPage.tsx:400 |
| 确认弹窗消息 | 确定要退出登录吗？ | SettingsPage.tsx:401 |
| 确认弹窗确认按钮 | 退出 | SettingsPage.tsx:402 |

### 6.5 管理面板 Section（仅移动端视口）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 列表项 | 用户管理 | SettingsPage.tsx:149 |
| 列表项 | 磁盘管理 | SettingsPage.tsx:150 |
| 列表项 | 权限配置 | SettingsPage.tsx:151 |
| 列表项 | 系统设置 | SettingsPage.tsx:152 |

### 6.6 外观 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 说明 | 跟随系统会随操作系统的亮/暗设置自动切换 | SettingsPage.tsx:334 |
| 页签（Segmented） | 跟随系统 | SettingsPage.tsx:340 |
| 页签（Segmented） | 亮色 | SettingsPage.tsx:341 |
| 页签（Segmented） | 暗色 | SettingsPage.tsx:342 |

### 6.7 文本编辑器 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 子块标题 | 快捷键 | SettingsPage.tsx:356 |
| 子块说明 | 共 XXX 项功能快捷键，逐项都有中文说明，可改键、可单项重置；快捷键只在 JFLove 窗口激活时生效，不注册任何系统级全局热键。 | SettingsPage.tsx:358-359 |
| 已自定义时说明 | 已自定义 XXX 项：XXX… | SettingsPage.tsx:361-363 |
| 未自定义时说明 | 当前全部使用默认键位。 | SettingsPage.tsx:364 |
| 按钮 | 打开快捷键配置 | SettingsPage.tsx:367 |
| 子块标题 | 文件关联 | SettingsPage.tsx:373 |
| 子块说明 | 选择 JFLove 声明支持的扩展名（应用内偏好，不修改系统默认程序） | SettingsPage.tsx:375 |

### 6.8 关于 Section

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 版本行 | JFLove vXXX | SettingsPage.tsx:385 |
| 加密方案行 | 加密方案：X25519 ECDH + ChaCha20-Poly1305 | SettingsPage.tsx:387 |

## 7. 用户管理（pages/admin/AdminUsersPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | 用户管理 | AdminUsersPage.tsx:135 |
| 页头按钮 | 添加用户 | AdminUsersPage.tsx:139 |
| 表头 | ID / 用户名 / 角色 / 状态 / 创建时间 / 操作 | AdminUsersPage.tsx:158-163 |
| 状态徽标（点击可切换） | 启用 / 禁用 | AdminUsersPage.tsx:187、AdminUsersPage.tsx:253 |
| 状态徽标 title | 点击禁用该账号 / 点击启用该账号 | AdminUsersPage.tsx:181 |
| 行内按钮 | 改密 | AdminUsersPage.tsx:202 |
| 行内按钮 | 删除 | AdminUsersPage.tsx:211 |
| 空态 | 暂无普通用户 / 点击右上角「添加用户」创建第一个普通用户 | AdminUsersPage.tsx:222-223 |
| 移动端卡片角色文案 | 普通用户 | AdminUsersPage.tsx:247 |
| 移动端三点菜单按钮 label | 操作菜单 | AdminUsersPage.tsx:261 |
| 移动端菜单项 | 修改密码 | AdminUsersPage.tsx:277 |
| 移动端菜单项 | 禁用账号 / 启用账号 | AdminUsersPage.tsx:286 |
| 移动端菜单项 | 删除 | AdminUsersPage.tsx:296 |
| 创建弹窗标题 | 创建用户 | AdminUsersPage.tsx:318 |
| 创建弹窗确认按钮 | 创建 | AdminUsersPage.tsx:322 |
| 输入框 placeholder | 用户名 | AdminUsersPage.tsx:326 |
| 输入框 placeholder | 密码 | AdminUsersPage.tsx:333 |
| 修改密码弹窗标题 | 修改密码 - XXX | AdminUsersPage.tsx:345 |
| 修改密码弹窗确认按钮 | 确认 | AdminUsersPage.tsx:349 |
| 输入框 placeholder | 新密码 | AdminUsersPage.tsx:353 |
| 表单弹窗取消按钮 | 取消 | AdminUsersPage.tsx:394 |
| 删除确认弹窗标题 | 确认删除 | AdminUsersPage.tsx:365 |
| 删除确认弹窗消息 | 确定要删除用户「XXX」吗？ | AdminUsersPage.tsx:366 |
| 删除确认弹窗确认按钮 | 删除 | AdminUsersPage.tsx:367 |
| 错误态 | 加载用户列表失败：XXX | AdminUsersPage.tsx:147 |
| Toast（错误） | 添加用户失败 / 修改密码失败 / 删除用户失败 / 禁用用户失败 / 启用用户失败 | AdminUsersPage.tsx:83、:98、:111、:123 |
| Toast（错误兜底） | 加载用户列表失败 | AdminUsersPage.tsx:55 |
| Toast（成功） | 用户已创建 / 密码已修改 / 用户已删除 | AdminUsersPage.tsx:89、:103、:115 |

## 8. 磁盘管理（pages/admin/AdminDisksPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | 磁盘管理 | AdminDisksPage.tsx:124 |
| 页头按钮 | 添加磁盘 | AdminDisksPage.tsx:128 |
| 表头 | ID / 名称 / 路径 / 创建时间 / 操作 | AdminDisksPage.tsx:147-151 |
| 行内按钮 | 编辑 | AdminDisksPage.tsx:180 |
| 行内按钮 | 删除 | AdminDisksPage.tsx:189 |
| 空态 | 暂无磁盘 / 点击右上角「添加磁盘」创建第一个虚拟磁盘 | AdminDisksPage.tsx:200-201 |
| 移动端三点菜单按钮 label | 操作菜单 | AdminDisksPage.tsx:234 |
| 移动端菜单项 | 编辑 | AdminDisksPage.tsx:255 |
| 移动端菜单项 | 删除 | AdminDisksPage.tsx:265 |
| 表单弹窗标题 | 创建磁盘 / 编辑磁盘 | AdminDisksPage.tsx:287、AdminDisksPage.tsx:302 |
| 输入框 placeholder | 磁盘名称 | AdminDisksPage.tsx:372 |
| 输入框 placeholder | 磁盘路径（可点右侧浏览选择） | AdminDisksPage.tsx:380 |
| 浏览按钮 | 浏览… | AdminDisksPage.tsx:387 |
| 表单弹窗说明 | 从服务器目录树选择，或直接输入绝对路径。 | AdminDisksPage.tsx:391 |
| 表单弹窗取消按钮 | 取消 | AdminDisksPage.tsx:362 |
| 表单弹窗确认按钮 | 确认 | AdminDisksPage.tsx:365 |
| 删除确认弹窗标题 | 确认删除 | AdminDisksPage.tsx:330 |
| 删除确认弹窗消息 | 确定要删除磁盘「XXX」吗？磁盘内的所有文件将被删除。 | AdminDisksPage.tsx:331 |
| 删除确认弹窗确认按钮 | 删除 | AdminDisksPage.tsx:332 |
| Toast（错误） | 创建磁盘失败 / 更新磁盘失败 / 删除磁盘失败 | AdminDisksPage.tsx:82、:95、:108 |
| Toast（错误兜底） | 加载磁盘列表失败 | AdminDisksPage.tsx:65 |
| Toast（成功） | 磁盘已创建 / 磁盘已更新 / 磁盘已删除 | AdminDisksPage.tsx:86、:99、:112 |

> 服务器目录选择器弹窗文案在 `components/ServerDirPickerModal.tsx`（不在本清单 10 文件范围内）。

## 9. 权限配置（pages/admin/AdminPermissionsPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | 权限配置 | AdminPermissionsPage.tsx:129 |
| 左栏标题 | 选择用户 | AdminPermissionsPage.tsx:143 |
| 左栏空态 | 暂无普通用户 | AdminPermissionsPage.tsx:162 |
| 表头 | 磁盘 / 读取 / 写入 / 删除 | AdminPermissionsPage.tsx:174-177 |
| 权限勾选框标签（移动端底部弹窗） | 读 / 写 / 删 | AdminPermissionsPage.tsx:19-21 |
| 权限勾选框 aria-label | XXX权限（已允许，点击取消） / XXX权限（未允许，点击授权） | AdminPermissionsPage.tsx:118 |
| 右侧空态（未选用户） | 请先选择左侧用户 / 选择用户后即可配置其磁盘权限 | AdminPermissionsPage.tsx:218 |
| 磁盘空态 | 暂无磁盘 | AdminPermissionsPage.tsx:210、:281 |
| 移动端用户卡片说明 | 点击配置磁盘权限 | AdminPermissionsPage.tsx:237 |
| 移动端用户空态 | 暂无普通用户 | AdminPermissionsPage.tsx:243 |
| 移动端底部弹窗标题 | XXX 的磁盘权限 | AdminPermissionsPage.tsx:259 |
| 移动端底部弹窗说明 | 切换即保存 | AdminPermissionsPage.tsx:260 |
| Toast（错误） | 取消磁盘权限失败 / 保存磁盘权限失败 | AdminPermissionsPage.tsx:98、:105 |
| Toast（错误兜底） | 加载用户与磁盘列表失败 | AdminPermissionsPage.tsx:56 |

## 10. 系统设置（pages/admin/AdminSystemPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | 系统设置 | AdminSystemPage.tsx:297 |
| 页面副标题 | 服务端配置（三端共享） | AdminSystemPage.tsx:298 |
| 加载文案 | 加载配置… | AdminSystemPage.tsx:305 |
| 错误兜底 | 加载系统配置失败 | AdminSystemPage.tsx:169 |

### 10.1 卡片一：离线媒体修复

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 卡片标题 | 离线媒体修复 | AdminSystemPage.tsx:318 |
| 卡片说明 | 损坏媒体经「修复中心」手动离线修复（在文件管理中右键文件选「修复损坏媒体」发起）。并发数 1~8，或留空按服务器 CPU 核数自动推导。配置存于服务端、三端共享，修改后立即生效。 | AdminSystemPage.tsx:321-323 |
| 设置项标题 | 修复并发数 | AdminSystemPage.tsx:330 |
| 设置项说明 | 1~8；留空按服务器 CPU 核数自动推导 | AdminSystemPage.tsx:332 |
| 输入框 placeholder | 自动 | AdminSystemPage.tsx:343 |
| 按钮 | 保存 | AdminSystemPage.tsx:347 |

### 10.2 卡片二：离线下载

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 卡片标题 | 离线下载 | AdminSystemPage.tsx:357 |
| 卡片说明 | 网址分析 / 媒体抓取的全局策略，配置存于服务端、三端共享，修改后立即生效。总开关默认开启；关闭后三端入口置灰且不产生任何出站请求（应急熔断）。三个超时项限制「解析总预算 / 出站连接 / 出站读取」，站点响应慢时可适当放宽。 | AdminSystemPage.tsx:360-362 |
| 开关标题 | 离线下载总开关 | AdminSystemPage.tsx:369 |
| 开关 label | 离线下载总开关 | AdminSystemPage.tsx:377 |
| 开关说明 | 默认开启；关闭后三端入口置灰，不产生任何出站请求 | AdminSystemPage.tsx:371 |
| 开关标题 | 深度分析（无头浏览器） | AdminSystemPage.tsx:392 |
| 开关 label | 深度分析 | AdminSystemPage.tsx:400 |
| 开关说明 | 仅作兜底；关闭后只能靠前两级解析 | AdminSystemPage.tsx:394 |
| 数字项标签 | 下载并发数（范围提示：允许范围 1~4） | AdminSystemPage.tsx:44（hint :45，范围文案 :421） |
| 数字项标签 | 单文件体积上限（MB）（允许范围 1~102400） | AdminSystemPage.tsx:50（hint :51） |
| 数字项标签 | 单次清单项数上限（允许范围 1~200） | AdminSystemPage.tsx:57（hint :58） |
| 数字项标签 | 单页图片张数上限（允许范围 1~1000） | AdminSystemPage.tsx:64（hint :65） |
| 数字项标签 | 解析总超时（秒）（允许范围 30~180） | AdminSystemPage.tsx:72（hint :73） |
| 数字项标签 | 连接超时（秒）（允许范围 5~30） | AdminSystemPage.tsx:78（hint :79） |
| 数字项标签 | 读取超时（秒）（允许范围 15~120） | AdminSystemPage.tsx:85（hint :86） |
| 按钮 | 保存数值配置 | AdminSystemPage.tsx:450 |

### 10.3 卡片三：存储与临时文件

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 卡片标题 | 存储与临时文件 | AdminSystemPage.tsx:460 |
| 卡片说明 | 自动清理程序自产的临时文件（上传分片与会话残留、离线下载残缺目录、合并残留）。临时文件期限同时决定断点续传有效期与清理保护期；到期后安全删除，客户端下次从头上传。关闭自动清理后，「立即清理」仍可手动执行一次。只删除程序自产、固定命名的文件，不会动你的正常文件。 | AdminSystemPage.tsx:463-466 |
| 开关标题 | 自动清理临时文件 | AdminSystemPage.tsx:473 |
| 开关 label | 自动清理临时文件 | AdminSystemPage.tsx:481 |
| 开关说明 | 只管自动清理；关闭后「立即清理」仍可手动执行 | AdminSystemPage.tsx:475 |
| 数字项标签 | 临时文件期限（小时）（允许范围 1~168） | AdminSystemPage.tsx:97（hint :98，范围文案 :502） |
| 数字项标签 | 清理检查间隔（分钟）（允许范围 5~1440） | AdminSystemPage.tsx:104（hint :105） |
| 设置项标题 | 立即清理 | AdminSystemPage.tsx:526 |
| 立即清理说明 | 立即清理一次（v1.9.1 起不受自动清理总开关限制）；跳过无法确认归属的条目，并显示删除与跳过数量。 | AdminSystemPage.tsx:528-529 |
| 清理结果展示 | 最近一次：删除会话 XXX 个 / 目录 XXX 个 / 合并残留 XXX 个，跳过 XXX 个 | AdminSystemPage.tsx:533-535 |
| 按钮 | 立即清理 | AdminSystemPage.tsx:546 |
| 按钮 | 保存存储配置 | AdminSystemPage.tsx:559 |

### 10.4 系统设置页 Toast

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| Toast（成功） | 配置已保存（副标题：三端共享，立即生效） | AdminSystemPage.tsx:182 |
| Toast（成功） | XXX已开启 / XXX已关闭（副标题：三端共享，立即生效） | AdminSystemPage.tsx:200 |
| Toast（成功） | 离线下载配置已保存 | AdminSystemPage.tsx:242 |
| Toast（成功） | 存储与临时文件配置已保存 | AdminSystemPage.tsx:270 |
| Toast（成功） | 清理完成（副标题：删除会话 XXX / 目录 XXX / 残留 XXX，跳过 XXX） | AdminSystemPage.tsx:284-286 |
| Toast（错误） | 保存失败 | AdminSystemPage.tsx:184、:203、:244、:272 |
| Toast（错误） | 并发数不合法（副标题：需为 1~8 的整数，或留空使用自动基线） | AdminSystemPage.tsx:218 |
| Toast（错误） | 配置值不合法（副标题：XXX需为 XXX~XXX 的整数） | AdminSystemPage.tsx:230-231 |
| Toast（错误） | 清理失败 | AdminSystemPage.tsx:289 |
