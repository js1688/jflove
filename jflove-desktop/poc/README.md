# poc/ —— 一次性验证脚本（**非交付物**）

本目录存放"为了判定某个跨端/系统行为到底成不成立"而写的探针脚本：跑一次、拿到退出码或
真机证据，结论**固化进 `文档记录/` 与源码注释**，脚本本身留在仓库里只为"结论可复现"。

## 硬约定

- **不参与构建、不被任何运行时代码 import**，不出现在安装包里（`build.py` 只打 `src/` + `ui/dist`）。
- **不进 `src/` / `tests/`**：进 `tests/` 的东西必须是能长期跑的回归用例，探针不是。
- **不要新建到仓库根**：本目录在 `jflove-desktop/` 之前被误放过一次（v1.7.0 时根目录出现过 `poc/`），
  已迁回。新增探针请按主题建子目录，与 `bridge/`、`frameless/`、`editor-probes/` 同级。
- 探针会复制 `jflove-server/` 当被测服务的批次，跑完留下的 `.tmp_*` 沙箱属于垃圾
  （`.gitignore` 已排除），清理用：

  ```bash
  python scripts/clean_poc_sandboxes.py            # dry-run，先看要删什么
  python scripts/clean_poc_sandboxes.py --apply    # 真删（只动未跟踪文件）
  ```

## 现有子目录

| 目录 | 主题 | 关键判据 / 证据去处 |
| --- | --- | --- |
| `cfapi/` | Windows CfAPI 系统级挂载：可行性探针 `cfapi_probe.py`、跨进程 A/B 诊断探针 `cfapi_crossproc_probe.py`、排障解锁工具 `cfapi_unregister.py` | 设计文档 v1.7.0 §S-8 / R-1；`src/utils/mount_cfapi.py` 顶部注释 |
| `editor-probes/` | v1.6.x 编辑器 / 快捷键 / 剪贴板 / 单实例仲裁 | `文档记录/桌面端开发记录/v1.6*.md` |
| `bridge/`、`frameless/`、`login-shell/`、`media-overlay/`、`common/` | 对应版本的窗口/桥/登录/媒体浮层探针 | 各版本开发记录 |

## ⚠ 例外：`cfapi/cfapi_unregister.py` 是**运维工具**，不是探针

Windows 上应用异常退出后，被 `CfRegisterSyncRoot` 接管的目录会「进不去 / 删不掉」。
这个脚本是目前唯一的自救手段，**不要当垃圾清掉**：

```bash
# 先退出 JFLove 应用，再执行
python jflove-desktop/poc/cfapi/cfapi_unregister.py "D:\JFLove\照片库"
```
