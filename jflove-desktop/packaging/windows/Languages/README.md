# Inno Setup 简体中文语言文件（内置副本）

本目录存放仓库**内置**的 Inno Setup 简体中文语言文件，供
`jflove-desktop/packaging/windows/jflove.iss` 的 `[Languages]` 段使用。

## 为什么内置（而不是从构建机的 Inno Setup 目录里找）

- Inno Setup 官方安装包的 `Languages\` **不一定**含 `ChineseSimplified.isl`
  （它属社区翻译，不同版本/发行渠道包含情况不一致）；
- 构建机上 `iscc` 很可能来自包管理器（如 Chocolatey 的 shim），
  **shim 所在目录与真实编译器安装目录是两个不同的地方**；
- 而 `jflove.iss` 里写 `MessagesFile: "compiler:Languages\ChineseSimplified.isl"` 时，
  `compiler:` 是由 ISCC 解析为**其自身安装目录**的（与 PATH / shim 无关）。

> 历史故障（v1.5.0 CI）：脚本按 shim 目录探测到"看起来存在"的语言文件 →
> 传 `HasChineseIsl=1` → ISCC 却按真实安装目录找不到该文件 → **iscc 编译中止、
> 安装包缺失、CI job 失败**。内置副本 + 真实存在性校验后彻底消除该路径不一致。

## 文件信息

| 项 | 值 |
| --- | --- |
| 文件 | `ChineseSimplified.isl` |
| 目标 Inno Setup 版本 | `6.5.0+`（文件头首行声明） |
| 编码 | UTF-8（无 BOM） |
| 大小 | 21516 字节 |
| SHA256 | `E0B0B350E2245F3C5E65586DFE43D574F6E7F06F2261149ABA284954B3FC9A8D` |
| 维护者 | Zhenghan Yang (Kira) — <https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation> |
| 上游下载页 | <https://jrsoftware.org/files/istrans/> |

## 许可与再分发

Inno Setup `license.txt` 明确允许再分发：

> Permission is granted to anyone to use this software for any purpose, including
> commercial applications, and to alter and redistribute it, provided that the
> following conditions are met: … All redistributions of source code files must
> retain all copyright notices that are currently in place…

因此本副本**必须保留文件头部的原始注释**（版权/来源/维护者信息），
**不要**删除或改写这些行。若上游更新了该翻译文件，可直接覆盖本文件并同步更新
上表的版本 / 大小 / SHA256。

## 升级方式

1. 从 <https://jrsoftware.org/files/istrans/> 或新版 Inno Setup 安装目录取得
   `Languages\ChineseSimplified.isl`；
2. 覆盖本目录下的同名文件（保持 UTF-8 无 BOM）；
3. 更新本 README 的版本 / 大小 / SHA256；
4. 跑一遍 `python jflove-desktop/build.py --mode onedir --installer` 确认向导为中文。

## 覆盖优先级（`build.py::find_chinese_isl`）

1. 环境变量 `JFLOVE_CHINESE_ISL` 显式指定的路径；
2. **本目录的内置副本**（CI / 离线 / 本机一致命中）；
3. 构建机上真实编译器目录里的同名文件；
4. 都没有 → 响亮警告并**退回英文界面**（安装包照常产出，绝不编译中止）。
