# 移动端 UI 文案事实清单 · 第 2 部分

> 全部从代码实测提取，evidence 为 `文件:行`。官网指引只能引用本文件出现过的文案。
> evidence 中的文件名均相对 `jflove-app/lib/`（`theme_provider.dart` 位于 `providers/`，widgets 组件位于 `widgets/`）。
> 约定：动态变量一律以 `XXX` 代替；同一控件有条件分支时用 ` / ` 分隔各分支文案（均须照抄对应分支）；
> 跨行 Dart 相邻字符串已按拼接后的完整句子抄录；说明性注记只出现在「控件」列，「精确文案」列均为可照抄文案。

## 1. 相册同步（lib/pages/album/album_backup_page.dart，移动端专属）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 相册同步 | album_backup_page.dart:51 |
| 活动卡阶段文字（动态消息，来自相册备份状态流） | XXX | album_backup_page.dart:121-124 |
| 活动卡「停止」按钮 | 停止 | album_backup_page.dart:129 |
| 活动卡进度计数（已处理 / 总数） | XXX / XXX | album_backup_page.dart:138 |
| 活动卡进行中提示 | 正在处理… | album_backup_page.dart:138 |
| 按钮（满宽 tonal） | 在文件中查看已上传 | album_backup_page.dart:65 |
| 按钮（文字） | 去传输任务查看上传明细 | album_backup_page.dart:74 |
| 页脚说明 | 本页只展示同步概况；上传明细与失败记录在「传输任务」中查看。 | album_backup_page.dart:79 |
| SnackBar（未配置远端目录时点「在文件中查看已上传」） | 请先选择远端目标目录 | album_backup_page.dart:29 |

## 2. 相册自动同步设置卡（lib/pages/settings/album_backup_card.dart）

> 同时被两处复用：设置页「笔记目录」卡片之后（见 §3）与相册同步页（§1）。

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 卡片标题 | 相册自动同步 | album_backup_card.dart:280 |
| 卡片说明 | 开启后，新的照片/视频会在满足条件时自动上传到你指定的远端目录。只在客户端保存配置，服务端不记录。 | album_backup_card.dart:298-299 |
| 管理员提示条（仅 admin 可见） | 当前为管理员账号：管理员可写入所有磁盘，请确认目标目录合适。 | album_backup_card.dart:312 |
| SwitchListTile 标题 | 自动同步相册 | album_backup_card.dart:320 |
| SwitchListTile 副文 | 默认关闭；关闭后不再产生新的上传任务 | album_backup_card.dart:321 |
| 目录行标题 | 远端目标目录 | album_backup_card.dart:329 |
| 目录行副文（已配置；格式为 磁盘 #ID · 路径） | 磁盘 #XXX · XXX | album_backup_card.dart:332-333 |
| 目录行副文（已配置且路径为空时） | 根目录 | album_backup_card.dart:333 |
| 目录行副文（未配置） | 未选择 | album_backup_card.dart:334 |
| 目录行「选择」按钮 | 选择 | album_backup_card.dart:338 |
| 开关 | 备份图片 | album_backup_card.dart:343 |
| 开关 | 备份视频 | album_backup_card.dart:348 |
| 开关 | 仅 Wi-Fi | album_backup_card.dart:353 |
| 开关 | 仅充电时 | album_backup_card.dart:358 |
| 小节标题 | 同步明细 | album_backup_card.dart:370 |
| 明细行标签 | 图片 | album_backup_card.dart:382 |
| 明细行标签 | 视频 | album_backup_card.dart:388 |
| 明细表表头（4 列） | 类型 / 总数 / 已同步 / 待同步 | album_backup_card.dart:621-624 |
| 小节标题 | 任务状态 | album_backup_card.dart:398 |
| 状态行 | 待备份 → XXX 项 | album_backup_card.dart:406 |
| 状态行 | 上传中 → XXX 项 | album_backup_card.dart:407 |
| 状态行 | 失败 → XXX 项 | album_backup_card.dart:408 |
| 状态行 | 不在范围 → XXX 项 | album_backup_card.dart:409 |
| 状态行 | 远端已存在跳过 → XXX 项 | album_backup_card.dart:411-412 |
| 状态行 | 等待远端索引（已暂停）→ XXX 项 | album_backup_card.dart:415-416 |
| 上次检测行（模板） | 上次检测：XXX · 新增上传 XXX 项 · 跳过 XXX 项 · 每 60 秒自动检测 | album_backup_card.dart:240 |
| 上次检测行（从未检测时） | 尚未检测 | album_backup_card.dart:235 |
| 索引行（未建立） | 远端索引：尚未建立（上传前会先建立） | album_backup_card.dart:245 |
| 索引行（已建立；时间为时:分） | 远端索引：XX:XX 建立 · 最多 10 分钟刷新一次（可手动全量核对） | album_backup_card.dart:248 |
| 索引暂缓提示 | 有上传任务进行中：远端索引暂不重建，上传完成后自动刷新 | album_backup_card.dart:431 |
| 索引不可用警告 | 远端索引不可用：这些目录已暂停上传（不会盲传），下轮自动重试；请检查网络/服务端后点「全量核对远端」。 | album_backup_card.dart:448 |
| 加载中文字 | 读取备份进度… | album_backup_card.dart:461 |
| 加载失败文字 | 读取备份进度失败：XXX | album_backup_card.dart:465 |
| 小节标题 | 操作 | album_backup_card.dart:475 |
| 按钮（主） | 立即检查并上传 | album_backup_card.dart:493 |
| 按钮（描边） | 重试失败项 | album_backup_card.dart:509 |
| 按钮（描边） | 全量核对远端 | album_backup_card.dart:524 |
| 按钮（文字，有失败项时显示） | 查看失败原因 | album_backup_card.dart:537 |
| 按钮（文字） | 后台运行设置 | album_backup_card.dart:544 |
| SnackBar | 已触发一次检查 | album_backup_card.dart:490 |
| SnackBar | 已重新排队失败项 | album_backup_card.dart:506 |
| SnackBar | 已开始全量核对远端 | album_backup_card.dart:521 |
| SnackBar（保存失败） | 保存失败：XXX | album_backup_card.dart:87 |
| SnackBar（未配置就开总开关） | 请先选择远端目标目录 | album_backup_card.dart:96 |
| SnackBar（未授权） | 未获得相册读取权限，请在系统设置中允许后重试 | album_backup_card.dart:103 |
| SnackBar（磁盘列表读取失败） | 读取磁盘列表失败：XXX | album_backup_card.dart:115 |
| SnackBar（无磁盘） | 没有可用的磁盘 | album_backup_card.dart:120 |
| 目录选择器标题 | 选择远端目标目录 | album_backup_card.dart:131 |
| 弹窗标题 | 后台运行设置 | album_backup_card.dart:174 |
| 弹窗正文（小米/Redmi） | 小米/Redmi：设置 → 应用设置 → 应用管理 → JFLove → 省电策略选「无限制」，并打开「自启动」。 | album_backup_card.dart:152-153 |
| 弹窗正文（华为/荣耀） | 华为/荣耀：设置 → 应用 → 应用启动管理 → JFLove → 改为「手动管理」，允许自启动/关联启动/后台活动。 | album_backup_card.dart:156-157 |
| 弹窗正文（OPPO/realme） | OPPO/realme：设置 → 电池 → 应用耗电管理 → JFLove → 允许后台运行与自启动。 | album_backup_card.dart:160-161 |
| 弹窗正文（vivo/iQOO） | vivo/iQOO：设置 → 电池 → 后台耗电管理 → JFLove → 允许后台高耗电；并在 i管家允许自启动。 | album_backup_card.dart:164-165 |
| 弹窗正文（其它品牌兜底） | 若备份在后台中断，请在系统设置中允许本应用「自启动 / 后台运行」，并关闭针对本应用的电池优化。 | album_backup_card.dart:168-169 |
| 弹窗按钮 | 知道了 | album_backup_card.dart:179 |
| 弹窗标题（XXX 为失败条数） | 失败原因（XXX 项） | album_backup_card.dart:200 |
| 弹窗空态 | 暂无失败记录 | album_backup_card.dart:202 |
| 失败条目类别（二选一） | 永久不可用 / 暂时失败 | album_backup_card.dart:210 |
| 失败条目副文（类别 · 已尝试 N 次） | XXX · 已尝试 XXX 次 | album_backup_card.dart:215 |
| 弹窗按钮 | 关闭 | album_backup_card.dart:223 |

## 3. 设置页（lib/pages/settings/settings_page.dart）

**页面卡片顺序（自上而下，共 7 张）**：服务器（:31，`_SectionCard`）→ 安全状态（:55，`_SectionCard`）→ 外观（:109，`AppCard`，标题文案在 :350）→ 笔记目录（:112，`_SectionCard`，标题在 :719）→ 账号（:116，`_SectionCard`）→ 管理面板（:168，`_SectionCard`，**仅管理员可见**）→ 关于（:203，`_SectionCard`）。
> ⚠ 与「已知事实」核对：5 个分区名（服务器/安全状态/账号/管理面板/关于）全部在码，**但页面实际可见卡片为 7 张**——「安全状态」与「账号」之间还有「外观」与「笔记目录」两张卡。官网若写"设置页 5 个分区"会漏项；普通用户可见 6 张（无管理面板）。

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 设置 | settings_page.dart:25 |
| 分区标题 | 服务器 | settings_page.dart:31 |
| 信息行（当前地址） | 当前地址 → XXX | settings_page.dart:37-38 |
| 信息行（当前地址未填时） | 未设置 | settings_page.dart:38 |
| 按钮（满宽 tonal） | 修改服务器地址 | settings_page.dart:45 |
| 分区标题 | 安全状态 | settings_page.dart:55 |
| 状态行 | 会话状态 → 已加密 / 未加密 | settings_page.dart:62-63 |
| 信息行（Session ID，截前 8 位后接省略号） | XXXXXXXX... | settings_page.dart:69-71 |
| 信息行 | 密钥交换 → XXX 分钟前 | settings_page.dart:77-79 |
| 按钮（描边，满宽） | 刷新会话密钥 | settings_page.dart:87 |
| 分区（卡片）标题 | 外观 | settings_page.dart:350 |
| 外观分段选项 1 | 跟随系统 | providers/theme_provider.dart:14 |
| 外观分段选项 2 | 亮色 | providers/theme_provider.dart:15 |
| 外观分段选项 3 | 暗色 | providers/theme_provider.dart:16 |
| 分区标题 | 笔记目录 | settings_page.dart:719 |
| 信息行 | 当前配置 → XXX | settings_page.dart:729 |
| 当前配置取值（未配置时） | 未配置 | settings_page.dart:651-652 |
| 当前配置取值（兜底格式：磁盘#ID / 路径） | 磁盘#XXX / （根目录） | settings_page.dart:657-658 |
| 加载失败文字 | 加载失败: XXX | settings_page.dart:733 |
| 按钮（满宽 tonal） | 配置笔记目录 | settings_page.dart:745 |
| 分区标题 | 账号 | settings_page.dart:116 |
| 信息行 | 用户名 → XXX | settings_page.dart:121 |
| 状态行 | 角色 → 管理员 / 普通用户 | settings_page.dart:125-126 |
| 信息行 | Token 过期 → XXX | settings_page.dart:132 |
| 按钮（描边，满宽，红色） | 退出登录 | settings_page.dart:148 |
| 分区标题（仅管理员） | 管理面板 | settings_page.dart:168 |
| 管理面板入口 1 | 用户管理 | settings_page.dart:174 |
| 管理面板入口 2 | 磁盘管理 | settings_page.dart:180 |
| 管理面板入口 3 | 权限配置 | settings_page.dart:186 |
| 管理面板入口 4 | 系统设置 | settings_page.dart:192 |
| 分区标题 | 关于 | settings_page.dart:203 |
| 信息行（版本；该字面量由 sync_version.py 随 version.json 同步） | v1.10.23 | settings_page.dart:208 |
| 关于副文 | 私有文档 & 笔记管理系统 | settings_page.dart:211 |
| 关于小字 | X25519 ECDH + ChaCha20-Poly1305 + ES256 JWT | settings_page.dart:216 |
| 弹窗标题 | 修改服务器地址 | settings_page.dart:239 |
| 表单 labelText | 服务器地址 | settings_page.dart:243 |
| 表单 hintText | http://192.168.1.100:8989 | settings_page.dart:244 |
| 弹窗按钮 | 取消 / 确定 | settings_page.dart:252,:256 |
| SnackBar | 服务器地址已更新 | settings_page.dart:267 |
| SnackBar | 会话密钥已刷新 | settings_page.dart:94 |
| SnackBar（失败） | 刷新失败: XXX | settings_page.dart:98 |
| SnackBar | 笔记目录已设置为：XXX | settings_page.dart:689 |
| SnackBar（保存失败） | 保存失败: XXX | settings_page.dart:696 |
| 弹窗标题（笔记目录） | 配置笔记目录 | settings_page.dart:865 |
| 弹窗步骤 1 | 第 1 步：选择虚拟磁盘 | settings_page.dart:872 |
| 磁盘下拉 hint | 选择磁盘 | settings_page.dart:883 |
| 弹窗步骤 2 | 第 2 步：浏览并选择子目录 | settings_page.dart:894 |
| 路径行（路径: /所选路径） | 路径: /XXX | settings_page.dart:900 |
| IconButton tooltip | 返回上级 | settings_page.dart:908 |
| IconButton tooltip | 根目录 | settings_page.dart:914 |
| 空态标题（未选磁盘） | 请先选择虚拟磁盘 | settings_page.dart:926 |
| 空态副文（未选磁盘） | 选择磁盘后可浏览并挑选笔记目录 | settings_page.dart:927 |
| 空态标题（无子目录） | 此目录下没有子文件夹 | settings_page.dart:937 |
| 空态副文（无子目录） | 可直接使用当前目录作为笔记目录 | settings_page.dart:938 |
| 弹窗按钮 | 取消 / 确定 | settings_page.dart:962,:976 |

## 4. 远程连接列表（lib/pages/remote/remote_list_page.dart）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 远程连接 | remote_list_page.dart:71 |
| IconButton tooltip | 新建连接 | remote_list_page.dart:75 |
| 分组筛选 chip（全部） | 全部 | remote_list_page.dart:97 |
| 分组筛选 chip（各分组；带数量时为「分组名 数量」） | XXX / XXX XXX | remote_list_page.dart:166 |
| 空态标题 | 还没有连接 | remote_list_page.dart:116 |
| 空态副文 | 新建一条 SSH 连接，配置会存到服务端 | remote_list_page.dart:117 |
| 连接卡标签（认证方式，三选一） | 密钥 / 密码 + OTP / 密码 | remote_list_page.dart:194-196 |
| 连接卡标签（经跳板；XXX 为跳板连接名） | ↳ 跳板 XXX | remote_list_page.dart:271 |
| 长按弹层菜单 1 | 编辑 | remote_list_page.dart:211 |
| 长按弹层菜单 2 | 删除 | remote_list_page.dart:219 |
| 删除弹窗标题 | 删除连接 | remote_list_page.dart:44 |
| 删除弹窗正文（XXX 为连接名） | 确定删除「XXX」？ | remote_list_page.dart:45 |
| 删除弹窗按钮 | 取消 / 删除 | remote_list_page.dart:47-48 |
| SnackBar（打开失败） | 打开失败：XXX | remote_list_page.dart:35 |
| SnackBar（删除失败） | 删除失败：XXX | remote_list_page.dart:59 |

## 5. 新建 / 编辑连接（lib/pages/remote/remote_edit_page.dart）

> 凭据语义：编辑时输入框留空 = 不修改（注释见 :10）。

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题（新建 / 编辑两态） | 新建连接 / 编辑连接 | remote_edit_page.dart:163 |
| AppBar 保存按钮（保存中也显示） | 保存 / 保存中… | remote_edit_page.dart:167 |
| 表单 labelText | 名称 | remote_edit_page.dart:176 |
| 表单 labelText | 主机 | remote_edit_page.dart:184 |
| 表单 labelText | 端口 | remote_edit_page.dart:192 |
| 表单 labelText | 用户名 | remote_edit_page.dart:200 |
| 模式分段 1 | SSH 终端 | remote_edit_page.dart:205 |
| 模式分段 2 | 仅 SFTP | remote_edit_page.dart:206 |
| 认证分段 1 | 密码 | remote_edit_page.dart:214 |
| 认证分段 2 | 密钥 | remote_edit_page.dart:215 |
| 认证分段 3 | 密码+OTP | remote_edit_page.dart:216 |
| 密码框 labelText | 密码 | remote_edit_page.dart:227 |
| 密码框 hintText（已设置时 / 新建时） | 已设置，留空不修改 / 输入密码 | remote_edit_page.dart:228 |
| 私钥框 labelText | 私钥内容 | remote_edit_page.dart:236 |
| 私钥框 hintText（已设置时 / 新建时） | 已设置，留空不修改 / -----BEGIN OPENSSH PRIVATE KEY----- | remote_edit_page.dart:237 |
| 口令框 labelText | 私钥口令（可选） | remote_edit_page.dart:245 |
| 口令框 hintText（已设置时 / 新建时） | 已设置，留空不修改 / 未设置 | remote_edit_page.dart:246 |
| 下拉 labelText | 跳板连接 | remote_edit_page.dart:253 |
| 跳板下拉选项（默认） | 直连 | remote_edit_page.dart:255 |
| 下拉 labelText | 分组 | remote_edit_page.dart:265 |
| 分组下拉选项（默认） | 未分组 | remote_edit_page.dart:267 |
| 表单 labelText | 连接后执行（可选） | remote_edit_page.dart:277 |
| 表单 hintText | cd /opt/app && ls | remote_edit_page.dart:278 |
| 校验错误 | 名称和主机不能为空 | remote_edit_page.dart:99 |
| 校验错误 | 该认证方式需要密码 | remote_edit_page.dart:105 |
| 校验错误 | 该认证方式需要私钥 | remote_edit_page.dart:111 |
| 校验错误 | 连接后执行命令只能是单行 | remote_edit_page.dart:115 |

> ⚠ 列表卡标签写 `密码 + OTP`（§4，含空格），本页分段按钮写 `密码+OTP`（无空格），两处不一致，官网指引须分端分位照抄。

## 6. 远程终端 + SFTP（lib/pages/remote/remote_terminal_page.dart）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题（XXX 为连接名） | XXX | remote_terminal_page.dart:258 |
| AppBar 副标题（XXX@XXX 为 用户名@主机） | XXX@XXX | remote_terminal_page.dart:260 |
| IconButton tooltip | 终端 | remote_terminal_page.dart:269 |
| IconButton tooltip | 文件 | remote_terminal_page.dart:274 |
| 首连弹窗标题 | 确认主机指纹 | remote_terminal_page.dart:59 |
| 首连弹窗正文（XXX 为主机） | 首次连接 XXX，请核对指纹： | remote_terminal_page.dart:64 |
| 首连弹窗按钮 | 拒绝 / 接受 | remote_terminal_page.dart:73-74 |
| 连接失败标题 | 连接失败 | remote_terminal_page.dart:291 |
| 连接失败重试按钮 | 重试 | remote_terminal_page.dart:295 |
| 终端未就绪占位 | 终端尚未就绪 | remote_terminal_page.dart:313 |
| SFTP 上传按钮 tooltip | 上传 | remote_terminal_page.dart:350 |
| SFTP 空目录占位 | 空目录 | remote_terminal_page.dart:362 |
| 文件行副文（目录） | 目录 | remote_terminal_page.dart:377 |
| 文件行副文（文件大小，按量级三选一） | XXX B / XXX KB / XXX MB | remote_terminal_page.dart:462-464 |
| 长按弹层菜单 1 | 下载 | remote_terminal_page.dart:392 |
| 长按弹层菜单 2 | 删除 | remote_terminal_page.dart:400 |
| 删除弹窗标题 | 删除远端文件 | remote_terminal_page.dart:212 |
| 删除弹窗正文（XXX 为文件名） | 确定删除「XXX」？ | remote_terminal_page.dart:213 |
| 删除弹窗按钮 | 取消 / 删除 | remote_terminal_page.dart:215-216 |
| 终端快捷键条（7 键） | Ctrl / Tab / Esc / ← / ↑ / ↓ / → | remote_terminal_page.dart:425-431 |
| SnackBar（读取目录失败） | 读取目录失败：XXX | remote_terminal_page.dart:137 |
| SnackBar（上传失败） | 上传失败：XXX | remote_terminal_page.dart:175 |
| SnackBar（下载成功；XXX 为本地完整路径） | 已下载到 XXX | remote_terminal_page.dart:196 |
| SnackBar（下载失败） | 下载失败：XXX | remote_terminal_page.dart:201 |
| SnackBar（删除失败） | 删除失败：XXX | remote_terminal_page.dart:232 |

## 7. 离线下载设置弹层（lib/pages/offline/offline_settings_sheet.dart）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| 弹层标题 | 离线下载设置 | offline_settings_sheet.dart:158 |
| IconButton tooltip | 关闭 | offline_settings_sheet.dart:164 |
| 弹层说明 | 默认目录按账号保存在服务端，桌面端 / Web 端同账号可见同一份。 | offline_settings_sheet.dart:171 |
| 无可写磁盘警告 | 当前账号没有可写入的磁盘，无法配置默认目录。请联系管理员分配写权限。 | offline_settings_sheet.dart:184 |
| 下拉 labelText | 虚拟磁盘 | offline_settings_sheet.dart:193 |
| 标签 | 当前目录 | offline_settings_sheet.dart:210 |
| 当前目录取值（格式：磁盘名 / 路径） | XXX / XXX | offline_settings_sheet.dart:216 |
| 当前目录取值（路径为空时） | 磁盘根目录 | offline_settings_sheet.dart:216 |
| 磁盘名兜底（无匹配磁盘时） | 未选择磁盘 | offline_settings_sheet.dart:65 |
| 按钮（描边） | 选择目录 | offline_settings_sheet.dart:224 |
| SwitchListTile 标题 | 目录不存在时自动创建 | offline_settings_sheet.dart:232 |
| SwitchListTile 副文 | 默认关闭；开启后保存时服务端会创建该目录 | offline_settings_sheet.dart:233 |
| 按钮（文字） | 清除默认目录 | offline_settings_sheet.dart:252 |
| 按钮（主） | 保存 | offline_settings_sheet.dart:263 |
| 目录选择器标题 | 选择默认目录 | offline_settings_sheet.dart:76 |
| 校验错误 | 请先选择虚拟磁盘 | offline_settings_sheet.dart:87 |
| SnackBar | 默认目录已保存 | offline_settings_sheet.dart:102 |
| SnackBar | 默认目录已清除 | offline_settings_sheet.dart:126 |
| 错误兜底文案 | 保存默认目录失败 | offline_settings_sheet.dart:108 |
| 错误兜底文案 | 清除默认目录失败 | offline_settings_sheet.dart:132 |

## 8. 用户管理（lib/pages/admin/admin_users_page.dart，仅管理员）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 用户管理 | admin_users_page.dart:26 |
| IconButton tooltip | 添加用户 | admin_users_page.dart:30 |
| IconButton tooltip | 刷新 | admin_users_page.dart:35 |
| 空态标题 | 暂无普通用户 | admin_users_page.dart:46 |
| 空态副文 | 点击右上角「添加用户」创建第一个普通用户 | admin_users_page.dart:47 |
| 角色徽标（二选一） | 管理员 / 普通用户 | admin_users_page.dart:108 |
| 状态徽标（二选一） | 启用 / 禁用 | admin_users_page.dart:115 |
| PopupMenu tooltip | 操作菜单 | admin_users_page.dart:127 |
| 三点菜单 1 | 修改密码 | admin_users_page.dart:142 |
| 三点菜单 2（随当前状态切换） | 禁用 / 启用 | admin_users_page.dart:151 |
| 三点菜单 3 | 删除 | admin_users_page.dart:162 |
| 弹窗标题 | 添加用户 | admin_users_page.dart:180 |
| 表单 labelText | 用户名 | admin_users_page.dart:186 |
| 表单 labelText | 密码 | admin_users_page.dart:192 |
| 弹窗按钮 | 取消 / 创建 | admin_users_page.dart:199,:217 |
| 弹窗标题 | 修改密码 | admin_users_page.dart:229 |
| 表单 labelText | 新密码 | admin_users_page.dart:233 |
| 弹窗按钮 | 取消 / 确认 | admin_users_page.dart:238,:260 |
| 弹窗标题 | 确认删除 | admin_users_page.dart:287 |
| 删除弹窗正文（XXX 为用户名） | 确定要删除用户「XXX」吗？ | admin_users_page.dart:288 |
| 删除弹窗按钮 | 取消 / 删除 | admin_users_page.dart:292,:312 |
| SnackBar | 密码已更新 | admin_users_page.dart:250 |
| SnackBar（失败） | 创建失败: XXX | admin_users_page.dart:213 |
| SnackBar（失败） | 修改失败: XXX | admin_users_page.dart:256 |
| SnackBar（失败） | 操作失败: XXX | admin_users_page.dart:278 |
| SnackBar（失败） | 删除失败: XXX | admin_users_page.dart:308 |

## 9. 磁盘管理（lib/pages/admin/admin_disks_page.dart，仅管理员）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 磁盘管理 | admin_disks_page.dart:30 |
| IconButton tooltip | 添加磁盘 | admin_disks_page.dart:34 |
| IconButton tooltip | 刷新 | admin_disks_page.dart:39 |
| 空态标题 | 暂无磁盘 | admin_disks_page.dart:49 |
| 空态副文 | 点击右上角「添加磁盘」创建第一个虚拟磁盘 | admin_disks_page.dart:50 |
| 磁盘卡标题（格式：编号. 名称） | XXX. XXX | admin_disks_page.dart:93 |
| PopupMenu tooltip | 操作菜单 | admin_disks_page.dart:117 |
| 三点菜单 1 | 编辑 | admin_disks_page.dart:130 |
| 三点菜单 2 | 删除 | admin_disks_page.dart:141 |
| 弹窗标题 | 添加虚拟磁盘 | admin_disks_page.dart:159 |
| 表单 labelText | 磁盘名称 | admin_disks_page.dart:165 |
| 表单 labelText | 真实路径 | admin_disks_page.dart:174 |
| 表单 hintText | /data/docs | admin_disks_page.dart:175 |
| IconButton tooltip | 浏览服务器目录 | admin_disks_page.dart:181 |
| 弹窗按钮 | 取消 / 创建 | admin_disks_page.dart:197,:215 |
| 弹窗标题 | 编辑虚拟磁盘 | admin_disks_page.dart:228 |
| 表单 labelText | 磁盘名称 | admin_disks_page.dart:234 |
| 表单 labelText | 真实路径 | admin_disks_page.dart:242 |
| IconButton tooltip | 浏览服务器目录 | admin_disks_page.dart:247 |
| 弹窗按钮 | 取消 / 保存 | admin_disks_page.dart:263,:285 |
| 弹窗标题 | 确认删除 | admin_disks_page.dart:296 |
| 删除弹窗正文（XXX 为磁盘名；两行真实换行） | 确定要删除磁盘「XXX」吗？ / 相关权限配置将同步清除。 | admin_disks_page.dart:297 |
| 删除弹窗按钮 | 取消 / 删除 | admin_disks_page.dart:301,:321 |
| SnackBar（失败） | 添加失败: XXX | admin_disks_page.dart:211 |
| SnackBar（失败） | 更新失败: XXX | admin_disks_page.dart:281 |
| SnackBar（失败） | 删除失败: XXX | admin_disks_page.dart:317 |

## 10. 权限配置（lib/pages/admin/admin_permissions_page.dart，仅管理员）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 权限配置 | admin_permissions_page.dart:37 |
| IconButton tooltip | 刷新 | admin_permissions_page.dart:41 |
| 空态标题 | 暂无普通用户 | admin_permissions_page.dart:55 |
| 空态副文 | 先在「用户管理」中创建普通用户，再回到此处配置磁盘权限 | admin_permissions_page.dart:56 |
| 用户卡副文 | 点击配置磁盘权限 | admin_permissions_page.dart:107 |
| 权限弹层标题（XXX 为用户名） | XXX 的权限 | admin_permissions_page.dart:353 |
| 弹层保存按钮 | 保存 | admin_permissions_page.dart:365 |
| 小节标题 | 磁盘权限 | admin_permissions_page.dart:378 |
| 数量徽标（XXX 为磁盘数） | XXX 个磁盘 | admin_permissions_page.dart:380 |
| 权限复选框 1 | 读取 | admin_permissions_page.dart:442 |
| 权限复选框 2 | 写入 | admin_permissions_page.dart:450 |
| 权限复选框 3 | 删除 | admin_permissions_page.dart:458 |
| SnackBar（失败） | 加载磁盘列表失败: XXX | admin_permissions_page.dart:157 |
| SnackBar（无磁盘） | 暂无可用的磁盘 | admin_permissions_page.dart:164 |
| SnackBar（保存成功且有变更；XXX 依次为用户名、变更数） | 已保存 XXX 的 XXX 项权限变更 | admin_permissions_page.dart:236 |
| SnackBar（保存成功且无变更） | 权限未作更改 | admin_permissions_page.dart:237 |
| SnackBar（保存失败） | 保存失败: XXX | admin_permissions_page.dart:247 |

## 11. 系统设置（lib/pages/admin/admin_system_page.dart，仅管理员）

页面卡片顺序：离线媒体修复 → 离线下载 → 存储与临时文件（:35-39）。

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| AppBar 标题 | 系统设置 | admin_system_page.dart:31 |
| 卡片标题（媒体修复，加载完成后） | 离线媒体修复 | admin_system_page.dart:202 |
| 卡片标题（媒体修复，加载中短暂显示） | 媒体修复 | admin_system_page.dart:193 |
| 媒体修复说明 | 损坏媒体经「修复中心」手动离线修复（在文件管理中长按文件选「修复损坏媒体」发起）。并发数 1~8，或留空按服务器 CPU 核数自动推导。配置存于服务端、三端共享，修改后立即生效。 | admin_system_page.dart:218-220 |
| 媒体修复并发数标签 | 修复并发数（1~8，留空自动） | admin_system_page.dart:231 |
| 媒体修复输入框 hintText | 自动 | admin_system_page.dart:243 |
| 媒体修复保存按钮 | 保存 | admin_system_page.dart:251 |
| 卡片标题 | 离线下载 | admin_system_page.dart:494 |
| 离线下载说明 | 网址分析 / 媒体抓取的全局策略，配置存于服务端、三端共享，修改后立即生效。总开关默认开启；关闭后三端入口置灰且不产生任何出站请求（应急熔断）。 | admin_system_page.dart:510-512 |
| SwitchListTile 标题 | 离线下载总开关 | admin_system_page.dart:525 |
| SwitchListTile 副文 | 默认开启；关闭后三端入口置灰，不产生任何出站请求 | admin_system_page.dart:526 |
| SwitchListTile 标题 | 深度分析（无头浏览器） | admin_system_page.dart:545 |
| SwitchListTile 副文 | 仅作兜底；关闭后只能靠前两级解析 | admin_system_page.dart:546 |
| 数字项 1（标签 / 允许范围） | 下载并发数 / 1~4 | admin_system_page.dart:297-298 |
| 数字项 2（标签 / 允许范围） | 单文件体积上限（MB） / 1~102400 | admin_system_page.dart:305-306 |
| 数字项 3（标签 / 允许范围） | 单次清单项数上限 / 1~200 | admin_system_page.dart:313-314 |
| 数字项 4（标签 / 允许范围） | 单页图片张数上限 / 1~1000 | admin_system_page.dart:321-322 |
| 数字项 5（标签 / 允许范围） | 解析总超时（秒） / 30~180 | admin_system_page.dart:329-330 |
| 数字项 6（标签 / 允许范围） | 连接超时（秒） / 5~30 | admin_system_page.dart:337-338 |
| 数字项 7（标签 / 允许范围） | 读取超时（秒） / 15~120 | admin_system_page.dart:345-346 |
| 数字项范围副文（XXX 为各字段允许范围） | 允许范围 XXX | admin_system_page.dart:571 |
| 离线下载保存按钮 | 保存数值配置 | admin_system_page.dart:600 |
| 卡片标题 | 存储与临时文件 | admin_system_page.dart:793 |
| 存储清理说明 | 自动清理程序自产的临时文件（上传分片与会话残留、离线下载残缺目录、合并残留）。临时文件期限同时决定断点续传有效期与清理保护期；到期后安全删除，客户端下次从头上传。关闭自动清理后，「立即清理」仍可手动执行一次。只删除程序自产、固定命名的文件，不会动你的正常文件。 | admin_system_page.dart:806-810 |
| SwitchListTile 标题 | 自动清理临时文件 | admin_system_page.dart:821 |
| SwitchListTile 副文 | 只管自动清理；关闭后「立即清理」仍可手动执行 | admin_system_page.dart:822 |
| 数字行标签（临时文件期限 / 允许范围） | 临时文件期限（小时） / 1~168 | admin_system_page.dart:830 |
| 数字行标签（清理间隔 / 允许范围） | 清理检查间隔（分钟） / 5~1440 | admin_system_page.dart:831 |
| 按钮（tonal） | 保存配置 | admin_system_page.dart:837 |
| 按钮（描边） | 立即清理 | admin_system_page.dart:842 |
| SnackBar | 配置已保存，立即生效 | admin_system_page.dart:161,:709 |
| SnackBar（开关切换模板；前 XXX 为「离线下载」或「深度分析」，后 XXX 为 开启 或 关闭） | XXX已XXX，立即生效 | admin_system_page.dart:443,:535,:555 |
| SnackBar（数值校验失败；XXX 依次为字段标签、下限、上限） | XXX需为 XXX~XXX 的整数 | admin_system_page.dart:460 |
| SnackBar | 离线下载配置已保存，立即生效 | admin_system_page.dart:471 |
| SnackBar（失败） | 保存失败: XXX | admin_system_page.dart:166,:474,:711 |
| SnackBar（并发数校验失败） | 并发数需为 1~8 的整数，或留空使用自动基线 | admin_system_page.dart:184 |
| SnackBar（整数校验失败） | 请填写合法整数 | admin_system_page.dart:688 |
| SnackBar（期限校验失败） | 临时文件期限需为 1~168 小时 | admin_system_page.dart:692 |
| SnackBar（间隔校验失败） | 清理间隔需为 5~1440 分钟 | admin_system_page.dart:696 |
| SnackBar（清理结果；XXX 依次为会话、目录、碎片、跳过数量） | 清理完成：会话 XXX，目录 XXX，碎片 XXX，跳过 XXX | admin_system_page.dart:722-724 |
| SnackBar（清理失败） | 清理失败: XXX | admin_system_page.dart:727 |
| 加载失败文字 | 加载失败: XXX | admin_system_page.dart:209,:501,:800 |

## 12. 通用组件固定文案（lib/widgets/）

| 控件 | 精确文案 | evidence |
| --- | --- | --- |
| `ErrorState` 默认标题 | 加载失败 | widgets/error_state.dart:13 |
| `ErrorState` 默认重试按钮 | 重试 | widgets/error_state.dart:15 |
| `FileListTile` 副信息行（大小 · 修改时间，以「 · 」连接） | XXX · XXX | widgets/file_list_tile.dart:98 |
| `AppCard`（含 SectionTitle / AppBadge / StatCard / PrimaryGradientButton） | 无用户可见文案 | widgets/app_card.dart:9-361 |
| `EmptyState`（title 必填、由调用方传入） | 无用户可见文案 | widgets/empty_state.dart:15-21 |
| `LoadingIndicator`（已弃用，`lib/` 下无引用） | 无用户可见文案 | widgets/loading_indicator.dart:19-34 |

## 13. 与「已知事实」的核对结果

1. **settings_page.dart 分区**：`服务器`（:31）→ `安全状态`（:55）→ `账号`（:116）→ `管理面板`（:168，仅管理员）→ `关于`（:203）五项全部在码、顺序一致；**但**「安全状态」与「账号」之间还渲染两张卡：`外观`（:109，标题 :350）与 `笔记目录`（:112，标题 :719）。页面可见卡片共 **7 张**（普通用户 6 张），"5 个分区"的说法漏了这 2 张。
2. **album_backup_card.dart 开关**：`自动同步相册`（:320）、`远端目标目录`（:329，目录行而非开关）、`仅 Wi-Fi`（:353）、`仅充电时`（:358）四项全部确认，另有 `备份图片`（:343）、`备份视频`（:348）两个开关。
