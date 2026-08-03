"""
公共提示词 — 所有 system/user prompt 共享的规则与契约。

- ASK_USER_RULES: 提问决策规则（经 {ask_user_rules} 占位符注入所有
  system prompt；finalizer 无工具注入，不包含）
- COMMON_JSON_CONTRACT: 公共 JSON 输出契约（自 json_contract.py 迁入）
"""

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
