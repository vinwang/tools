#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IFLOW UserPromptSubmit Hook
作用：
- 每次对话强制注入 IFLOW.md 执行约束
- 幂等（不会重复注入）
- 作为 system-level contract，而不是“提醒”
"""

import json
import sys

IFLOW_CONTRACT_TAG = "[[IFLOW_EXECUTION_CONTRACT]]"

IFLOW_CONTRACT = f"""
{IFLOW_CONTRACT_TAG}
当前对话受 IFLOW.md 约束（规则源）。

以下为【执行期强制锚点】，用于判定合规性：

- IFLOW-RULE-001：任务必须先经 planner 决策与拆解
- IFLOW-RULE-002：禁止任何 agent 自由生成实现代码
- IFLOW-RULE-003：代码仅允许在 planner 明确授权后出现
- IFLOW-RULE-004：任一失败必须回流 planner
- IFLOW-RULE-005：所有输出必须使用简体中文

若出现冲突或歧义，以 IFLOW.md 为最终裁定依据。
"""

def main():
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        # 无法解析就原样放行
        sys.exit(0)

    prompt = input_data.get("prompt", "")
    if not isinstance(prompt, str):
        sys.exit(0)

    # 幂等性检查：已注入则不再重复
    if IFLOW_CONTRACT_TAG in prompt:
        print(json.dumps(input_data, ensure_ascii=False))
        return

    # 强制前置注入（而不是拼在后面）
    new_prompt = IFLOW_CONTRACT.strip() + "\n\n" + prompt.strip()

    input_data["prompt"] = new_prompt
    print(json.dumps(input_data, ensure_ascii=False))


if __name__ == "__main__":
    main()