"""
Task Graph Prompts — 三阶段提示词（规划 / 节点执行 / 总结）。

第一阶段  _task_planning:     LLM 将用户请求分解为 DAG 节点图
第二阶段  _task_graph_node_loop:  每个节点执行（注入图状态 + tool结果）
第三阶段  _task_finalizer:    将所有节点结果汇总

本文件为提示词维护入口，包含：
- 公共规则（ASK_USER_RULES / COMMON_JSON_CONTRACT，自 common_prompt.py 迁入）
- 内置意图领域指引（DOMAIN_SECTIONS；generic 自 research_prompts.py 迁入并更名重写）
- 三阶段提示词模板

规划提示词（SYSTEM_PROMPT_TASK_PLANNING）中的意图列表是动态注入的：
- `generic` 为固定兜底意图，始终出现在列表中（常量 GENERIC_INTENT_* 兜底，
  前后端均禁止删除/禁用）
- 其余意图（ppt、coding 等，均在 Helix.json 的 intents.* 中配置）从配置注入，
  仅列出 enabled 的意图
- 每个意图可在 intents.* 中配置 planning_prompt / node_prompt / finalizer_prompt
  字段覆盖默认提示词（ppt/coding 的提示词已全部迁入 Helix.json）

系统提示词通过 {ask_user_rules} 占位符注入公共提问决策规则、通过
{planning_guidelines} 占位符注入核心工作编号列表（auto 模式含意图分类说明，
forced 模式意图已确定则不含）、通过 {intent_catalog_section} 占位符注入
可用意图列表段（auto 模式注入，forced 模式为空）、通过 {domain_section}
占位符注入领域指引段（forced 模式取目标意图的 planning_prompt 或内置
DOMAIN_SECTIONS，auto 模式拼接各可用意图配置的 planning_prompt 指引段）。
规划与节点执行提示词均通过 {available_tools} 占位符注入可用工具列表段
（由 format_tools_section 生成；节点执行段标题见 NODE_TOOLS_HEADING）。
"""

import json
from typing import List, Optional

from HelixCore.tools.base import ToolDefinition

# ═══════════════════════════════════════════════════════════════════
# 公共规则（自 common_prompt.py 迁入）
# ═══════════════════════════════════════════════════════════════════

ASK_USER_RULES = """\
提问决策规则：
1. 上下文已有明确信息，绝不提问；
2. 存在多种可能性、缺少关键参数，禁止猜测，必须调用ask_user；
3. 一次尽量把多个疑问合并成一个问题，不要分多次零散提问；
4. 能通过其他工具（搜索）获取的信息，优先工具，不要直接问用户。"""


COMMON_JSON_CONTRACT = """\
## JSON 输出契约（必须严格遵守）

1. 只输出一个 JSON 对象，不要包含 markdown 代码块标记（```、```json）、注释或任何其他文字
2. 字符串字段内禁止真实换行：多行内容必须使用转义符 \\n 连接，例如 "第一行\\n第二行"
3. 字符串字段内禁止真实制表符（\\t），同理使用转义符
4. 布尔值使用 true/false，不要写成 True/False 或带引号的字符串

正确示例（字符串内多行用 \\n 转义，整个 JSON 保持单行字符串）：
{"response": "第一行\\n第二行"}

错误示例（字符串内真实换行，会导致整个 JSON 解析失败）：
{"response": "第一行
第二行"}
"""

# ═══════════════════════════════════════════════════════════════════
# 内置意图常量
# ═══════════════════════════════════════════════════════════════════

# generic 为固定兜底意图：始终出现在规划提示词的意图列表中，
# 后端（intent_router）与前端（意图管理页）均禁止删除或禁用。
GENERIC_INTENT_ID = "generic"
GENERIC_INTENT_NAME = "通用任务"
GENERIC_INTENT_DESC = "回答一般性问题、处理一般性事务，必要时通过搜索等工具获取信息"

# ═══════════════════════════════════════════════════════════════════
# 内置意图领域指引（规划阶段领域补充段）
# ═══════════════════════════════════════════════════════════════════

# generic 领域段（原 research_prompts.py 迁入并更名重写）：
# 原"搜索研究"意图扩展为一般任务兜底意图，覆盖一般问题解答与一般性事务。
DOMAIN_SECTION_GENERIC = """## 通用任务领域补充

当前请求意图已确定为: **generic**。你是通用任务处理 Agent,负责为任务分解提供领域指导。

### 领域任务分解指引
- 通用任务涵盖:一般性问题解答、信息查询、资料整理、一般性事务处理等
- 需要最新或外部信息时,使用 `web_search` 搜索、`web_fetch_batch` 抓取页面内容
- 涉及文件读写、代码执行、命令操作时,使用对应的文件/Shell 工具
- 多来源信息需交叉验证,结论注明来源与不确定性
"""

# 内部定义意图仅 generic（固定兜底）；ppt/coding 等其余意图的提示词
# 全部由 Helix.json 的 intents.* 配置提供（planning_prompt 等字段）。
DOMAIN_SECTIONS = {
    "generic": DOMAIN_SECTION_GENERIC,
}

# ═══════════════════════════════════════════════════════════════════
# Phase 1 — Task Planning
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_TASK_PLANNING = """# AI Agent Orchestrator — Task Planning Engine

你是一个混合 AI Agent 系统的调度核心。你的工作是根据用户请求做任务规划：

{planning_guidelines}

## 任务分解原则
- 每个节点有**单一且明确的目标**
- 节点粒度适中：一个节点 = 一组可以批量调用的工具
- 将最大的依赖链放在前面，减少等待

## 工具选择 (initial_tool_calls)
- 工具名必须从下方 **Available Tools** 列表中选择真实存在的工具，不要编造
- 只选择与节点目标相关的工具；若没有合适工具，`initial_tool_calls` 可留空 `[]`
- `arguments` 参数格式参考各工具的 Parameters 定义

{available_tools}

## 初始工具调用
- 对于**简单节点**（工具参数可以预先确定的），在 `initial_tool_calls` 中提供完整的工具调用，**可以一次提供多个调用**，系统会在节点执行时批量直接执行，再把结果交给 LLM 分析，避免多余的往返
- 对于**复杂节点**（参数依赖中间结果，如搜索词依赖前序节点的输出），必须将 `initial_tool_calls` 留空 `[]`，执行阶段再决定

## 规划阶段提问
- 若任务信息不足、缺少关键参数、**无法完成规划**，在 JSON 顶层返回 `"tools"` 字段调用 ask_user 提问，格式为 `{"tools": [{"name": "ask_user", "arguments": {"question": "..."}}]}`
- 系统会先向用户提问，拿到回答后携带回答**重新规划**；禁止在信息不足时猜测关键参数硬做规划
{ask_user_rules}

{intent_catalog_section}

{domain_section}
"""

USER_PROMPT_TASK_PLANNING = """# Task Planning Request

## User Request
{user_request}

请分析用户请求，返回 JSON 格式的任务规划。

## JSON Response Format
```json
{{
  "intent_type": "{intent_enum}",
  "task_graph_nodes": [
    {{
      "id": "node_1",
      "title": "节点的明确任务描述",
      "initial_tool_calls": [
        {{"name": "web_search", "arguments": {{"query": "..."}}}},
        {{"name": "web_fetch", "arguments": {{"url": "..."}}}}
      ],
      "depends": [],
      "can_parallel": false
    }},
    {{
      "id": "node_2",
      "title": "节点2描述",
      "initial_tool_calls": [],
      "depends": ["node_1"],
      "can_parallel": false
    }}
  ],
  "tools": [],
  "task_complete": false,
  "response": "",
  "reason": "任务分解的思考和原因",
  "need_finalizer": true
}}
```

信息不足、**无法完成规划**时，不返回 `task_graph_nodes`，改用顶层 `tools` 调用 ask_user 提问：

```json
{{
  "intent_type": "generic",
  "task_graph_nodes": [],
  "tools": [{{"name": "ask_user", "arguments": {{"question": "需要向用户确认的问题"}}}}],
  "task_complete": false,
  "response": "",
  "reason": "信息不足，无法完成规划，需要向用户提问",
  "need_finalizer": false
}}
```

## 字段说明
- `intent_type`: 意图分类
- `task_graph_nodes`: 任务节点列表
  - `id`: 节点唯一标识 (node_1, node_2, ...)
  - `title`: 节点任务描述（LLM 执行时理解）
  - `initial_tool_calls`: 节点的初始工具调用（可选）。仅当参数可预先确定时填写完整的调用列表，**可包含多个** `{{"name", "arguments"}}`，系统会先批量直接执行再进入 LLM 迭代；复杂节点必须留空 `[]`
  - `depends`: 依赖的节点 ID 列表
  - `can_parallel`: 是否可以与其他无依赖节点并行
- `task_complete`: 如果用户的问题可以直接回答（不需要任何工具），设为 true 并填写 `response`；需要工具的任务必须返回 `task_graph_nodes` 节点图，不得跳过规划直接回答
- `response`: 当 task_complete 为 true 时，直接回复用户
- `tools`: （可选）仅当任务信息不足**无法完成规划**时，在顶层用该字段调用 ask_user 提问（见上方替代示例）；系统会先提问并携带回答重新规划。其余情况省略该字段或留空 `[]`
- `reason`: 你的分解思路
- `need_finalizer`: 是否需要在所有节点完成后进行总结

{json_contract}
"""

# ═══════════════════════════════════════════════════════════════════
# 核心工作指引整段文本（build_planning_guidelines 使用）
# ═══════════════════════════════════════════════════════════════════

# forced / auto 两种模式分别维护整段编号文本，避免运行时按子串拼接：
# - PLANNING_GUIDELINES_FORCED：意图已确定，无意图分类条目（第 1-3 条）
# - PLANNING_GUIDELINES_AUTO：含意图分类条目（第 1-4 条），其中
#   {intent_types} 占位符由 build_planning_guidelines 注入动态意图枚举
PLANNING_GUIDELINES_FORCED = """\
1. **任务分解**：将任务拆解为可独立执行的 DAG 节点，每个节点有明确目标
2. **依赖管理**：节点之间有依赖关系，必须等依赖节点完成才能执行
3. **并行判断**：没有依赖关系的节点可以并行执行"""

PLANNING_GUIDELINES_AUTO = """\
1. **意图分类 (intent_type)**：判断用户需要是哪个意图类型（{intent_types}）
2. **任务分解**：将任务拆解为可独立执行的 DAG 节点，每个节点有明确目标
3. **依赖管理**：节点之间有依赖关系，必须等依赖节点完成才能执行
4. **并行判断**：没有依赖关系的节点可以并行执行"""

# ═══════════════════════════════════════════════════════════════════
# Phase 2 — Node Execution（按 intent 分类）
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_NODE_GENERIC = """# Generic Node Execution Agent

你是通用任务处理 Agent。当前正在执行任务图中的一个节点。

## 你的工作方式
1. 根据当前节点的任务描述，使用合适的工具获取信息或完成任务
2. 一次尽可能多地返回需要调用的工具列表，系统会批量执行
3. 分析工具返回的结果
4. 当节点目标完成时，标记 node_complete=true
5. 如果此路不通，可以更新任务图切换到其他路径

## 工具使用规范
- 涉及文件读写、代码执行等一般性任务时，使用对应的文件/Shell 工具
- 工具调用可以一次返回多个，系统会并行执行

{available_tools}

## 提问决策规则
{ask_user_rules}
"""

SYSTEM_PROMPT_NODE_DEFAULT = """# AI Agent Node Execution

你是 AI Agent。当前正在执行任务图中的一个节点。

根据节点描述使用合适的工具完成任务。
一次可以返回多个工具调用，系统会批量执行。

{available_tools}

## 提问决策规则
{ask_user_rules}
"""

USER_PROMPT_NODE_EXECUTION = """# Node Execution

## Current Node
- **Node ID**: {node_id}
- **Title**: {node_title}
- **Initial Tool Calls**: {initial_tool_calls}

## Tool Results History
{tool_results}

## Latest Node Graph Execution Conversation
{conversation_history}

## Entire Task Graph Status
{graph_state}

## Instruction
请根据当前节点的任务，判断是否需要继续调用工具。

### JSON Response Format
```json
{{
  "reason": "分析过程",
  "response": "节点的完成总结",
  "node_complete": true,
  "tools": [
    {{
      "name": "tool_name",
      "arguments": {{ "param1": "value1" }}
    }}
  ],
  "need_update_node": false,
  "task_graph_nodes": []
}}
```

当前路径走不通、需要切换路径时，返回 `need_update_node=true`，并在 `task_graph_nodes` 中提供**新的完整节点图**（已完成的节点保留原 id，失败路径的节点不再包含）：

```json
{{
  "reason": "node_2 路径执行失败，切换为 node_3 替代路径",
  "response": "",
  "node_complete": false,
  "tools": [],
  "need_update_node": true,
  "task_graph_nodes": [
    {{
      "id": "node_1",
      "title": "已完成的节点，保留原 id 以保留其结果",
      "initial_tool_calls": [],
      "depends": [],
      "can_parallel": false
    }},
    {{
      "id": "node_3",
      "title": "替代路径节点：搜索备选方案并整理结果",
      "initial_tool_calls": [
        {{"name": "web_search", "arguments": {{"query": "备选方案关键词"}}}}
      ],
      "depends": ["node_1"],
      "can_parallel": false
    }}
  ]
}}
```

### 字段说明
- `reason`: 你的分析思考过程
- `response`: 节点的完成总结（当 node_complete=true 时）
- `node_complete`: 当前节点是否已完成
  - true: 节点完成，不再需要工具调用
  - false: 需要继续调用工具
- `tools`: 当 node_complete=false 时，需要调用的工具列表（可多个，系统会批量执行）
- `need_update_node`: 是否需要更新任务图（当前路径走不通时，切换到其他路径）
- `task_graph_nodes`: 任务节点列表（当 need_update_node=true 时必填，否则留空 `[]`）
  - `id`: 节点唯一标识 (node_1, node_2, ...)；**已完成的节点保留原 id** 以保留其结果，失败路径的节点不再包含
  - `title`: 节点任务描述（LLM 执行时理解）
  - `initial_tool_calls`: 节点的初始工具调用（可选）。仅当参数可预先确定时填写完整的调用列表，**可包含多个** `{{"name", "arguments"}}`，系统会先批量直接执行再进入 LLM 迭代；复杂节点必须留空 `[]`
  - `depends`: 依赖的节点 ID 列表
  - `can_parallel`: 是否可以与其他无依赖节点并行

### 重要规则
1. 尽可能一次返回多个工具调用，减少往返次数
2. 工具的调用结果会在下一次迭代中通过 "Tool Results History" 提供
3. 如果当前路径走不通（如工具连续失败），返回 need_update_node=true 切换路径
4. 切换路径时，在 task_graph_nodes 中提供新的完整节点图，失败的路径节点不再包含
5. 已失败的节点路径会通过 "Failed paths (avoid)" 提示你，不要重复尝试

{json_contract}
"""

# ═══════════════════════════════════════════════════════════════════
# Phase 3 — Finalizer（按 intent 分类）
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_FINALIZER_GENERIC = """# AI Agent — Task Finalizer

你是 AI Agent 总结分析专家。你的任务是将所有节点的执行结果汇总为用户可以直接使用的最终答案。

- 通用任务：输出完整的研究报告或处理结果
"""

# 兜底总结提示词（未知 / 未配置意图时使用）：仅含通用总结要求，不含意图特化表述
SYSTEM_PROMPT_FINALIZER = """# AI Agent — Task Finalizer

你是 AI Agent 总结分析专家。你的任务是将所有节点的执行结果汇总为用户可以直接使用的最终答案。
"""

SYSTEM_PROMPTS_FINALIZER = {
    "generic": SYSTEM_PROMPT_FINALIZER_GENERIC,
}

USER_PROMPT_FINALIZER = """# Final Summary

## User Request
{user_request}

## All Node Results
{graph_summary}

请根据所有节点的执行结果，生成最终的回答。
要求：
1. 直接、清晰地回答用户的问题
2. 根据执行结果组织内容，使用标题分段
3. 如果有生成的文件，说明文件位置和内容

## JSON Response Format
```json
{{
  "reason": "总结思路",
  "final_answer": "最终回复内容（支持 Markdown）"
}}
```

{json_contract}
"""

# ── 按 intent 获取 node system prompt ──────────────────────────

SYSTEM_PROMPTS_NODE = {
    "generic": SYSTEM_PROMPT_NODE_GENERIC,
}

# ── 可用工具列表段 ──────────────────────────────────────────────

# 节点执行阶段 tools 段的标题（get_node_system_prompt 注入 {available_tools}
# 时使用）；规划阶段使用 format_tools_section 的默认标题 "## Available Tools"。
NODE_TOOLS_HEADING = (
    "Available tools (respond with JSON that includes tool_calls if needed):"
)


def format_tools_section(
    tools: List[ToolDefinition],
    heading: str = "## Available Tools",
) -> str:
    """将工具定义格式化为提示词文本块：标题 + 每个工具一行。

    供规划 / 节点执行阶段注入 {available_tools} 占位符使用；tools 为空时
    仅返回标题（与调用方行为约定一致）。
    """
    parts = [heading]
    for t in tools:
        parts.append(f"- {t.name}: {t.description}")
        parts.append(
            f"  Parameters: {json.dumps(t.parameters, ensure_ascii=False)}"
        )
    return "\n".join(parts)


def get_node_system_prompt(
    intent_type: str,
    intents_cfg: Optional[dict] = None,
    tools: Optional[List[ToolDefinition]] = None,
) -> str:
    """根据 intent 获取对应的节点执行 system prompt（含可用工具列表段）。

    intents_cfg 为 Helix.json 的 intents.* 配置；若某意图配置了非空的
    node_prompt 字段则优先使用（支持用户自定义意图的节点提示词），
    否则回退内置注册表 SYSTEM_PROMPTS_NODE，最后兜底 SYSTEM_PROMPT_NODE_DEFAULT。

    可用工具列表经 {available_tools} 占位符注入（tools 为 None 时不注入）；
    自定义 node_prompt 未含该占位符时在末尾追加 tools 段，保证自定义
    提示词仍能拿到工具列表。
    """
    configured = ""
    if intents_cfg:
        cfg = intents_cfg.get(intent_type) or {}
        configured = (cfg.get("node_prompt") or "").strip()
    raw = configured or SYSTEM_PROMPTS_NODE.get(intent_type, SYSTEM_PROMPT_NODE_DEFAULT)
    prompt = raw.replace("{ask_user_rules}", ASK_USER_RULES)
    tools_section = (
        format_tools_section(tools, heading=NODE_TOOLS_HEADING)
        if tools is not None
        else ""
    )
    if "{available_tools}" in prompt:
        return prompt.replace("{available_tools}", tools_section)
    if tools_section:
        return f"{prompt}\n\n{tools_section}"
    return prompt


def get_finalizer_system_prompt(
    intent_type: str,
    intents_cfg: Optional[dict] = None,
) -> str:
    """根据 intent 获取对应的总结（finalizer）system prompt。

    intents_cfg 为 Helix.json 的 intents.* 配置；若某意图配置了非空的
    finalizer_prompt 字段则优先使用（支持用户自定义意图的总结提示词），
    否则回退内置注册表 SYSTEM_PROMPTS_FINALIZER，最后兜底 SYSTEM_PROMPT_FINALIZER。
    """
    configured = ""
    if intents_cfg:
        cfg = intents_cfg.get(intent_type) or {}
        configured = (cfg.get("finalizer_prompt") or "").strip()
    return configured or SYSTEM_PROMPTS_FINALIZER.get(
        intent_type, SYSTEM_PROMPT_FINALIZER
    )


# ── 规划提示词动态构建 ──────────────────────────────────────────

def build_available_intents_section(intents_cfg: dict) -> str:
    """构建"可用的意图类型"列表段。

    generic 为固定兜底意图，无论配置 enabled 与否始终列出第一行；
    其余意图仅列出配置中 enabled 的意图（含用户新增意图）。
    """
    generic_cfg = intents_cfg.get(GENERIC_INTENT_ID) or {}
    lines = [
        f"- `{GENERIC_INTENT_ID}`: {generic_cfg.get('name') or GENERIC_INTENT_NAME}"
        f" — {generic_cfg.get('description') or GENERIC_INTENT_DESC}"
    ]
    for intent_id, cfg in intents_cfg.items():
        if intent_id == GENERIC_INTENT_ID:
            continue
        if not (cfg or {}).get("enabled", True):
            continue
        name = cfg.get("name") or intent_id
        desc = cfg.get("description")
        if desc:
            lines.append(f"- `{intent_id}`: {name} — {desc}")
        else:
            lines.append(f"- `{intent_id}`: {name}")
    return "\n".join(lines)


def build_planning_guidance_sections(intents_cfg: dict) -> str:
    """构建 auto 模式下的意图领域指引段（仅使用配置的 planning_prompt）。

    内置意图的代码领域段（DOMAIN_SECTIONS）含"意图已确定为"表述，仅供
    forced 模式使用；auto 模式下依赖配置的 planning_prompt（中性表述）
    提供指引，未配置该字段的意图无指引段。
    """
    sections = []
    for intent_id, cfg in intents_cfg.items():
        if not cfg:
            continue
        if intent_id != GENERIC_INTENT_ID and not cfg.get("enabled", True):
            continue
        prompt = (cfg.get("planning_prompt") or "").strip()
        if not prompt:
            continue
        name = cfg.get("name") or intent_id
        sections.append(
            f"## 领域指引（当规划意图为 `{intent_id}`（{name}）的任务时适用）\n\n{prompt}"
        )
    return "\n\n".join(sections)


def _enabled_intent_ids(intents_cfg: dict) -> list:
    """返回 generic + 配置中 enabled 的意图 ID 列表（generic 固定在最前）。

    供 build_intent_enum / build_intent_types_list 复用，保证各占位符
    的意图枚举口径一致。
    """
    ids = [GENERIC_INTENT_ID]
    for intent_id, cfg in intents_cfg.items():
        if intent_id == GENERIC_INTENT_ID:
            continue
        if (cfg or {}).get("enabled", True):
            ids.append(intent_id)
    return ids


def build_intent_enum(intents_cfg: dict) -> str:
    """构建 USER_PROMPT_TASK_PLANNING 中 intent_type 字段的取值枚举。

    generic 固定在最前，其后为配置中 enabled 的意图。
    """
    return " | ".join(_enabled_intent_ids(intents_cfg))


def build_intent_types_list(intents_cfg: dict) -> str:
    """构建 SYSTEM_PROMPT_TASK_PLANNING 意图分类说明中的意图类型列举。

    仅 generic 为固定兜底意图，其余意图（如 ppt、coding 等配置注册的意图）均
    从配置动态获取，仅列出 enabled 的意图；新增或删除意图时自动同步。
    """
    return " / ".join(_enabled_intent_ids(intents_cfg))


def build_planning_guidelines(intents_cfg: dict, forced_intent: str = "") -> str:
    """构建"核心工作"编号列表整段文本。

    forced 模式返回 PLANNING_GUIDELINES_FORCED（意图已确定，无意图分类
    条目）；auto 模式返回 PLANNING_GUIDELINES_AUTO 并注入 {intent_types}
    占位符（意图枚举从配置动态生成）。
    """
    if forced_intent:
        return PLANNING_GUIDELINES_FORCED
    return PLANNING_GUIDELINES_AUTO.replace(
        "{intent_types}", build_intent_types_list(intents_cfg)
    )


def _build_domain_section(intents_cfg: dict, forced_intent: str = "") -> str:
    """构建注入 {domain_section} 占位符的领域指引段（空串则不产生该段）。

    - forced 模式：目标意图配置了非空 planning_prompt 时使用配置内容（中性
      表述，由本函数补充"意图已确定为"声明）；未配置则回退内置 DOMAIN_SECTIONS；
      两者皆无（如未知意图）返回空串
    - auto 模式：拼接全部可用意图配置的 planning_prompt 指引段（无配置则为空）
    """
    if forced_intent:
        cfg = intents_cfg.get(forced_intent) or {}
        configured = (cfg.get("planning_prompt") or "").strip()
        if configured:
            name = cfg.get("name") or forced_intent
            return (
                f"## 当前请求意图已确定为: **{forced_intent}**（{name}）\n\n"
                f"{configured}"
            )
        return DOMAIN_SECTIONS.get(forced_intent, "")
    return build_planning_guidance_sections(intents_cfg)


def build_system_prompt_task_planning(
    intents_cfg: dict,
    forced_intent: str = "",
    tools: Optional[List[ToolDefinition]] = None,
) -> str:
    """构建任务规划阶段 system prompt（仅做占位符替换，不做字符串拼接）。

    模板 SYSTEM_PROMPT_TASK_PLANNING 的五个占位符依次替换：
    - {planning_guidelines} / {ask_user_rules}：核心工作列表 / 提问决策规则
    - {intent_catalog_section}：可用意图列表段（仅 auto 模式注入，forced 为空）
    - {domain_section}：领域指引段，由 _build_domain_section 按模式生成
    - {available_tools}：可用工具列表段（tools 为空或 None 时不注入）

    intents_cfg: Helix.json 中的 intents.* 配置（dict，允许为空）
    forced_intent: 强制指定意图时传入（如配置中注册的意图 ID）；为空表示自动识别。
        - forced: 不注入意图分类说明与可用意图列表；领域指引段优先取配置的
          planning_prompt（中性表述，由本函数补充"意图已确定为"声明），
          未配置则回退内置 DOMAIN_SECTIONS
        - auto: 注入意图分类说明、可用意图列表，以及全部可用意图的
          配置 planning_prompt 指引段
    """
    return SYSTEM_PROMPT_TASK_PLANNING.replace(
        "{ask_user_rules}", ASK_USER_RULES
    ).replace(
        "{planning_guidelines}", build_planning_guidelines(intents_cfg, forced_intent)
    ).replace(
        "{intent_catalog_section}",
        ""
        if forced_intent
        else f"\n## 可用的意图类型\n{build_available_intents_section(intents_cfg)}\n",
    ).replace(
        "{domain_section}", _build_domain_section(intents_cfg, forced_intent),
    ).replace(
        "{available_tools}", format_tools_section(tools) if tools else "",
    )
