"""
modules.host — Host 侧适配层（通过 HelixCore 端口注入）。

与 HelixCore（零依赖核心）的边界由 HelixCore.ports 定义：
- ai_engine_backend.py: LLMBackend 实现（ai_engine 适配，原 llm_client.py）
- event_sink.py:        EventSink 实现（SSE 事件总线，P2 迁入）
- intent_store.py:      IntentProvider 实现（Helix.json 意图，P3 迁入）
- llm_events / status_events / routes / composition: 后续阶段迁入
"""
