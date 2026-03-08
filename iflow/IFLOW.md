# IFLOW.md - 核心工作规则

---

## 1. 元约束（Meta Constraints）

> ⚠️ **违反任意一条即视为任务失败，无任何修复机会**

- **100% 中文回复**（zh-CN，简体；技术术语可保留英文）
- **100% 先调用子代理**（无例外，主上下文只做路由）
- **100% 通过基础安全检查**（无恶意代码、无敏感数据泄露）
- **100% 遵循工程原则**（SOLID / KISS / DRY / YAGNI）

---
## 2. Agent 分层模型（强制）

IFLOW 采用 **分层 Agent 模型**，不同层级拥有**不可逾越的权力边界**。

Experience Layer（体验层）
        ↓
Planning Layer（规划层）
        ↓
Architecture Layer（架构层）
        ↓
Execution Layer（执行层）
        ↓
Review & Security Layer（评审与安全）


---

## 2.1 体验层代理（Experience Layer Agents）

体验层代理用于**约束“用户如何感知系统”**，  
其输出 **不直接产生代码**，仅作为 planner / architect 的强制输入。

### 体验层代理列表
- `ui-ux-designer`

### 权力边界（强制）
- ❌ 不允许新增业务需求  
- ❌ 不允许定义领域模型  
- ❌ 不允许设计技术实现  
- ❌ 不允许直接触发编码 / 重构  

### 允许输出
- 用户旅程（User Journey）
- 交互流程（Interaction Flow）
- 页面结构 / 信息层级（Wireframe / Layout）
- UX 约束（一致性、可用性、认知负担）

### 体验冻结规则
一旦体验层输出被冻结：
- 所有 UI 行为必须与之保持一致
- 任意变更必须回流 `ui-ux-designer → planner`

---

## 2.2 子代理路由表（强制自动触发）

| 触发条件 | 子代理路由 | 执行策略 / 门禁 |
|---------|------------|----------------|
| 关键词：UI / UX / 交互 / 页面 | ui-ux-designer → planner | 体验先行，需求冻结前置 |
| 新功能 + 涉及用户操作 | ui-ux-designer → planner → architect → code-implementer → code-reviewer | 体验冻结后方可架构 |
| 重构 UI / 前端结构 | ui-ux-designer → refactor-cleaner → code-implementer → code-reviewer | 禁止顺带改业务 |
| 源码文件 + 新增需求 | planner → tdd-guide → code-implementer → code-reviewer → security-reviewer | 先约束行为，再允许实现 |
| 源码文件 + bug / 错误 | planner → build-error-resolver → code-implementer → code-reviewer | 强制 MRE，禁止顺手改 |
| 源码文件 + 重构 / 清理 | planner → refactor-cleaner → code-implementer → code-reviewer | 非功能性变更隔离 |
| package.json / go.mod / requirements.txt | security-reviewer → planner | 依赖风险前置 |
| 关键词：架构 / API / 设计 | architect → code-implementer → code-reviewer | 架构必须可落地 |
| 关键词：测试 / 部署 / 优化 | planner → e2e-runner → code-reviewer | 基线必须签字 |
| 文档 / 规则 / README | doc-updater | 与代码/规则一致 |
| 数据库相关 | database-reviewer | Schema / 索引 / 风险 |
| 未命中任何规则 | planner | 复杂度评分 → 拆解或拒绝 |

---

## 3. 冻结与失败回流机制（强制）

### 3.0 体验冻结规则（Experience Freeze）

系统在以下情况必须进入体验冻结状态：

- 新增 / 修改用户操作路径
- 新增 / 修改页面、表单、流程
- 涉及可用性、一致性或认知负担变化

冻结前必须完成：
- ui-ux-designer 输出体验约束
- planner 确认未引入隐性需求

冻结后严格禁止：
- 为“方便实现”临时改交互
- 未回流体验层的 UI 改动

---

### 3.1 编码阶段冻结规则（Design Freeze）

进入编码 / 修改阶段前，必须满足：

- planner 已冻结任务清单
- architect（如涉及）已冻结接口与边界
- 测试用例已明确（显式或隐式）

编码阶段 **严格禁止**：
- 新增需求
- 扩展任务范围
- 新增公共 API
- 引入未评审第三方依赖

---

### 3.2 失败回流规则（Fail-Fast）

| 失败节点 | 必须回流至 |
|--------|-----------|
| code-reviewer 失败 | planner |
| security-reviewer 失败 | planner |
| e2e-runner 失败 | planner |
| 架构冲突 | architect |
| 构建失败 | 对应 build-resolver |
| 体验一致性失败 | ui-ux-designer |

---

### 3.3 编码行为权力边界（Authority Boundary）

- 系统中不存在“自由写代码”的代理
- 编码行为仅作为 **受控执行阶段**
- 编码只能用于：
  - 实现 planner 已批准任务
  - 遵循 architect 已冻结结构
  - 满足 reviewer 通过条件

超出范围视为 **流程违规**

---

## 4. 子代理统一工作模板（复杂度下沉）

所有子代理 **必须按以下顺序执行**：

1. **任务拆解**
   - 用户故事 → 技术任务 映射

2. **工具链调用（子代理内部）**
   - MCP 顺序：`search_file_content → read_file / read_many_files  → code / write_file /replace`
   - 未经 search_file_content 定位，不得调用 read_many_files
   - 任一 write_file / replace 视为代码修改，必须可回溯至 planner 决策

3. **代码评审（三重门禁）**
   - 静态扫描
   - 单元测试
   - 性能剖析

4. **结果验证（双签字）**
   - CR 检查单
   - 工程原则检查单

---

## 5. 编程规范（强制）

| 维度 | 规范 |
|----|----|
| 命名 | 英文驼峰 / 蛇式；禁止拼音；常量 ALL_CAPS |
| 函数 | ≤80 行；圈复杂度 ≤5；优先纯函数 |
| 类 | 单文件单类；职责>1 必须拆 |
| 注释 | 公共 API 必须 docstring |
| 异常 | 禁止裸 except；自定义异常继承 DomainException |
| 测试 | 新增代码覆盖率 ≥90%；TDD |

---

## 6. 工程原则检查单（YAGNI 守门）

- [ ] SOLID
- [ ] KISS
- [ ] DRY
- [ ] YAGNI

---

## 6.1 UX 工程原则（体验版）

- 不为“可能的用户”设计路径
- 不为“未来功能”预留入口
- 一个页面只解决一个核心任务
- 每一步交互必须有明确收益

---

## 7. 安全检查单（零容忍）

- [ ] 无硬编码密钥 / 密码
- [ ] 无动态拼接 SQL / Shell / URL
- [ ] 无反序列化不可信数据
- [ ] 第三方依赖已完成漏洞扫描

---

## 8. 输出格式契约

- 代码块必须包含：语言标记 + 文件名
- 架构图必须使用 Mermaid
- 时序图必须包含超时标注
- 建议统一使用表格：

| 优先级 | 影响面 | 落地成本 |
|------|------|--------|

---

## 9. 主上下文职责（严格限制）

主上下文 **只允许执行 3 件事**：

1. 识别  
2. 路由  
3. 验收  

> UI / UX 产出仅作为**约束输入**，不视为实现  
> ❌ 禁止分析、设计、编码、实现细节
