# iFlow Tools

个人工具集与配置仓库，包含 iFlow CLI 全局配置和 OpenClaw 多 Agent 系统配置。

## 模块说明

| 目录 | 说明 |
|------|------|
| [iflow](./iflow/) | iFlow CLI 全局配置，包含 Agents、Skills、Commands、Rules 等配置 |
| [openclaw](./openclaw/) | OpenClaw 多 Agent 系统配置，支持团队协作的 AI Agent 管理 |

## 快速开始

### iFlow CLI

iFlow CLI 是一个支持子代理路由的 AI 辅助开发工具。

```bash
# 使用 iFlow CLI
iflow
```

### OpenClaw

OpenClaw 是一个多 Agent 协作系统，支持多个专业 Agent 协同工作。

**Agent 列表：**
- `main` - 主 Agent，默认工作空间
- `milo` - Team Lead，负责团队协调
- `josh` - Business，负责业务指标
- `marketing` - 营销 Agent，负责内容创作
- `dev` - 开发 Agent，负责技术实现

## 目录结构

```
.
├── iflow/                    # iFlow CLI 配置
│   ├── agents/               # 子代理定义
│   ├── commands/             # 自定义命令
│   ├── skills/               # 技能模块
│   └── IFLOW.md              # 核心工作规则
│
└── openclaw/                 # OpenClaw 配置
    ├── openclaw.json         # 主配置文件
    ├── team/                 # 团队文档
    └── workspace*/           # 各 Agent 工作空间
```

## 配置说明

### iFlow 核心规则

详见 [iflow/IFLOW.md](./iflow/IFLOW.md)，包含：
- 子代理路由表
- 编码规范
- 安全检查单
- 工程原则（SOLID/KISS/DRY/YAGNI）

### OpenClaw 模型配置

支持多个模型提供商：
- **Moonshot** - Kimi K2.5
- **OpenRouter** - 自动路由
- **Bailian** - 通义千问、GLM、Kimi 等

## 许可证

MIT
