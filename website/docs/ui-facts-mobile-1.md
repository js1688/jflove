# 移动端 UI 文案事实清单 · 第 1 部分

> 全部从代码实测提取，evidence 为 `文件:行`（相对 `jflove-app/lib/`）。官网指引只能引用本文件出现过的文案。
> 约定：动态变量（文件名、数量、百分比、路径等）一律以 `XXX` 代替；文案内的换行记作 `\n`；文案原样照抄（含标点、空格、全角/半角冒号差异）。
> 覆盖范围：`app.dart` + `pages/` 下 10 个页面（登录 / 文件管理 / 磁盘浏览 / 文件预览 / 笔记列表 / 笔记编辑 / 传输 / 离线下载中心 / 新建离线任务 / 修复中心）。

## 1. 底部导航（lib/app.dart）

底部导航栏共 6 项（`NavigationDestination` 顺序即排列顺序）。

| # | 菜单名 | evidence |
| --- | --- | --- |
| 1 | `文件` | `app.dart:177` |
| 2 | `笔记` | `app.dart:182` |
| 3 | `传输任务` | `app.dart:187` |
| 4 | `离线下载` | `app.dart:192` |
| 5 | `相册同步` | `app.dart:197` |
| 6 | `设置` | `app.dart:202` |

## 2. 登录页（lib/pages/login/login_page.dart）

本页无 AppBar（品牌光晕整屏布局，`login_page.dart:79`）。

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `JFLove` | 品牌标题（渐变字） | `login_page.dart:120` |
| `私有文档 & 笔记管理` | 标题下副标语 | `login_page.dart:130` |
| `服务器地址` | 表单 labelText | `login_page.dart:160` |
| `http://192.168.1.100:8989` | 服务器地址 hintText | `login_page.dart:161` |
| `用户名` | 表单 labelText | `login_page.dart:176` |
| `密码` | 表单 labelText | `login_page.dart:187` |
| `登录有效期` | 下拉表单 labelText | `login_page.dart:197` |
| `1 天` / `7 天` / `30 天` | 登录有效期下拉选项 | `login_page.dart:35` |
| `登录` | 主按钮（渐变 PrimaryGradientButton label） | `login_page.dart:256` |
| `请输入服务器地址` | 错误提示（页内红字） | `login_page.dart:280` |
| `请输入用户名和密码` | 错误提示（页内红字） | `login_page.dart:284` |

## 3. 磁盘浏览页（lib/pages/files/disk_browser_page.dart）

进入某个磁盘后的文件列表页。文件操作入口 = **长按列表项 → 底部弹层**。

### 3.1 AppBar

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `XXX`（磁盘名称，动态） | AppBar 标题（取磁盘列表中的名称） | `disk_browser_page.dart:532` |
| `磁盘 #XXX` | AppBar 标题兜底（取不到磁盘名时） | `disk_browser_page.dart:118` |

### 3.2 顶部图标按钮 tooltip

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `上传文件` | 上传图标按钮 tooltip（有写权限时） | `disk_browser_page.dart:546` |
| `新建目录` | 新建目录图标按钮 tooltip（有写权限时） | `disk_browser_page.dart:551` |
| `无写权限` | 上述两按钮无写权限时的 tooltip（按钮保留但禁用） | `disk_browser_page.dart:546`、`disk_browser_page.dart:551` |

### 3.3 长按底部弹层菜单（showModalBottomSheet）

对文件：下载 / 预览 /（仅媒体文件）修复损坏媒体 / 重命名 / 移动到… / 删除；对目录：重命名 / 移动到… / 删除（无下载/预览/修复）。

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `下载` | 长按菜单项（仅文件） | `disk_browser_page.dart:159` |
| `预览` | 长按菜单项（仅文件） | `disk_browser_page.dart:167` |
| `修复损坏媒体` | 长按菜单项（仅音视频扩展名） | `disk_browser_page.dart:181` |
| `重命名` | 长按菜单项 | `disk_browser_page.dart:196` |
| `移动到…` | 长按菜单项 | `disk_browser_page.dart:212` |
| `删除` | 长按菜单项（危险色） | `disk_browser_page.dart:229` |

### 3.4 弹窗

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `重命名` | 重命名弹窗标题 | `disk_browser_page.dart:332` |
| `新名称` | 重命名弹窗 labelText | `disk_browser_page.dart:335` |
| `取消` | 重命名弹窗按钮 | `disk_browser_page.dart:341` |
| `确认` | 重命名弹窗按钮 | `disk_browser_page.dart:361` |
| `确认删除` | 删除确认弹窗标题 | `disk_browser_page.dart:401` |
| `确定要删除「XXX」吗？` | 删除确认弹窗内容 | `disk_browser_page.dart:402` |
| `取消` | 删除确认弹窗按钮 | `disk_browser_page.dart:406` |
| `删除` | 删除确认弹窗按钮（危险色） | `disk_browser_page.dart:414` |
| `新建目录` | 新建目录弹窗标题 | `disk_browser_page.dart:475` |
| `目录名称` | 新建目录弹窗 labelText | `disk_browser_page.dart:478` |
| `取消` | 新建目录弹窗按钮 | `disk_browser_page.dart:484` |
| `创建` | 新建目录弹窗按钮 | `disk_browser_page.dart:488` |

### 3.5 空态与 SnackBar

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `此目录为空` | 空态标题 | `disk_browser_page.dart:572` |
| `可上传文件或新建目录` | 空态副文案 | `disk_browser_page.dart:573` |
| `下载任务已添加: XXX\n保存位置: XXX` | SnackBar（添加下载任务成功） | `disk_browser_page.dart:279` |
| `添加下载任务失败: XXX` | SnackBar | `disk_browser_page.dart:288` |
| `已加入修复队列，可在「修复中心」查看进度` | SnackBar（发起修复成功） | `disk_browser_page.dart:315` |
| `发起修复失败：XXX` | SnackBar（全角冒号） | `disk_browser_page.dart:321` |
| `重命名失败: XXX` | SnackBar | `disk_browser_page.dart:357` |
| `「XXX」已移动` | SnackBar（移动成功） | `disk_browser_page.dart:386` |
| `移动失败: XXX` | SnackBar | `disk_browser_page.dart:392` |
| `删除失败: XXX` | SnackBar | `disk_browser_page.dart:430` |
| `已加入传输队列: XXX 个文件` | SnackBar（选择文件上传后） | `disk_browser_page.dart:463` |
| `创建失败: XXX` | SnackBar（新建目录失败） | `disk_browser_page.dart:515` |

## 4. 文件管理页（lib/pages/files/file_list_page.dart）

「文件」底部导航落地页 = 虚拟磁盘列表。

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `文件管理` | AppBar 标题 | `file_list_page.dart:28` |
| `离线下载中心` | AppBar 右上图标按钮 tooltip（跳离线下载） | `file_list_page.dart:33` |
| `暂无可用磁盘` | 空态标题 | `file_list_page.dart:43` |
| `请联系管理员分配磁盘权限` | 空态副文案 | `file_list_page.dart:44` |
| `路径: XXX` | 磁盘卡片路径行 | `file_list_page.dart:106` |
| `可写` / `只读` | 磁盘卡片读写权限徽标 | `file_list_page.dart:118` |

## 5. 文件预览页（lib/pages/files/file_preview_page.dart）

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `XXX`（文件名，动态） | AppBar 标题 | `file_preview_page.dart:56` |
| `不支持预览此文件类型` | 空态标题（不支持的格式） | `file_preview_page.dart:213` |
| `请下载后查看` | 空态副文案 | `file_preview_page.dart:214` |
| `加载失败: XXX` | 图片加载失败提示（半角冒号） | `file_preview_page.dart:235` |
| `会话密钥未就绪，请重新登录` | 媒体播放异常文案（展示于错误详情区） | `file_preview_page.dart:306` |
| `已加入修复队列，可在「修复中心」查看进度` | SnackBar（点「立即修复」成功） | `file_preview_page.dart:382` |
| `发起修复失败：XXX` | SnackBar（全角冒号） | `file_preview_page.dart:388` |
| `该文件已损坏，无法在线播放` | 媒体损坏错误标题 | `file_preview_page.dart:420` |
| `播放失败` | 媒体播放普通错误标题 | `file_preview_page.dart:420` |
| `立即修复` | FilledButton（损坏文件引导修复） | `file_preview_page.dart:441` |
| `正在加载视频…` | 视频加载中提示 | `file_preview_page.dart:458` |
| `正在加载音频…` | 音频加载中提示 | `file_preview_page.dart:458` |
| `正在播放音频` | 音频模式标题 | `file_preview_page.dart:514` |
| `[二进制文件，无法以文本方式显示]` | 文本预览兜底文案 | `file_preview_page.dart:766` |
| `加载失败：XXX` | 文本预览 ErrorState（全角冒号） | `file_preview_page.dart:792` |
| `文件过大，已截断旧内容` | 大文本截断提示条（Markdown 与纯文本两处） | `file_preview_page.dart:805`、`file_preview_page.dart:847` |

## 6. 笔记列表页（lib/pages/notes/note_list_page.dart）

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `笔记管理` | AppBar 标题 | `note_list_page.dart:213` |
| `搜索笔记…` | 搜索框 hintText | `note_list_page.dart:221` |
| `新建笔记` | 新建笔记弹窗标题 | `note_list_page.dart:82` |
| `文件名（.md 结尾）` | 新建笔记弹窗 labelText | `note_list_page.dart:85` |
| `我的笔记` | 新建笔记弹窗 hintText | `note_list_page.dart:86` |
| `取消` | 新建笔记弹窗按钮 | `note_list_page.dart:93` |
| `创建` | 新建笔记弹窗按钮 | `note_list_page.dart:97` |
| `重命名` | 重命名弹窗标题 | `note_list_page.dart:124` |
| `新文件名` | 重命名弹窗 labelText | `note_list_page.dart:127` |
| `取消` | 重命名弹窗按钮 | `note_list_page.dart:133` |
| `确认` | 重命名弹窗按钮 | `note_list_page.dart:155` |
| `确认删除` | 删除确认弹窗标题 | `note_list_page.dart:166` |
| `确定要删除「XXX」吗？` | 删除确认弹窗内容 | `note_list_page.dart:168` |
| `取消` | 删除确认弹窗按钮 | `note_list_page.dart:178` |
| `删除` | 删除确认弹窗按钮（危险色） | `note_list_page.dart:186` |
| `暂无笔记` | 空态标题 | `note_list_page.dart:254` |
| `点击右下角按钮新建笔记` | 空态副文案 | `note_list_page.dart:255` |
| `更多操作` | 列表项行尾三点菜单（PopupMenuButton）tooltip | `note_list_page.dart:316` |
| `重命名` | 三点菜单项 | `note_list_page.dart:336` |
| `删除` | 三点菜单项（危险色） | `note_list_page.dart:357` |
| `新建笔记` | 右下角浮动按钮（FAB）tooltip | `note_list_page.dart:386` |
| `创建失败: XXX` | SnackBar | `note_list_page.dart:113` |
| `重命名失败: XXX` | SnackBar | `note_list_page.dart:151` |
| `删除失败: XXX` | SnackBar | `note_list_page.dart:201` |

## 7. 笔记编辑页（lib/pages/notes/note_edit_page.dart）

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `XXX`（笔记文件名，动态） | AppBar 标题 | `note_edit_page.dart:405` |
| `编辑` | AppBar 图标按钮 tooltip（当前为预览态时） | `note_edit_page.dart:409` |
| `预览` | AppBar 图标按钮 tooltip（当前为编辑态时） | `note_edit_page.dart:409` |
| `保存` | AppBar 图标按钮 tooltip | `note_edit_page.dart:424` |
| `未保存` | 返回拦截弹窗标题 | `note_edit_page.dart:129` |
| `当前笔记有未保存的修改，是否放弃？` | 返回拦截弹窗内容 | `note_edit_page.dart:131` |
| `放弃` | 返回拦截弹窗按钮 | `note_edit_page.dart:141` |
| `继续编辑` | 返回拦截弹窗按钮 | `note_edit_page.dart:145` |
| `已保存` | SnackBar（保存成功） | `note_edit_page.dart:162` |
| `保存失败: XXX` | SnackBar | `note_edit_page.dart:168` |
| `插入图表` | 「插入图表」底部弹层标题 | `note_edit_page.dart:228` |
| `插入后可在编辑区修改源码，预览区会自动渲染成图；语法有误时只影响这一张图。` | 插入图表弹层底部说明 | `note_edit_page.dart:279` |
| `笔记加载失败` | ErrorState 标题 | `note_edit_page.dart:302` |
| `*暂无内容*` | 预览态空笔记占位（按 Markdown 斜体渲染） | `note_edit_page.dart:321` |
| `使用 Markdown 语法编写笔记…` | 编辑区 hintText | `note_edit_page.dart:355` |
| `加粗` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:454` |
| `斜体` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:458` |
| `标题` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:462` |
| `无序列表` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:466` |
| `有序列表` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:470` |
| `链接` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:474` |
| `图片` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:478` |
| `代码块` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:482` |
| `引用` | 编辑工具栏按钮 tooltip | `note_edit_page.dart:486` |
| `插入图表` | 工具栏右侧常驻图标按钮 tooltip | `note_edit_page.dart:513` |
| `预览模式` / `编辑模式` | 底部状态条模式指示 | `note_edit_page.dart:536` |
| `● 未保存` | 底部状态条未保存标记 | `note_edit_page.dart:543` |

## 8. 传输页（lib/pages/transfer/transfer_page.dart）

两个页签：`传输任务` + `修复中心`（修复中心内容见第 11 节）。

### 8.1 AppBar 与页签

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `传输任务` | AppBar 标题 | `transfer_page.dart:53` |
| `清除已完成` | AppBar 图标按钮 tooltip（仅「传输任务」页签显示） | `transfer_page.dart:59` |
| `刷新` | AppBar 图标按钮 tooltip | `transfer_page.dart:64` |
| `传输任务` | 页签一文字 | `transfer_page.dart:81` |
| `修复中心` | 页签二文字 | `transfer_page.dart:82` |

### 8.2 任务列表

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `暂无传输任务` | 空态标题 | `transfer_page.dart:127` |
| `在「文件」页面上传或下载文件` | 空态副文案 | `transfer_page.dart:128` |
| `共 XXX` | 顶部统计胶囊 | `transfer_page.dart:141` |
| `进行中 XXX` | 顶部统计胶囊 | `transfer_page.dart:145` |
| `完成 XXX` | 顶部统计胶囊 | `transfer_page.dart:150` |
| `失败 XXX` | 顶部统计胶囊 | `transfer_page.dart:155` |
| `传输任务加载失败` | ErrorState 标题 | `transfer_page.dart:174` |
| `取消任务` | 进行中任务行内取消图标按钮 tooltip | `transfer_page.dart:250` |
| `等待中` | 任务状态徽标 | `transfer_page.dart:326` |
| `校验中` | 任务状态徽标 | `transfer_page.dart:328` |
| `校验中 XXX%` | 任务状态徽标（有校验进度时） | `transfer_page.dart:327` |
| `传输中` | 任务状态徽标 | `transfer_page.dart:329` |
| `已完成` | 任务状态徽标 | `transfer_page.dart:331` |
| `失败` | 任务状态徽标 | `transfer_page.dart:333` |
| `已取消` | 任务状态徽标 | `transfer_page.dart:335` |
| `已校验 XXX / XXX（XXX%）` | 进度文本（校验阶段） | `transfer_page.dart:276-277` |
| `XXX / XXX（XXX%）` | 进度文本（传输阶段） | `transfer_page.dart:278-279` |
| `错误：XXX` | 任务错误详情 | `transfer_page.dart:285` |
| `保存至：XXX` | 下载完成后的保存位置行 | `transfer_page.dart:299` |
| `已清除 XXX 个已结束任务` | SnackBar（清除成功） | `transfer_page.dart:100` |
| `没有可清除的任务` | SnackBar（无可清除） | `transfer_page.dart:100` |

## 9. 离线下载中心页（lib/pages/offline/offline_download_page.dart）

### 9.1 AppBar 与入口

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `离线下载中心` | AppBar 标题 | `offline_download_page.dart:250` |
| `设置` | AppBar 图标按钮 tooltip（离线下载设置弹层） | `offline_download_page.dart:254` |
| `刷新` | AppBar 图标按钮 tooltip | `offline_download_page.dart:259` |
| `新建任务` | 右下角浮动按钮（FAB.extended）label | `offline_download_page.dart:267` |
| `新建任务` | FAB label（嵌入传输页页签时） | `offline_download_page.dart:242` |

### 9.2 空态与错误态

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `暂无离线下载任务` | 空态标题 | `offline_download_page.dart:310` |
| `点右下角「新建任务」发起，或等桌面端 / Web 端创建后同步显示` | 空态副文案 | `offline_download_page.dart:311` |
| `新建任务` | 空态内按钮（FilledButton label） | `offline_download_page.dart:314` |
| `任务加载失败` | ErrorState 标题 | `offline_download_page.dart:282` |
| `重新加载` | ErrorState 重试按钮 | `offline_download_page.dart:286` |

### 9.3 功能不可用提示条（顶部黄条 + 「去设置」）

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `服务端已关闭离线下载功能` | 不可用提示 | `offline_download_page.dart:154` |
| `你没有可写入的磁盘，暂时无法使用离线下载` | 不可用提示 | `offline_download_page.dart:157` |
| `尚未设置离线下载默认目录` | 不可用提示（引导去设置） | `offline_download_page.dart:161` |
| `离线下载默认目录已失效，请重新配置` | 不可用提示（引导去设置） | `offline_download_page.dart:163` |
| `离线下载当前不可用` | 不可用提示（兜底） | `offline_download_page.dart:169` |
| `尚未配置可用的默认目录` | 不可用提示（兜底，引导去设置） | `offline_download_page.dart:172` |
| `去设置` | 提示条内 TextButton | `offline_download_page.dart:399` |

### 9.4 任务卡片

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `来源 XXX` | 任务卡来源行（来源未知时为 `来源 未知站点`） | `offline_download_page.dart:477` |
| `XXX%` | 任务卡进度百分比 | `offline_download_page.dart:497` |
| `XXX / XXX` | 任务卡已下载字节数 | `offline_download_page.dart:508-509` |
| `XXX/s` | 任务卡下载速度 | `offline_download_page.dart:519` |
| `产物项 XXX/XXX` | 任务卡产物项计数行 | `offline_download_page.dart:527` |
| `目标 XXX` | 任务卡目标目录行 | `offline_download_page.dart:533` |
| `创建于 XXX` | 任务卡创建时间行 | `offline_download_page.dart:551` |
| `去选择` | 任务卡操作按钮（状态 ready 时） | `offline_download_page.dart:564` |
| `取消` | 任务卡操作按钮 | `offline_download_page.dart:566` |
| `重试` | 任务卡操作按钮 | `offline_download_page.dart:568` |
| `删除记录` | 任务卡操作按钮（危险色） | `offline_download_page.dart:575` |
| `任务已取消` | SnackBar | `offline_download_page.dart:347` |
| `已重新提交` | SnackBar | `offline_download_page.dart:351` |
| `任务记录已删除` | SnackBar | `offline_download_page.dart:357` |
| `操作失败` | SnackBar 兜底文案（配合错误原因展示） | `offline_download_page.dart:211` |

### 9.5 任务详情底部弹层

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `任务详情` | 弹层标题 | `offline_download_page.dart:770` |
| `关闭` | 弹层右上角图标按钮 tooltip | `offline_download_page.dart:781` |
| `状态` | 详情行标签 | `offline_download_page.dart:791` |
| `—` | 详情行空值占位 | `offline_download_page.dart:792` |
| `来源域名` | 详情行标签 | `offline_download_page.dart:795` |
| `未知站点` | 来源域名空值占位 | `offline_download_page.dart:796` |
| `产物名` | 详情行标签 | `offline_download_page.dart:798` |
| `目标目录` | 详情行标签 | `offline_download_page.dart:799` |
| `进度` | 详情行标签 | `offline_download_page.dart:801` |
| `产物项` | 详情行标签 | `offline_download_page.dart:810` |
| `创建时间` | 详情行标签 | `offline_download_page.dart:813` |
| `开始时间` | 详情行标签 | `offline_download_page.dart:814` |
| `完成时间` | 详情行标签 | `offline_download_page.dart:816` |
| `失败原因` | 详情行标签（危险色） | `offline_download_page.dart:819` |
| `解析` | 详情行标签 | `offline_download_page.dart:828` |
| `解析超时，已返回部分结果（可重试补充）` | 解析行取值 | `offline_download_page.dart:829` |
| `选择要下载的项（已全选，可取消勾选）` | ready 态勾选说明 | `offline_download_page.dart:845` |
| `清单` | 清单区标题 | `offline_download_page.dart:854` |
| `XXX 项` | 清单计数 | `offline_download_page.dart:864` |
| `重新加载` | 清单加载失败重试按钮 | `offline_download_page.dart:877` |
| `该任务没有可展示的清单项` | 清单为空提示 | `offline_download_page.dart:883` |
| `目标：XXX` | 目标目录行（未选择时显示 `目标：未选择`） | `offline_download_page.dart:917` |
| `选择` | 目标目录选择按钮 | `offline_download_page.dart:923` |
| `打包为 zip` | SwitchListTile 开关项 | `offline_download_page.dart:933` |
| `开始下载` | 提交按钮（FilledButton） | `offline_download_page.dart:944` |
| `追加下一页` | 追加图片页按钮 | `offline_download_page.dart:956` |
| `取消任务` | 弹层底部操作按钮 | `offline_download_page.dart:973` |
| `重试` | 弹层底部操作按钮 | `offline_download_page.dart:987` |
| `删除记录` | 弹层底部操作按钮（危险色） | `offline_download_page.dart:1000` |
| `已追加 XXX 张图片` | SnackBar（追加图片页成功） | `offline_download_page.dart:684` |
| `请至少勾选一项` | 提交校验错误提示 | `offline_download_page.dart:716` |
| `请选择目标目录` | 提交校验错误提示 | `offline_download_page.dart:720` |
| `已开始下载` | SnackBar（提交成功） | `offline_download_page.dart:733` |
| `未选择` | 目标目录未选占位 | `offline_download_page.dart:746` |
| `磁盘根目录` | 目标路径为空时占位 | `offline_download_page.dart:746` |
| `选择目标目录` | 目录选择弹层标题 | `offline_download_page.dart:705` |
| `任务详情加载失败` | 详情加载失败兜底文案 | `offline_download_page.dart:649` |

## 10. 新建离线下载任务页（lib/pages/offline/offline_new_task_page.dart）

### 10.1 AppBar 与表单

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `新建离线下载任务` | AppBar 标题 | `offline_new_task_page.dart:381` |
| `网页地址` | 网址输入卡标签 | `offline_new_task_page.dart:421` |
| `https://example.com/video` | 网址输入框 hintText | `offline_new_task_page.dart:434` |
| `仅支持公网 http(s) 页面；不支持登录态站点 / DRM / 直播。` | 网址输入说明 | `offline_new_task_page.dart:443` |
| `分析` | 分析按钮（FilledButton label） | `offline_new_task_page.dart:456` |
| `取消` | 分析中取消按钮（OutlinedButton label） | `offline_new_task_page.dart:451` |
| `正在分析页面，最多 30 秒…` | 分析中提示 | `offline_new_task_page.dart:466` |
| `重试分析` | 分析失败重试按钮（OutlinedButton label） | `offline_new_task_page.dart:485` |
| `配置加载失败` | ErrorState 标题 | `offline_new_task_page.dart:396` |
| `重新加载` | ErrorState 重试按钮 | `offline_new_task_page.dart:399` |

### 10.2 网址校验与不可用提示

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `请输入网页地址` | 网址校验错误 | `offline_new_task_page.dart:134` |
| `请输入 http(s) 开头的公开网页地址` | 网址校验错误 | `offline_new_task_page.dart:139` |
| `当前不可用` | 不可用兜底提示 | `offline_new_task_page.dart:151` |
| `服务端已关闭离线下载功能` | 不可用提示（同 9.3） | `offline_new_task_page.dart:98` |
| `你没有可写入的磁盘，暂时无法使用离线下载` | 不可用提示（同 9.3） | `offline_new_task_page.dart:99` |
| `尚未设置离线下载默认目录` | 不可用提示（同 9.3） | `offline_new_task_page.dart:100` |
| `离线下载默认目录已失效，请重新配置` | 不可用提示（同 9.3） | `offline_new_task_page.dart:101` |
| `去设置` | 不可用提示卡操作按钮 | `offline_new_task_page.dart:411` |
| `分析失败，请重试` | 分析异常兜底文案 | `offline_new_task_page.dart:175` |
| `XXX（已重试 3 次，请检查网络后重新分析）` | 轮询失败 3 次错误文案 | `offline_new_task_page.dart:241` |
| `取消分析失败` | 取消分析异常兜底文案 | `offline_new_task_page.dart:264` |

### 10.3 分析结果（清单勾选与提交）

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `没有可下载的项` | 空态标题（分析结果为空） | `offline_new_task_page.dart:507` |
| `换一个地址重新分析` | 空态副文案 | `offline_new_task_page.dart:508` |
| `清单` | 清单卡标题 | `offline_new_task_page.dart:527` |
| `XXX 项` | 清单计数 | `offline_new_task_page.dart:537` |
| `全选` | 快捷勾选 ActionChip | `offline_new_task_page.dart:546` |
| `只要音频` | 快捷勾选 ActionChip | `offline_new_task_page.dart:548` |
| `只要图片` | 快捷勾选 ActionChip | `offline_new_task_page.dart:552` |
| `最高清` | 快捷勾选 ActionChip | `offline_new_task_page.dart:555` |
| `清单里没有该类型的项` | 快捷勾选无匹配提示 | `offline_new_task_page.dart:349` |
| `已选 XXX 项 · XXX` | 已选汇总行（估算大小时为 `已选 XXX 项 · 约 XXX`） | `offline_new_task_page.dart:514-515` |
| `目标：XXX` | 目标目录行 | `offline_new_task_page.dart:603` |
| `选择` | 目标目录选择按钮 | `offline_new_task_page.dart:610` |
| `选择目标目录` | 目录选择弹层标题 | `offline_new_task_page.dart:285` |
| `未选择` | 目标目录未选占位 | `offline_new_task_page.dart:650` |
| `磁盘根目录` | 目标路径为空时占位 | `offline_new_task_page.dart:651` |
| `打包为 zip` | SwitchListTile 开关项 | `offline_new_task_page.dart:620` |
| `开始下载` | 提交按钮（FilledButton label） | `offline_new_task_page.dart:632` |
| `提交中…` | 提交按钮提交中态 | `offline_new_task_page.dart:632` |
| `请至少勾选一项` | 提交校验错误提示 | `offline_new_task_page.dart:296` |
| `请选择目标目录` | 提交校验错误提示 | `offline_new_task_page.dart:300` |
| `没有可写入的磁盘` | SnackBar（无可写磁盘） | `offline_new_task_page.dart:275` |
| `开始下载失败` | 提交异常兜底文案 | `offline_new_task_page.dart:323` |

## 11. 修复中心（lib/pages/repair/repair_center_page.dart）

> 独立路由 `/repair` 仍可用（自带 AppBar）；常态入口 = 「传输任务」底部导航 → 第二个页签「修复中心」。

### 11.1 AppBar 与状态

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `修复中心` | AppBar 标题（独立路由形态） | `repair_center_page.dart:243` |
| `刷新` | AppBar 图标按钮 tooltip | `repair_center_page.dart:247` |
| `排队中` | 任务状态徽标 | `repair_center_page.dart:54` |
| `执行中` | 任务状态徽标 | `repair_center_page.dart:55` |
| `验证中` | 任务状态徽标 | `repair_center_page.dart:56` |
| `修复成功` | 任务状态徽标 | `repair_center_page.dart:57` |
| `修复失败` | 任务状态徽标 | `repair_center_page.dart:58` |
| `已取消` | 任务状态徽标 | `repair_center_page.dart:59` |
| `已覆盖` | 任务状态徽标 | `repair_center_page.dart:60` |
| `执行中 XXX%` | 执行中任务徽标（带进度百分比） | `repair_center_page.dart:315` |
| `由 XXX 发起 · XXX` | 任务卡发起人与大小行 | `repair_center_page.dart:323-324` |

### 11.2 空态与错误态

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `暂无修复任务` | 空态标题 | `repair_center_page.dart:271` |
| `对损坏的音视频文件长按 →「修复损坏媒体」即可发起修复` | 空态副文案（含移动端长按交互指引） | `repair_center_page.dart:272` |
| `修复任务加载失败` | ErrorState 标题 | `repair_center_page.dart:263` |

### 11.3 任务操作按钮与弹窗

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `取消` | 进行中任务操作按钮（TextButton） | `repair_center_page.dart:367` |
| `验证播放` | 修复成功任务操作按钮（TextButton） | `repair_center_page.dart:376` |
| `覆盖原文件` | 修复成功任务行尾三点菜单项 | `repair_center_page.dart:396` |
| `删除产物` | 修复成功任务行尾三点菜单项 | `repair_center_page.dart:401` |
| `删除记录` | 终态任务操作按钮（TextButton） | `repair_center_page.dart:416` |
| `覆盖原文件（不可恢复）` | 覆盖确认弹窗标题 | `repair_center_page.dart:185` |
| `即将用修复产物覆盖「XXX」。\n\n注意：原损坏文件将被直接删除、无法恢复！\n请确认已通过「验证播放」确认修复产物可用。` | 覆盖确认弹窗内容 | `repair_center_page.dart:187-189` |
| `取消` | 覆盖确认弹窗按钮 | `repair_center_page.dart:194` |
| `确认覆盖` | 覆盖确认弹窗按钮 | `repair_center_page.dart:198` |
| `删除修复产物` | 删除产物确认弹窗标题 | `repair_center_page.dart:384` |
| `确认删除「XXX」的修复产物？` | 删除产物确认弹窗内容 | `repair_center_page.dart:386` |
| `删除任务记录` | 删除记录确认弹窗标题 | `repair_center_page.dart:411` |
| `确认删除「XXX」的任务记录？` | 删除记录确认弹窗内容 | `repair_center_page.dart:412` |
| `取消` | 通用确认弹窗按钮 | `repair_center_page.dart:225` |
| `确认` | 通用确认弹窗按钮 | `repair_center_page.dart:229` |

### 11.4 SnackBar

| 文案 | 位置/类型 | evidence |
| --- | --- | --- |
| `任务已取消` | SnackBar（取消成功） | `repair_center_page.dart:364` |
| `已覆盖原文件` | SnackBar（覆盖成功） | `repair_center_page.dart:207` |
| `产物已删除` | SnackBar（删除产物成功） | `repair_center_page.dart:388` |
| `记录已删除` | SnackBar（删除记录成功） | `repair_center_page.dart:414` |
| `操作失败：XXX` | SnackBar（操作异常，全角冒号） | `repair_center_page.dart:160` |

---

## 提取校对说明（供官网指引撰写者）

1. 底部导航 6 项与 `app.dart:170-203` 的已知事实核对一致：文件 / 笔记 / 传输任务 / 离线下载 / 相册同步 / 设置。
2. 磁盘浏览页顶部 tooltip 与已知事实核对一致：`上传文件`、`新建目录`，无写权限时两者 tooltip 均为 `无写权限`（按钮保留但禁用，与桌面/Web 端「按钮整组消失」不同）。
3. 长按文件底部菜单与已知事实核对一致：下载 / 预览 / 修复损坏媒体（仅媒体文件且需写+删权限）/ 重命名 / 移动到… / 删除。
4. 注意冒号差异（原样照抄，勿"纠正"）：`发起修复失败：XXX`、`操作失败：XXX`、`保存至：XXX`、`加载失败：XXX`（文本预览 ErrorState）用全角冒号；`下载任务已添加: XXX`、`添加下载任务失败: XXX`、`重命名失败: XXX` 等用半角冒号。
5. 「移动到…」「正在加载视频…」等使用 U+2026 省略号，非三个句点。
