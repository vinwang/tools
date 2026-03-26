# iFlow Tools

个人 AI 开发工具与多 Agent 配置仓库，集中维护 Codex、iFlow CLI、OpenClaw 三套工作流。

## 模块概览

| 目录 | 用途 | 关键内容 |
|------|------|----------|
| [codex](./codex/) | Codex 项目级规则 | `AGENTS.md`，约束回复语言、调试优先、工程基线与技能路由 |
| [iflow](./iflow/) | iFlow CLI 配置 | `IFLOW.md`、`settings.json`、Agents、Commands、Hooks、Skills |
| [openclaw](./openclaw/) | OpenClaw 多 Agent 协作配置 | `openclaw.json`、团队文档、主工作区与各角色工作区 |

## 仓库用途

这个仓库不是业务代码仓库，而是本地 AI 工具链的配置中心，用来统一维护：

- Codex 终端代理的项目工作规则
- iFlow CLI 的代理、命令、技能、MCP 与 Hook 配置
- OpenClaw 的多角色协作、工作区和渠道接入配置

## 快速开始

### Codex

`codex/AGENTS.md` 提供一套可复用的项目级代理规则，核心约束包括：

- 默认用中文回复
- Debug-first，不做静默降级和假成功
- 保持 SOLID、DRY、YAGNI 等工程基线
- 任务开始前先扫描并使用匹配技能

### iFlow CLI

`iflow/` 保存 iFlow CLI 的完整工作流配置。

```bash
iflow
```

当前配置重点：

- 默认模型为 `glm-5`
- 默认 API 地址为 `https://apis.iflow.cn/v1`
- 已启用 `checkpointing`
- 默认编辑器为 `vscode`
- 已配置 `UserPromptSubmit` Hook，执行 `iflow/hooks/user_prompt_submit.py`

`settings.json` 当前内置的 MCP 服务器包括：

- `mcp-probe-kit`
- `github`
- `context7`
- `sequential-thinking`
- `fetch`
- `mcp-doc`（已禁用）

其中 `context7` 与 `sequential-thinking` 已使用 `@iflow-mcp/*` 包名。

### OpenClaw

`openclaw/` 用于维护多 Agent 团队协作配置。当前默认角色如下：

- `main`：主 Agent
- `milo`：Team Lead
- `josh`：Business
- `marketing`：营销 Agent
- `dev`：开发 Agent

当前 `openclaw/openclaw.json` 还定义了：

- 多模型提供商：Moonshot、OpenRouter、Bailian
- Agent 间通信能力：`agentToAgent`
- 定时任务：`cron`
- 本地网关：`gateway`
- 飞书渠道：`channels.feishu`

## 目录结构

```text
.
├── codex/
│   └── AGENTS.md
├── iflow/
│   ├── agents/
│   ├── commands/
│   ├── hooks/
│   ├── skills/
│   ├── IFLOW.md
│   └── settings.json
└── openclaw/
    ├── openclaw.json
    ├── team/
    ├── workspace/
    ├── workspace-dev/
    ├── workspace-josh/
    ├── workspace-marketing/
    └── workspace-milo/
```

## 关键文件

| 文件 | 说明 |
|------|------|
| [codex/AGENTS.md](./codex/AGENTS.md) | Codex 的全局代理规则模板 |
| [iflow/IFLOW.md](./iflow/IFLOW.md) | iFlow 的核心工作规范与路由约束 |
| [iflow/settings.json](./iflow/settings.json) | iFlow 的模型、MCP、Hook 与编辑器配置 |
| [openclaw/openclaw.json](./openclaw/openclaw.json) | OpenClaw 的模型、角色、渠道和网关配置 |

## 许可证

MIT
