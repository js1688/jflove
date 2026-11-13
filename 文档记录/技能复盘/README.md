# 技能复盘报告（目录说明）

> 本目录是 `skills-self-growth` 技能的**唯一产出位置**，规则见 `AGENTS.md` §6.5。
> 报告是**给人做决策**用的，不是原始收件箱的转储 —— 原始复盘在 `.agents/skills/lessons/INBOX.md`。

## 文件命名

| 文件 | 说明 |
| --- | --- |
| `<YYYY-MM>.md` | 月度报告，一期一个文件，**不覆盖** |
| `README.md` | 本文件 |

## 内容边界

- ✅ 提案（改哪个技能/脚本、净行数、判据、回滚方式）、驳回理由、上期落实情况、门禁结果。
- ❌ 完整实现代码（提案只放草稿 diff）、逐条原始复盘（那是 INBOX 的活）。

## 脱敏规则（强制，`scripts/check_retro.py` 会拦）

| 禁止出现 | 写成 |
| --- | --- |
| token / 密码 / 私钥 / Cookie / session key | `<redacted>` |
| 真实公网/内网 IP、真实域名 | `<redacted>`（`127.0.0.1` / `localhost` 除外） |
| 绝对家目录路径（`/home/<name>/…`、`C:\Users\<name>\…`） | 仓库内相对路径，或 `<repo>/…` |

需要保留原文才能复现时，改用"命令 + 退出码 + 相对路径"描述，**不要贴原始日志**。

## 归档

- 报告是历史记录，**不删不改**；如判断过期，在当期报告里写"已废止：原因"。
- 超过 24 个月的报告移到 `归档/`，由人手动移动（脚本不自动搬）。

## 门禁

```bash
jflove-server/venv-linux/bin/python scripts/check_retro.py
```
