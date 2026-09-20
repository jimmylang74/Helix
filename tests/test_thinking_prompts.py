"""Tests for Thinking intent prompts（固定内部意图：被动接收 · 事件驱动）。

Covers:
- build_thinking_planning_system_prompt: 画像/日期时间/地点注入、默认降级、
  4000 字符截断、提问段按 ask_user 可用性注入/裁剪、工具列表注入
- render_thinking_planning_user_prompt: 固定 intent_enum、提问示例插拔
- 被动语义断言：提示词含"被动接收"语义、不含主动订阅/轮询指令
- 结构保证：Thinking 规划提示词无意图分类段（intent_catalog）；auto 模式
  规划提示词与意图目录不含 thinking
- 注册：thinking 进入 SYSTEM_PROMPTS_NODE / SYSTEM_PROMPTS_FINALIZER，
  get_node_system_prompt / get_finalizer_system_prompt 可解析
"""

from HelixCore.prompts.task_graph_prompts import (
    SYSTEM_PROMPTS_FINALIZER,
    SYSTEM_PROMPTS_NODE,
    build_available_intents_section,
    build_intent_enum,
    build_system_prompt_task_planning,
    get_finalizer_system_prompt,
    get_node_system_prompt,
)
from HelixCore.prompts.thinking_prompts import (
    MAX_PROFILE_CHARS,
    SYSTEM_PROMPT_THINKING_FINALIZER,
    SYSTEM_PROMPT_THINKING_NODE,
    THINKING_INTENT_ID,
    build_thinking_planning_system_prompt,
    render_thinking_planning_user_prompt,
)
from HelixCore.tools.base import ToolDefinition


def _tool(name: str, description: str = "工具描述") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
    )


# ── 组装函数：画像 / 日期时间 / 地点注入 ────────────────────────


def test_injections_render_into_system_prompt():
    prompt = build_thinking_planning_system_prompt(
        agent_profile="我是测试画像",
        datetime_text="日期时间: 2026-09-19 08:00:00\n星期: 星期六\n时区: UTC+8",
        location_text="当前城市: 南京 (Nanjing)",
        tools=None,
    )
    assert "## 智能体画像（Helix.md）\n我是测试画像" in prompt
    assert "日期时间: 2026-09-19 08:00:00" in prompt
    assert "当前城市: 南京 (Nanjing)" in prompt
    assert "# Thinking Agent" in prompt


def test_empty_injections_fall_back_gracefully():
    prompt = build_thinking_planning_system_prompt()
    assert "未配置 Helix.md 画像" in prompt
    assert "## 当前日期时间\n（未提供）" in prompt
    assert "## 当前地点\n（未提供）" in prompt


def test_profile_truncated_at_max_chars():
    tail_marker = "截断尾部标记"
    long_profile = "兴" * MAX_PROFILE_CHARS + tail_marker
    prompt = build_thinking_planning_system_prompt(agent_profile=long_profile)
    assert "兴" * MAX_PROFILE_CHARS in prompt
    assert tail_marker not in prompt
    assert "已截断" in prompt


# ── 组装函数：提问段 / 工具段按工具表条件注入 ────────────────────


def test_no_ask_user_tool_trims_ask_section():
    with_ask = build_thinking_planning_system_prompt(tools=None)  # None=未知工具集，保留
    assert "## 规划阶段提问" in with_ask
    without_ask = build_thinking_planning_system_prompt(tools=[])
    assert "## 规划阶段提问" not in without_ask
    assert "{ask_user_rules}" not in without_ask


def test_tools_section_injected():
    tools = [_tool("web_search"), _tool("ask_user", "向用户提问")]
    prompt = build_thinking_planning_system_prompt(tools=tools)
    assert "- web_search: 工具描述" in prompt
    assert "- ask_user: 向用户提问" in prompt
    assert "{available_tools}" not in prompt


# ── 被动语义：有匹配、无主动订阅 ────────────────────────────────


def test_passive_reactive_semantics_present():
    prompt = build_thinking_planning_system_prompt()
    for phrase in ("被动接收", "不主动轮询或订阅", "兴趣匹配", "深度挖掘", "未命中 → 轻量处理"):
        assert phrase in prompt


def test_no_active_subscription_rhetoric():
    prompt = build_thinking_planning_system_prompt()
    for forbidden in ("主动订阅", "每日收集", "定时轮询"):
        assert forbidden not in prompt


# ── 结构保证：不进意图目录 / 不参与自动分类 ──────────────────────


def test_thinking_system_prompt_has_no_intent_catalog():
    prompt = build_thinking_planning_system_prompt()
    assert "可用的意图类型" not in prompt
    assert "intent_type" not in prompt


def test_auto_mode_excludes_thinking_from_catalog_and_enum():
    intents_cfg = {
        "generic": {"name": "通用任务", "description": "兜底意图", "enabled": True},
        "coding": {"name": "代码生成", "description": "生成代码", "enabled": True},
    }
    auto_prompt = build_system_prompt_task_planning(intents_cfg, tools=None)
    assert THINKING_INTENT_ID not in auto_prompt
    assert THINKING_INTENT_ID not in build_available_intents_section(intents_cfg)
    assert THINKING_INTENT_ID not in build_intent_enum(intents_cfg)


# ── 规划用户提示词 ──────────────────────────────────────────────


def test_user_prompt_fixed_intent_enum():
    tpl = render_thinking_planning_user_prompt(tools=[])
    user_prompt = tpl.format(
        user_request="外部事件内容",
        json_contract='{"strict": true}',
        intent_enum=THINKING_INTENT_ID,
    )
    assert '"intent_type": "thinking"' in user_prompt
    assert "外部事件内容" in user_prompt
    assert "兴趣命中判断" in user_prompt
    assert "{user_request}" not in user_prompt
    assert "{intent_enum}" not in user_prompt


def test_user_prompt_ask_example_toggled_by_tools():
    with_ask = render_thinking_planning_user_prompt(tools=None)
    assert "无法完成规划" in with_ask
    assert '"intent_type": "thinking"' in with_ask
    without_ask = render_thinking_planning_user_prompt(tools=[])
    assert "无法完成规划" not in without_ask
    assert "{planning_ask_example}" not in without_ask


# ── 注册表：节点执行 / 总结阶段可解析 ────────────────────────────


def test_thinking_registered_in_builtin_registries():
    assert SYSTEM_PROMPTS_NODE.get(THINKING_INTENT_ID) == SYSTEM_PROMPT_THINKING_NODE
    assert (
        SYSTEM_PROMPTS_FINALIZER.get(THINKING_INTENT_ID)
        == SYSTEM_PROMPT_THINKING_FINALIZER
    )


def test_get_node_system_prompt_resolves_thinking():
    prompt = get_node_system_prompt(THINKING_INTENT_ID, tools=None)
    assert "Thinking Node Execution Agent" in prompt
    assert "不重新决策" in prompt
    assert "提问决策规则" in prompt  # 未知工具集（None）→ 保留提问段

    no_ask = get_node_system_prompt(THINKING_INTENT_ID, tools=[])
    assert "提问决策规则" not in no_ask


def test_get_finalizer_system_prompt_resolves_thinking():
    prompt = get_finalizer_system_prompt(THINKING_INTENT_ID)
    assert "Thinking 总结反馈" in prompt
    assert "命中兴趣" in prompt
    assert "未命中兴趣" in prompt