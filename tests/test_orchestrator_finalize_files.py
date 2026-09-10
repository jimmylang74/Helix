"""Tests for finalizer file-declaration contract (A2) and shared merge gate.

Covers:
- A2: finalizer response generated_files parse + real-file gate + merge/dedup
- A1: whitelisted tool return-value collection (write_file format + others)
- prompt contract text (USER_PROMPT_FINALIZER includes generated_files + example)
"""

from unittest.mock import MagicMock, patch

import os

import pytest

from HelixCore.orchestrator.agent_state import create_initial_state
from HelixCore.orchestrator.config import AgentConfig
from HelixCore.orchestrator.orchestrator import (
    AgentOrchestrator,
    FILE_GENERATING_TOOLS,
)
from HelixCore.orchestrator.task_graph import TaskGraph
from HelixCore.prompts.task_graph_prompts import (
    PLANNING_GUIDELINES_AUTO,
    PLANNING_GUIDELINES_FORCED,
    USER_PROMPT_FINALIZER,
    USER_PROMPT_TASK_PLANNING,
)


@pytest.fixture
def orchestrator():
    llm = MagicMock()
    llm.get_provider_model.return_value = ("unknown", "mock-model")
    orch = AgentOrchestrator(
        llm_backend=llm,
        config=AgentConfig(),
        event_sink=MagicMock(),
        intent_provider=MagicMock(),
        tool_registry=MagicMock(),
        event_bus=MagicMock(),
    )
    return orch


@pytest.fixture
def ready_state(orchestrator):
    state = create_initial_state("测试请求", "req_final_test")
    graph = TaskGraph(nodes=[{"id": "node_1", "title": "任务", "depends": []}])
    graph.set_node_done("node_1", "执行完毕")
    orchestrator._graphs["req_final_test"] = graph
    return state


def _run_finalizer(orch, state, response):
    patch.object(orch, "_check_token_budget", return_value=True).start()
    patch.object(orch, "_record_usage").start()
    try:
        orch.llm.ask_json.return_value = response
        orch._task_finalizer(state)
    finally:
        patch.stopall()


class TestFinalizerDeclaration:
    def test_declared_real_absolute_path_is_collected(self, orchestrator, ready_state, tmp_path):
        f = tmp_path / "report.md"
        f.write_text("hello", encoding="utf-8")
        _run_finalizer(
            orchestrator,
            ready_state,
            {"reason": "r", "final_answer": "完成", "generated_files": [str(f)]},
        )
        assert ready_state.get("generated_files") == [str(f)]
        assert ready_state["final_result"] == "完成"

    def test_nonexistent_path_is_dropped(self, orchestrator, ready_state, tmp_path):
        existent = tmp_path / "ok.md"
        existent.write_text("x", encoding="utf-8")
        ghost = tmp_path / "ghost.md"
        _run_finalizer(
            orchestrator,
            ready_state,
            {"final_answer": "a", "generated_files": [str(existent), str(ghost)]},
        )
        assert ready_state.get("generated_files") == [str(existent)]

    def test_relative_path_resolved_against_cwd(self, orchestrator, ready_state):
        # isfile 只对 abspath 解析后的 cwd 相对路径返回 True
        with patch("os.path.isfile", side_effect=lambda p: p.endswith("ok.md")):
            _run_finalizer(
                orchestrator,
                ready_state,
                {"final_answer": "a", "generated_files": ["ok.md", "nope.md"]},
            )
        files = ready_state.get("generated_files")
        assert len(files) == 1
        assert files[0].endswith("/ok.md")

    def test_quoted_and_whitespace_padded_path_is_cleaned(self, orchestrator, ready_state, tmp_path):
        f = tmp_path / "quoted.md"
        f.write_text("x", encoding="utf-8")
        _run_finalizer(
            orchestrator,
            ready_state,
            {"final_answer": "a", "generated_files": [f'  "{f}"  ']},
        )
        assert ready_state.get("generated_files") == [str(f)]

    def test_merges_with_a1_files_without_duplicates(self, orchestrator, ready_state, tmp_path):
        a1_file = tmp_path / "from_tool.txt"
        a1_file.write_text("x", encoding="utf-8")
        a2_file = tmp_path / "from_finalizer.txt"
        a2_file.write_text("x", encoding="utf-8")
        ready_state["generated_files"] = [str(a1_file)]
        _run_finalizer(
            orchestrator,
            ready_state,
            {"final_answer": "a", "generated_files": [str(a1_file), str(a2_file)]},
        )
        assert ready_state.get("generated_files") == [str(a1_file), str(a2_file)]

    def test_non_list_generated_files_is_tolerated(self, orchestrator, ready_state):
        _run_finalizer(
            orchestrator,
            ready_state,
            {"final_answer": "a", "generated_files": "output/报告.md"},
        )
        assert ready_state.get("generated_files") == []
        assert ready_state["final_result"] == "a"

    def test_missing_generated_files_key_is_ok(self, orchestrator, ready_state):
        _run_finalizer(orchestrator, ready_state, {"final_answer": "a"})
        assert ready_state.get("generated_files") == []


class TestA1ToolCollection:
    def test_write_file_format_extracted_and_validated(self, orchestrator, tmp_path):
        state = create_initial_state("x", "req_tool")
        real = tmp_path / "code.py"
        real.write_text("print(1)", encoding="utf-8")
        ghost = tmp_path / "ghost.py"
        orchestrator._collect_generated_files(
            state, "write_file",
            f"File written: {real} ({len('print(1)')} bytes)",
        )
        orchestrator._collect_generated_files(
            state, "write_file", f"File written: {ghost} (5 bytes)",
        )
        assert state.get("generated_files") == [str(real)]

    def test_non_whitelisted_tool_ignored(self, orchestrator):
        state = create_initial_state("x", "req_tool2")
        orchestrator._collect_generated_files(state, "run_script", ["/tmp/x.py"])
        orchestrator._collect_generated_files(state, "write_file", "unparseable")
        assert state.get("generated_files") == []
        assert "run_script" not in FILE_GENERATING_TOOLS


class TestFinalizerPromptContract:
    def test_prompt_contract_reports_paths_verbatim_from_tools(self):
        assert "generated_files" in USER_PROMPT_FINALIZER
        assert "原样照抄工具返回的路径" in USER_PROMPT_FINALIZER
        assert "禁止自行拼接目录前缀" in USER_PROMPT_FINALIZER
        assert "正确示例" in USER_PROMPT_FINALIZER
        assert "错误示例" in USER_PROMPT_FINALIZER

    def test_prompt_contract_no_longer_mandates_absolute_paths(self):
        assert "必须把每个文件的**绝对路径**填入" not in USER_PROMPT_FINALIZER

    def test_prompt_contract_workspace_prefix_only_in_counterexample(self):
        assert "/workspace/output/黄金价格趋势报告.md" not in USER_PROMPT_FINALIZER.split(
            "错误示例", 1
        )[0]
        assert "/workspace/output/黄金价格趋势报告.md" in USER_PROMPT_FINALIZER.split(
            "错误示例", 1
        )[1]
        # 正确示例段必须用相对路径
        assert "output/黄金价格趋势报告.md" in USER_PROMPT_FINALIZER.split(
            "## JSON Response Format", 1
        )[1].split("正确示例", 1)[0]

    def test_invented_prefix_dropped_relative_resolved_against_cwd(
        self, orchestrator, ready_state
    ):
        with patch(
            "os.path.isfile",
            side_effect=lambda p: p == os.path.abspath("ok.md"),
        ):
            _run_finalizer(
                orchestrator,
                ready_state,
                {
                    "final_answer": "a",
                    "generated_files": [
                        "/workspace/output/黄金价格趋势报告.md",
                        "ok.md",
                        "/workspace/output/ok.md",
                    ],
                },
            )
        assert ready_state.get("generated_files") == [os.path.abspath("ok.md")]


class TestPlanningNeedFinalizerContract:
    def test_user_prompt_requires_finalizer_when_files_generated(self):
        assert "need_finalizer" in USER_PROMPT_TASK_PLANNING
        assert "必须设为 true" in USER_PROMPT_TASK_PLANNING
        assert "write_file / save_code / create_ppt / image_download" in USER_PROMPT_TASK_PLANNING
        assert "设 false 会导致生成的文件无法被系统收集与下发" in USER_PROMPT_TASK_PLANNING

    def test_guidelines_require_finalizer_when_files_generated(self):
        for guidelines in (PLANNING_GUIDELINES_FORCED, PLANNING_GUIDELINES_AUTO):
            assert "finalizer 判定" in guidelines
            assert "必须为 true" in guidelines
            assert "write_file / save_code / create_ppt / image_download" in guidelines