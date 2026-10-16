---
name: subagent-delegation
description: "子 agent 委派契约（父 agent 派活 / 子 agent 接活前必读）。规定角色点名、prompt 自包含、目录白名单、只读约束、禁止嵌套委派与统一回执格式。Use when: 准备调用 subagent / subagent_fork / workflow 派活给子 agent，或自己作为子 agent 收到任务。"
---

# subagent-delegation — 子 agent 委派契约

> 本技能是 `AGENTS.md §6.1` 的可加载形态。**父 agent 派活前、子 agent 接活后，都必须先读本节。**
> 适用对象：`subagent` / `subagent_fork` / `workflow` 启动的任何子 agent，含孙 agent。
>
> ⚠ **维护须知（别把技能改没了）**：本文件 frontmatter 的 `description` **必须用双引号包裹** ——
> 描述里含 `Use when: `（冒号+空格），写成 YAML plain scalar 就是**非法 YAML**，
> DSH 会**静默丢弃**整个技能（技能目录里查不到、`skill("subagent-delegation")` 只回
> `unknown or no longer available`）。依据 `LESSONS.md` L45。

## 0. 为什么需要它（实测结论，别当成口号）

- 子 agent **确实**继承了 `AGENTS.md`（以 system-reminder 注入），但注入语气的效力是
  *"may be relevant … as guidance when applicable"*，**弱于**父 agent 在 prompt 里的显式要求。
- DSH 的委派工具**没有权限参数**，写盘边界无法在工具层硬隔离。
- 实测反例：探针子 agent 的 prompt 里写明「严禁创建、修改、删除任何文件」，它**仍然写了文件**。
  ⇒ **只在 prompt 里写"禁止"不牢靠，必须配事后核对。**

## 1. 父 agent 的硬性义务

1. **必须点名角色**。委派 prompt 首段写：
   `角色：<role>；先调用 skill 工具加载 <role>，再 read .agents/skills/<role>/SKILL.md 全文并遵守。`
   未点名角色的委派 = 违规委派。
2. **prompt 必须自包含**。`subagent` / `workflow` 是全新会话、**不继承本对话**（只有 `subagent_fork` 继承）。
   必须显式给出：
   - 任务目标 + 验收判据（可判定的，不要"看着对"）
   - **允许改动的目录白名单** / **禁止改动的目录**
   - 必须先读的文件清单（设计文档 / 开发记录 / 相关源码）
   - 需要产出的文档路径（按 §6 角色表对应目录）
3. **只读任务要写成只读**：调研 / 审查 / 探针类子 agent，prompt 必须含
   「**只读，禁止写盘**，结论以最终文本返回，不要写任何副本文件」。
4. **事后核对（不可省）**：委派前后各跑一次 `git status --porcelain`，
   逐条确认新增/修改文件是否都在白名单内。越界写盘**立即清理**并在回复中说明。
5. **回执必须核验**：子 agent 的结论必须带证据（文件:行 / 命令 / 输出），
   父 agent **核对证据后才采信**，不得把子 agent 结论直接当事实转述给用户。
6. **一个子 agent 只做一件事**；跨模块任务拆成多个子 agent，不要塞进一个。
7. **技能命中必须播报**（承接 §6）：父子两侧都要说明命中了哪些技能。

## 2. 子 agent 的硬性义务

1. **禁止嵌套委派**：不得再调用 `subagent` / `subagent_fork` / `workflow`。需要拆分时，回报父 agent。
2. **模块边界同等有效**（`AGENTS.md §1`）：只能在自己被授权的模块目录内改动；越界写入视为父 agent 违规。
3. **只读就真的只读**：被要求只读时，不创建任何文件、不改任何文件、不写"答案副本"。
4. **计划先行**（`AGENTS.md §1.1`）：动手前先列出计划；长任务要更新 `plans/` 计划文件状态。
5. **统一回执四段式**：
   ```
   1) 结论
   2) 证据（文件:行 / 命令 / 输出摘要）
   3) 未决问题 / 未验证项
   4) 是否越界写盘（列出自建/自改文件；没有也要明确写"无"）
   ```
6. **不确定就说不确定**：没跑过的验证不要写"已验证"（`LESSONS.md §五` 第 1 条）。

## 3. 技能加载（与 §6.2 配套）

- 项目技能在会话技能目录中的名字 = `<角色名>`（如 `backend`、`code-review`），另有一个 `lessons`。
- 优先级：**项目技能 > 内置技能**（rank 200/300 vs 600）；同名时项目技能胜出。
- 项目技能不可见时（换机器 / 新克隆 / 技能目录未刷新），退化为
  `read .agents/skills/<role>/SKILL.md` —— **约束照样成立，不许因此跳过角色技能**（`AGENTS.md` §6.2 第 3 条）。
