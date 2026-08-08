"""
HelixCore.ports — 注入端口定义（Protocol / 数据类）。

端口是 HelixCore 与 Host 之间的唯一契约面：
- llm:      LLMBackend Protocol（LLM 调用）
- events:   EventSink Protocol（前端事件输出）
- intents:  IntentProvider Protocol（意图配置读取）
"""
