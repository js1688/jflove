# 桌面端 UI 文案事实清单 · 第 1 部分

> 全部从代码实测提取，evidence 为 `文件:行`。官网指引只能引用本文件出现过的文案。
> 注意：`jflove-desktop/ui/src/config/placeholders.ts` **不是**菜单真相，真实侧栏以
> `layouts/DesktopLayout.tsx` 的 `NAV_ITEMS` / `BOTTOM_ITEMS` / `ADMIN_ITEMS` 为准。
> 「文件:行」均相对 `jflove-desktop/ui/src/`。

## 1. 侧栏导航（layouts/DesktopLayout.tsx）

| 位置 | 菜单名 | 路由 | evidence |
| --- | --- | --- | --- |
| 主导航 1 | `文件管理` | `/files` | `DesktopLayout.tsx:32` |
| 主导航 2 | `笔记管理` | `/notes` | `DesktopLayout.tsx:33` |
| 主导航 3 | `文本编辑器` | `/editor` | `DesktopLayout.tsx:37`（仅登录且有编辑器准入时渲染，见 `:26-27` 注释与 `:78` 过滤） |
| 主导航 4 | `云盘挂载` | `/sync` | `DesktopLayout.tsx:38` |
| 主导航 5 | `传输任务` | `/transfer` | `DesktopLayout.tsx:42` |
| 主导航 6 | `离线下载` | `/offline` | `DesktopLayout.tsx:45` |
| 主导航 7 | `远程连接` | `/remote` | `DesktopLayout.tsx:47` |
| 底部固定 1 | `安全状态` | `/security` | `DesktopLayout.tsx:52` |
| 底部固定 2 | `设置` | `/settings` | `DesktopLayout.tsx:53` |
| 管理分区标题 | `管理` | — | `DesktopLayout.tsx:144`（仅 admin 可见，`:139`） |
| 管理面板 1 | `用户管理` | `/admin/users` | `DesktopLayout.tsx:58` |
| 管理面板 2 | `磁盘管理` | `/admin/disks` | `DesktopLayout.tsx:59` |
| 管理面板 3 | `权限配置` | `/admin/permissions` | `DesktopLayout.tsx:60` |
| 管理面板 4 | `系统设置` | `/admin/system` | `DesktopLayout.tsx:61` |

侧栏其它文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 品牌 Slogan | `私有云 · 端到端加密` | `DesktopLayout.tsx:128` |
| 品牌名（常量 `APP_NAME`，用户可见值即 `JFLove`） | `{APP_NAME}` | `DesktopLayout.tsx:125`（取值 `config/constants.ts:8`） |
| 折叠按钮 tooltip（展开态） | `折叠侧栏` | `DesktopLayout.tsx:113` |
| 折叠按钮 tooltip（折叠态） | `展开侧栏` | `DesktopLayout.tsx:113-114` |
| 用户卡角色（管理员） | `管理员` | `DesktopLayout.tsx:174` |
| 用户卡角色（普通） | `普通用户` | `DesktopLayout.tsx:174` |

## 2. 登录页（pages/LoginPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 标题 | `JFLove` | `LoginPage.tsx:138` |
| 副标语 | `所有通信均经过端到端加密，连接后自动交换临时会话密钥。` | `LoginPage.tsx:140` |
| 表单标签 | `服务端地址` | `LoginPage.tsx:148` |
| 地址输入框 placeholder | `例如 http://127.0.0.1:8989` | `LoginPage.tsx:161` |
| 地址提示 | `需填写完整的服务端地址（含协议与端口），例如 http://127.0.0.1:8989` | `LoginPage.tsx:167` |
| 下拉面板标题 | `历史地址` | `ServerUrlInput.tsx:133` |
| 下拉面板空态 | `暂无匹配的历史地址` | `ServerUrlInput.tsx:138` |
| 删除历史按钮 aria-label | `删除历史地址 XXX` | `ServerUrlInput.tsx:159` |
| 连接按钮 | `连接服务端` | `LoginPage.tsx:179` |
| 连接按钮（加载中） | `连接中…` | `LoginPage.tsx:179` |
| 管理员初始化警告 | `系统尚未配置管理员，请创建管理员账号。` | `LoginPage.tsx:196` |
| 表单标签 | `用户名` | `LoginPage.tsx:199` / `LoginPage.tsx:247` |
| 表单标签 | `密码` | `LoginPage.tsx:208` / `LoginPage.tsx:257` |
| 表单标签 | `确认密码` | `LoginPage.tsx:217` |
| 返回按钮 | `返回` | `LoginPage.tsx:227` |
| 创建按钮 | `创建管理员并登录` | `LoginPage.tsx:237` |
| 创建按钮（加载中） | `创建中…` | `LoginPage.tsx:237` |
| 表单标签 | `登录有效期` | `LoginPage.tsx:267` |
| 登录有效期选项 | `1 天` / `7 天` / `30 天` | `config/constants.ts:38-40` |
| 登录按钮 | `登录` | `LoginPage.tsx:286` |
| 登录按钮（加载中） | `登录中…` | `LoginPage.tsx:286` |
| 版本号页脚 | `vXXX`（`v{APP_VERSION}`） | `LoginPage.tsx:293` |

错误/提示文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 连接失败提示 | `连接失败，请检查服务器地址` | `LoginPage.tsx:74` |
| 表单校验提示 | `请填写用户名和密码` | `LoginPage.tsx:83` / `LoginPage.tsx:106` |
| 密码校验提示 | `两次输入的密码不一致` | `LoginPage.tsx:87` |
| 创建管理员失败提示 | `创建管理员失败` | `LoginPage.tsx:97` |
| 登录失败提示 | `登录失败` | `LoginPage.tsx:114` |

## 3. 磁盘列表页（pages/FileListPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `文件管理` | `FileListPage.tsx:31` |
| 页面副标题 | `选择磁盘开始浏览` | `FileListPage.tsx:31` |
| 空态标题 | `暂无可用磁盘` | `FileListPage.tsx:40` |
| 空态描述 | `请联系管理员为你分配磁盘权限` | `FileListPage.tsx:41` |
| 权限说明徽章 | `所有文件传输均走端到端加密` | `FileListPage.tsx:73` |
| 加载失败提示 | `加载磁盘列表失败` | `FileListPage.tsx:21` |
| 错误态重试按钮 | `重试`（ErrorState 默认 retryLabel） | `ui/Feedback.tsx:156` |

磁盘卡片（动态）：磁盘名 `disk.name`（`FileListPage.tsx:58`）、路径 `disk.path`（`FileListPage.tsx:60`），点击进入 `/files/磁盘ID`。

## 4. 磁盘浏览页（pages/DiskBrowserPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `XXX`（磁盘名；无磁盘记录时 `磁盘 XXX`） | `DiskBrowserPage.tsx:276`（`:109` 兜底文案） |
| 页面副标题 | `当前位置：XXX` / `根目录` | `DiskBrowserPage.tsx:278` |
| 上传按钮（有写权限时） | `上传` | `DiskBrowserPage.tsx:288` |
| 新建目录按钮（有写权限时） | `新建目录` | `DiskBrowserPage.tsx:291` |
| 只读徽章（无写权限时） | `只读` | `DiskBrowserPage.tsx:296` |
| 排序栏标签 | `排序` | `DiskBrowserPage.tsx:311` |
| 排序按钮 | `名称` / `大小` / `时间` | `DiskBrowserPage.tsx:325` |
| 新建目录弹窗标题 | `新建目录` | `DiskBrowserPage.tsx:341` |
| 新建目录弹窗确认 | `创建` | `DiskBrowserPage.tsx:360` |
| 新建目录弹窗取消 | `取消` | `DiskBrowserPage.tsx:357` |
| 目录名输入框 placeholder | `目录名称` | `DiskBrowserPage.tsx:370` |
| 拖拽提示主文案 | `松开以上传到当前目录` | `DiskBrowserPage.tsx:392` |
| 拖拽提示副文案 | `分片 64 KB · ChaCha20-Poly1305 端到端加密` | `DiskBrowserPage.tsx:394` |
| 空态标题 | `此目录为空` | `DiskBrowserPage.tsx:402` |
| 空态描述 | `可上传文件或新建目录` | `DiskBrowserPage.tsx:402` |
| 右键菜单项 | `下载` | `DiskBrowserPage.tsx:442` |
| 右键菜单项 | `预览` | `DiskBrowserPage.tsx:455` |
| 右键菜单项（可写可删 + 媒体文件） | `修复损坏媒体` | `DiskBrowserPage.tsx:464` |
| 右键菜单项（可写） | `重命名` | `DiskBrowserPage.tsx:478` |
| 右键菜单项（可写） | `移动到…` | `DiskBrowserPage.tsx:487` |
| 右键菜单项（可写，红色） | `删除` | `DiskBrowserPage.tsx:496` |
| 重命名弹窗标题 | `重命名` | `DiskBrowserPage.tsx:511` |
| 重命名弹窗确认 | `确认` | `DiskBrowserPage.tsx:521` |
| 重命名弹窗取消 | `取消` | `DiskBrowserPage.tsx:518` |
| 删除确认弹窗标题 | `确认删除` | `DiskBrowserPage.tsx:539` |
| 删除确认弹窗正文 | `确定要删除「XXX」吗？此操作不可撤销。` | `DiskBrowserPage.tsx:540` |
| 删除确认按钮 | `删除` | `DiskBrowserPage.tsx:541` |

右键菜单可见性：`下载` / `预览` / `修复损坏媒体` 仅文件（非目录）可见（`DiskBrowserPage.tsx:438`）；`修复损坏媒体` 还需磁盘可写可删（`:461`）；`重命名` / `移动到…` / `删除` 需磁盘可写（`:474`）。

错误/提示文案（Toast / ErrorBanner）：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 修复发起成功 Toast | `已加入修复队列`（副文案 `可在「修复中心」查看进度`） | `DiskBrowserPage.tsx:159` |
| 修复发起失败 Toast | `发起修复失败` | `DiskBrowserPage.tsx:161` |
| 拖放不可写 Toast | `当前目录不可写`（副文案 `无法上传文件`） | `DiskBrowserPage.tsx:250` |
| 重命名失败 | `重命名失败` | `DiskBrowserPage.tsx:177` |
| 删除失败 | `删除失败` | `DiskBrowserPage.tsx:187` |
| 移动失败 | `移动失败` | `DiskBrowserPage.tsx:197` |
| 创建目录失败 | `创建目录失败` | `DiskBrowserPage.tsx:208` |
| 无效磁盘空态 | `无效的磁盘 ID` | `DiskBrowserPage.tsx:269` |

「移动到…」弹窗（DirTreeModal）文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 弹窗标题 | `选择目录 · XXX`（磁盘名） | `DirTreeModal.tsx:79` |
| 关闭按钮 aria-label | `关闭` | `DirTreeModal.tsx:81` |
| 路径标签 | `路径` | `DirTreeModal.tsx:87` |
| 上级按钮 | `上级` | `DirTreeModal.tsx:90` |
| 根目录按钮 | `根目录` | `DirTreeModal.tsx:93` |
| 取消按钮 | `取消` | `DirTreeModal.tsx:133` |
| 确认按钮 | `选择此目录` | `DirTreeModal.tsx:136` |

## 5. 文件预览页（pages/FilePreviewPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `XXX`（文件名） | `FilePreviewPage.tsx:193` |
| 页面副标题 | `文件管理 / 预览` | `FilePreviewPage.tsx:194` |
| 播放/暂停按钮（视频/音频） | `播放` / `暂停` | `FilePreviewPage.tsx:203` |
| 下载按钮 | `下载` | `FilePreviewPage.tsx:207` |
| 加载提示 | `加载中…` | `FilePreviewPage.tsx:213` |
| 不支持预览标题（PDF） | `PDF 文件` | `FilePreviewPage.tsx:229` |
| 不支持预览标题（其它） | `不支持预览此文件类型` | `FilePreviewPage.tsx:229` |
| 不支持预览说明 | `该类型无法在应用内在线预览，请下载后用本地应用打开。` | `FilePreviewPage.tsx:232` |
| 不支持预览下载按钮 | `下载文件` | `FilePreviewPage.tsx:235` |
| 全屏按钮 | `全屏` / `退出全屏` | `FilePreviewPage.tsx:278` |
| 音量显示 | `XXX%` | `FilePreviewPage.tsx:266` |
| 解码说明 | `画面由本地解码器（OS 解码器）渲染；应用内不经过浏览器解码。` | `FilePreviewPage.tsx:303` |
| 大文件截断提示 | `文件较大，仅显示前 2 MB 内容。` | `FilePreviewPage.tsx:313` |

无预览目标（直接刷新页面）时的文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `预览` | `FilePreviewPage.tsx:179` |
| 页面副标题 | `文件管理` | `FilePreviewPage.tsx:179` |
| 错误信息 | `没有可预览的文件（请从文件列表点进来看）` | `FilePreviewPage.tsx:182` |

错误/提示文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 预览失败 | `预览失败` | `FilePreviewPage.tsx:122` |
| 下载保存成功 Toast | `已保存`（副文案为保存路径） | `FilePreviewPage.tsx:149` |
| 保存失败 Toast | `保存失败` | `FilePreviewPage.tsx:151` |

## 6. 笔记列表页（pages/NoteListPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `笔记管理` | `NoteListPage.tsx:65` |
| 页面副标题 | `共 XXX 篇`（`共 ${store.notes.length} 篇`） | `NoteListPage.tsx:66` |
| 新建按钮 | `新建` | `NoteListPage.tsx:69` |
| 搜索框 placeholder | `搜索笔记…` | `NoteListPage.tsx:79` |
| 空态标题（无搜索词） | `暂无笔记` | `NoteListPage.tsx:92` |
| 空态标题（有搜索词） | `未找到匹配的笔记` | `NoteListPage.tsx:92` |
| 空态描述（无搜索词） | `点击右上角「新建」创建第一篇笔记` | `NoteListPage.tsx:93` |
| 行尾重命名按钮 label | `重命名` | `NoteListPage.tsx:116` |
| 行尾删除按钮 label | `删除` | `NoteListPage.tsx:126` |
| 新建笔记弹窗标题 | `新建笔记` | `NoteListPage.tsx:141` |
| 新建笔记弹窗确认 | `创建` | `NoteListPage.tsx:160` |
| 新建笔记弹窗取消 | `取消` | `NoteListPage.tsx:157` |
| 笔记名输入框 placeholder | `笔记名称（自动补 .md）` | `NoteListPage.tsx:170` |
| 重命名弹窗标题 | `重命名` | `NoteListPage.tsx:179` |
| 重命名弹窗确认 | `确认` | `NoteListPage.tsx:189` |
| 重命名弹窗取消 | `取消` | `NoteListPage.tsx:186` |
| 删除确认弹窗标题 | `确认删除` | `NoteListPage.tsx:207` |
| 删除确认弹窗正文 | `确定要删除「XXX」吗？此操作不可撤销。` | `NoteListPage.tsx:208` |
| 删除确认按钮 | `删除` | `NoteListPage.tsx:209` |
| 确认弹窗取消按钮（默认） | `取消` | `ui/Overlay.tsx:168`（cancelLabel 默认值） |

## 7. 笔记编辑页（pages/NoteEditPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `XXX`（文件名） | `NoteEditPage.tsx:367` |
| 页面副标题 | `笔记管理 / Markdown` | `NoteEditPage.tsx:368` |
| 视图页签 | `编辑` / `分屏` / `预览` | `NoteEditPage.tsx:381-383` |
| 折叠大纲按钮 label | `折叠大纲`（展开时） | `NoteEditPage.tsx:391` |
| 展开大纲按钮 label | `展开大纲`（折叠时） | `NoteEditPage.tsx:391` |
| 修改状态徽章 | `未保存` | `NoteEditPage.tsx:400` |
| 保存状态徽章 | `已保存` | `NoteEditPage.tsx:404` |
| 保存按钮 | `保存` | `NoteEditPage.tsx:414` |
| 加载提示 | `加载笔记…` | `NoteEditPage.tsx:420` |

Markdown 工具栏按钮（title / aria-label）：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 工具栏按钮 | `加粗` / `斜体` / `删除线` / `一级标题` / `二级标题` / `三级标题` / `无序列表` / `有序列表` / `引用` / `行内代码` / `链接` / `图片` | `NoteEditPage.tsx:43-54` |
| 插入图表按钮 title | `插入 Mermaid 图表` | `NoteEditPage.tsx:446` |
| 图表面板标题 | `插入 Mermaid 图表` | `NoteEditPage.tsx:462` |
| 图表面板关闭按钮 label | `关闭` | `NoteEditPage.tsx:466` |
| 图表模板按钮 | `流程图` / `时序图` / `状态图` / `甘特图` / `饼图` / `类图` / `ER 图` | `NoteEditPage.tsx:60,71,84,97,110,122,138` |
| 图表面板说明 | `插入后编辑区可继续修改源码，预览区会自动渲染成图；语法有误时只影响该图表。` | `NoteEditPage.tsx:487` |

编辑区与预览区：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 编辑区 placeholder（源码中的 `&#10;` 是换行实体，此处保留原样） | `开始编写 Markdown…&#10;&#10;支持代码块（```python）与 Mermaid 图表（```mermaid）` | `NoteEditPage.tsx:509` |
| 预览区空态 | `暂无内容，切换到编辑模式开始编写` | `NoteEditPage.tsx:527` |
| 大纲栏标题 | `大纲（XXX 个标题）`（`大纲（{headings.length} 个标题）`） | `NoteEditPage.tsx:538` |
| 放弃确认弹窗标题 | `未保存的修改` | `NoteEditPage.tsx:561` |
| 放弃确认弹窗正文 | `当前笔记有未保存的修改，确定要放弃吗？` | `NoteEditPage.tsx:562` |
| 放弃确认按钮 | `放弃` | `NoteEditPage.tsx:563` |

错误/提示文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 保存成功 Toast | `笔记已保存` | `NoteEditPage.tsx:242` |
| 保存失败 Toast | `保存失败` | `NoteEditPage.tsx:244` |

## 8. 云盘挂载页（pages/SyncPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题 | `云盘挂载` | `SyncPage.tsx:328` |
| 页面副标题 | `本地与远端自动保持一致：改动自动上传，读取时自动下载；本地删除不会影响远端` | `SyncPage.tsx:329` |
| 新建按钮 | `新建挂载点` | `SyncPage.tsx:332` |
| 加载提示 | `加载挂载点…` | `SyncPage.tsx:337` |
| 空态标题 | `还没有挂载点` | `SyncPage.tsx:351` |
| 空态描述 | `新建一个挂载点，把远端目录映射到本地缓存目录；开关默认关闭，可随时停用` | `SyncPage.tsx:352` |

挂载点卡片文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 启用状态徽章 | `已启用` / `已停用` | `SyncPage.tsx:380` |
| 远端行 | `远端：磁盘 XXX`（有子目录时 `远端：磁盘 XXX / 子目录`，根为 `远端：磁盘 XXX / 根目录`） | `SyncPage.tsx:385-386` |
| 本地行 | `本地：XXX` | `SyncPage.tsx:389` |
| 统计行 | `共 XXX 项 · 已同步 XXX`（按需追加 ` · 待同步 XXX` / ` · 失败 XXX` / ` · 在线 XXX`） | `SyncPage.tsx:392-400` |

右侧详情区：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 未选择提示 | `请选择左侧的挂载点` | `SyncPage.tsx:411` |
| 启用状态文字 | `已启用` / `已停用` | `SyncPage.tsx:419` |
| 启用开关 label | `启用挂载点` | `SyncPage.tsx:425` |
| 刷新按钮 | `刷新` | `SyncPage.tsx:433` |
| 编辑按钮 label | `编辑挂载点` | `SyncPage.tsx:437` |
| 删除按钮 label | `删除挂载点` | `SyncPage.tsx:443` |
| 停用提示 | `挂载点默认关闭。打开上方开关后会拉取远端清单。` | `SyncPage.tsx:452` |
| 文件列表加载提示 | `读取文件列表…` | `SyncPage.tsx:457` |
| 文件空态标题 | `还没有文件` | `SyncPage.tsx:463` |
| 文件空态描述 | `往本地目录添加文件会自动上传；远端新增也会自动出现` | `SyncPage.tsx:464` |

文件状态徽章与统计（渲染条件：数量 > 0 才显示）：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 状态徽章 | `已同步` / `待上传` / `校验中` / `上传中` / `服务端处理中` / `下载中` / `失败` / `在线` | `SyncPage.tsx:64-73`（FILE_STATE_LABEL） |
| 传输中徽章带进度 | `上传中 XXX%` / `下载中 XXX%` | `SyncPage.tsx:568` |
| 统计行 | `共 XXX 项` | `SyncPage.tsx:471` |
| 统计行 | `已同步 XXX` | `SyncPage.tsx:472` |
| 统计行 | `待上传 XXX` | `SyncPage.tsx:474` |
| 统计行 | `校验中 XXX` | `SyncPage.tsx:477` |
| 统计行 | `上传中 XXX` | `SyncPage.tsx:480` |
| 统计行 | `处理中 XXX` | `SyncPage.tsx:483` |
| 统计行 | `下载中 XXX` | `SyncPage.tsx:486` |
| 统计行 | `失败 XXX` | `SyncPage.tsx:489` |
| 统计行 | `在线 XXX` | `SyncPage.tsx:491` |
| 渲染上限提示 | `（仅显示前 XXX 项）` | `SyncPage.tsx:493` |
| 目录行大小列 | `目录` | `SyncPage.tsx:558` |

「正在同步」区块：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 区块标题 | `正在同步 XXX 项` | `SyncPage.tsx:501` |
| 状态动作文案 | `正在校验` / `正在上传` / `服务端合并校验中` / `正在下载` / `等待上传` / `上传失败` | `SyncPage.tsx:95-103`（stateAction） |
| 剩余提示 | `还有 XXX 项…` | `SyncPage.tsx:534` |
| 区块说明 | `上传期间文件以分片暂存在服务端临时目录；传完还要服务端合并校验，全部通过后才会出现在远端目录。「校验中 100%」= 哈希算完、即将开始传；「服务端处理中 100%」= 分片已传完、正在合并，稍等即可。` | `SyncPage.tsx:538-541`（源码含换行排版，成句如上） |

新建/编辑弹窗：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 弹窗标题（新建） | `新建挂载点` | `SyncPage.tsx:588` |
| 弹窗标题（编辑） | `编辑挂载点` | `SyncPage.tsx:588` |
| 取消按钮 | `取消` | `SyncPage.tsx:595` |
| 保存按钮 | `保存` | `SyncPage.tsx:598` |
| 表单标签 | `名称` | `SyncPage.tsx:605` |
| 名称 placeholder | `例如：照片库` | `SyncPage.tsx:608` |
| 表单标签 | `远端磁盘` | `SyncPage.tsx:615` |
| 磁盘选择占位 | `请选择磁盘` | `SyncPage.tsx:623` |
| 表单标签 | `远端子目录` | `SyncPage.tsx:657` |
| 子目录 placeholder | `磁盘内子目录，留空表示根目录` | `SyncPage.tsx:661` |
| 子目录选择按钮 | `选择…` | `SyncPage.tsx:671` |
| 表单标签 | `本地缓存目录` | `SyncPage.tsx:677` |
| 本地目录 placeholder | `选择或粘贴本地目录绝对路径` | `SyncPage.tsx:681` |
| 本地目录浏览按钮 | `浏览…` | `SyncPage.tsx:686` |
| 弹窗说明 | `新建后开关默认关闭；打开后本地目录与远端自动保持一致。本地删除只删本地缓存，不会删除远端文件。` | `SyncPage.tsx:692-693`（源码两行，成句如上） |
| 删除确认弹窗标题 | `确认删除` | `SyncPage.tsx:714` |
| 删除确认弹窗正文 | `确定要删除挂载点「XXX」吗？远端文件与本地已下载的文件都不会被删除。` | `SyncPage.tsx:715` |
| 删除确认按钮 | `删除` | `SyncPage.tsx:716` |
| 本地目录选择对话框标题 | `选择本地缓存目录` | `SyncPage.tsx:308` |

错误/提示文案（Toast）：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 读取挂载点失败 | `读取挂载点失败` | `SyncPage.tsx:145` |
| 读取文件列表失败 Toast | `读取文件列表失败`（兜底 `未知错误`） | `SyncPage.tsx:160` |
| 更新成功 Toast | `挂载点已更新` | `SyncPage.tsx:256` |
| 创建成功 Toast | `挂载点已创建`（副文案 `开关默认关闭，请手动打开`） | `SyncPage.tsx:259` |
| 保存失败 Toast | `保存失败` | `SyncPage.tsx:264` |
| 启用成功 Toast | `挂载点已启用` | `SyncPage.tsx:277` |
| 停用成功 Toast | `挂载点已停用` | `SyncPage.tsx:277` |
| 启用失败 Toast | `启用失败` | `SyncPage.tsx:279` |
| 停用失败 Toast | `停用失败` | `SyncPage.tsx:279` |
| 同步失败 Toast | `同步失败` | `SyncPage.tsx:290` |
| 删除成功 Toast | `挂载点已删除`（副文案 `本地已下载的文件不会被删除`） | `SyncPage.tsx:300` |
| 删除失败 Toast | `删除失败` | `SyncPage.tsx:302` |
| 选择目录失败 Toast | `选择目录失败` | `SyncPage.tsx:311` |

## 9. 传输任务页（pages/TransferTabsPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题（传输页签） | `传输任务` | `TransferTabsPage.tsx:39` |
| 页面副标题（传输页签） | `上传与下载进度` | `TransferTabsPage.tsx:40` |
| 页面标题（修复页签） | `修复中心` | `TransferTabsPage.tsx:39` |
| 页面副标题（修复页签） | `共享修复任务与产物管理` | `TransferTabsPage.tsx:40` |
| 页签选项 | `传输` / `修复` | `TransferTabsPage.tsx:46-47` |

传输页签内容为 `TransferPage` 的 `TransferTasksView`（`TransferTabsPage.tsx:52`），其自身文案不在本清单的 10 个指定文件范围内（见「未决问题」）。

## 10. 修复中心页（pages/RepairCenterPage.tsx）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 页面标题（独立路由 `/repair`） | `修复中心` | `RepairCenterPage.tsx:39` |
| 页面副标题（独立路由） | `共享修复任务与产物管理` | `RepairCenterPage.tsx:39` |
| 页面说明文字 | `对损坏的视频/音频发起离线修复：修复产物验证播放满意后，可覆盖原文件（原文件将被直接删除、不可恢复）。在「文件管理」中右键文件即可发起修复。` | `RepairCenterPage.tsx:207-208`（源码两行，成句如上） |
| 加载提示 | `加载修复任务…` | `RepairCenterPage.tsx:135` |
| 空态标题 | `暂无修复任务` | `RepairCenterPage.tsx:227` |
| 空态描述 | `在文件管理中右键损坏的视频/音频文件，选择「修复损坏媒体」` | `RepairCenterPage.tsx:228` |
| 任务状态徽章 | `排队中` / `执行中` / `验证中` / `修复成功` / `修复失败` / `已取消` / `已覆盖` | `RepairCenterPage.tsx:13-19`（STATUS_TEXT） |
| 取消按钮（排队中/执行中/验证中） | `取消` | `RepairCenterPage.tsx:276` |
| 验证播放按钮（修复成功） | `验证播放` | `RepairCenterPage.tsx:287` |
| 覆盖原文件按钮（修复成功） | `覆盖原文件` | `RepairCenterPage.tsx:295` |
| 删除产物按钮（修复成功） | `删除产物` | `RepairCenterPage.tsx:303` |
| 删除记录按钮（失败/已取消/已覆盖） | `删除记录` | `RepairCenterPage.tsx:314` |
| 覆盖确认弹窗标题 | `覆盖原文件（不可恢复）` | `RepairCenterPage.tsx:333` |
| 覆盖确认弹窗正文 | `即将用修复产物覆盖「XXX」。` | `RepairCenterPage.tsx:349` |
| 覆盖确认弹窗警示 | `原损坏文件将被直接删除、无法恢复！` | `RepairCenterPage.tsx:352` |
| 覆盖确认弹窗提示 | `请确认已通过「验证播放」确认修复产物可用。` | `RepairCenterPage.tsx:355` |
| 覆盖确认弹窗取消 | `取消` | `RepairCenterPage.tsx:340` |
| 覆盖确认按钮 | `确认覆盖` | `RepairCenterPage.tsx:343` |
| 任务数提示 | `共 XXX 条任务，仅展示最近 XXX 条` | `RepairCenterPage.tsx:323` |

错误/提示文案：

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 加载失败 | `加载修复任务失败` | `RepairCenterPage.tsx:74` |
| 操作失败提示 | `操作失败：XXX`（`操作失败：${message}`） | `RepairCenterPage.tsx:110` |
| 取消成功提示 | `任务已取消` | `RepairCenterPage.tsx:147` |
| 覆盖成功提示 | `已覆盖原文件` | `RepairCenterPage.tsx:131` |
| 删除产物成功提示 | `产物已删除` | `RepairCenterPage.tsx:151` |
| 删除记录成功提示 | `记录已删除` | `RepairCenterPage.tsx:153` |
