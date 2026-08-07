# Helix 外部插件编写指南

## 概述

`plugins/user/` 是用户自定义工具插件目录。放在该目录下的 `.py` 文件会被 `ToolRegistry` 在启动时**自动发现并注册**，无需修改任何框架代码。

插件在系统中的来源标记为 `"外部插件"`，与内置插件（`"内部插件"`）和 MCP 工具（`"MCP"`）区分开来。

## 快速开始

1. 在 `plugins/user/` 下创建一个 `.py` 文件（文件名不能以 `_` 开头）
2. 定义一个继承 `BaseTool` 的类
3. 重启 Helix，你的工具就会自动注册

就这么简单，不需要额外配置。

## BaseTool 接口

```python
from modules.agents.tool_base import BaseTool

class MyTool(BaseTool):
    """工具的简短说明。"""

    name = "my_tool"                          # 唯一标识符，LLM 通过它调用工具
    description = "这个工具做什么"              # LLM 看到的描述，写清楚
    intents = ["generic"]                     # 绑定的意图列表（见下方说明）
    parameters = {                             # JSON Schema，定义 LLM 传入的参数
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词"
            }
        },
        "required": ["query"]
    }

    def execute(self, query: str = "", **kwargs) -> str:
        """执行工具逻辑，返回字符串结果给 LLM。"""
        return f"结果: {query}"
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `str` | 是 | 工具唯一 ID，LLM 用它来决定调用哪个工具。全小写+下划线 |
| `description` | `str` | 是 | 给 LLM 看的说明，要清晰准确，它直接影响调用质量 |
| `intents` | `list` | 是 | 该工具支持哪些意图（见下表）。设为 `["*"]` 表示所有意图可用 |
| `parameters` | `dict` | 是 | JSON Schema 格式的参数定义，LLM 按此格式传参 |
| `execute()` | method | 是 | 核心逻辑，接收参数，返回 `str` 给 LLM |

## 意图（Intents）说明

意图决定工具在什么场景下被 LLM 看到。常见意图：

| 意图 ID | 用途 |
|---------|------|
| `generic` | 通用任务（一般问题与事务，必要时搜索） |
| `ppt` | PPT 生成（Helix.json 中注册的示例意图） |
| `coding` | 代码生成与执行（Helix.json 中注册的示例意图） |
| `*` | 所有意图均可见（万能工具） |

你也可以自定义意图，只需在 `Helix.json` 的 `intents` 配置中注册即可（含 planning/node/finalizer 各阶段提示词）。

## execute() 规范

```python
def execute(self, param1: str = "", param2: int = 0, **kwargs) -> str:
    """
    规范：
    - 所有参数使用关键字参数并提供默认值
    - 返回值必须是 str（LLM 只接收文本）
    - 异常应被 catch 并返回错误描述字符串，不要让异常逃逸
    """
    try:
        # 你的逻辑
        return "成功的结果"
    except Exception as e:
        return f"工具执行失败: {e}"
```

## 常用导入

```python
from modules.agents.tool_base import BaseTool          # 必须
from modules.utils.logger import log_tool_call          # 可选：记录工具调用
from modules.utils.file_ops import FileOps               # 可选：文件操作封装
```

## 完整示例

### 示例 1：天气查询工具

```python
"""
weather_tool.py - 天气查询示例插件
"""

import json
import urllib.request

from modules.agents.tool_base import BaseTool
from modules.utils.logger import log_tool_call


class WeatherTool(BaseTool):
    """查询指定城市的天气信息。"""

    name = "weather"
    description = "查询指定城市的当前天气。返回温度、天气状况、风力等信息。"
    intents = ["generic", "*"]
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，如 '北京'、'Tokyo'、'New York'"
            }
        },
        "required": ["city"]
    }

    def execute(self, city: str = "", **kwargs) -> str:
        log_tool_call(f"weather(city='{city}')")
        try:
            # 示例：使用 wttr.in 免费天气 API
            url = f"https://wttr.in/{city}?format=j1"
            req = urllib.request.Request(url, headers={"User-Agent": "Helix-Agent"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            current = data["current_condition"][0]
            result = (
                f"城市: {city}\n"
                f"温度: {current['temp_C']}°C\n"
                f"体感: {current['FeelsLikeC']}°C\n"
                f"天气: {current['weatherDesc'][0]['value']}\n"
                f"风速: {current['windspeedKmph']} km/h"
            )
            return result
        except Exception as e:
            return f"天气查询失败: {e}"
```

### 示例 2：计算器工具

```python
"""
calculator_tool.py - 简单计算器插件
"""

import ast
import operator

from modules.agents.tool_base import BaseTool
from modules.utils.logger import log_tool_call


class CalculatorTool(BaseTool):
    """安全地执行数学表达式计算。"""

    name = "calculator"
    description = "计算数学表达式。支持加减乘除、幂运算、括号。如 '2*(3+4)' → '14'"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 '2 + 3 * 4'、'sqrt(16)'、'(100 - 32) * 5/9'"
            }
        },
        "required": ["expression"]
    }

    # 允许的运算符（安全限制）
    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
    }

    def _safe_eval(self, node):
        """递归安全地求值 AST 节点。"""
        if isinstance(node, ast.Expression):
            return self._safe_eval(node.body)
        elif isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in self._operators:
                raise ValueError(f"不支持的运算符: {op_type.__name__}")
            left = self._safe_eval(node.left)
            right = self._safe_eval(node.right)
            return self._operators[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in self._operators:
                raise ValueError(f"不支持的一元运算符: {op_type.__name__}")
            return self._operators[op_type](self._safe_eval(node.operand))
        else:
            raise ValueError(f"不支持的表达式类型: {type(node).__name__}")

    def execute(self, expression: str = "", **kwargs) -> str:
        log_tool_call(f"calculator(expression='{expression}')")
        try:
            tree = ast.parse(expression, mode="eval")
            result = self._safe_eval(tree)
            return f"{expression} = {result}"
        except ZeroDivisionError:
            return "错误: 除数不能为零"
        except Exception as e:
            return f"计算失败: {e}"
```

## 目录结构参考

```
plugins/
├── __init__.py              # 已有
├── web_tools.py             # 内置：搜索
├── code_tools.py            # 内置：代码
├── shell_tools.py           # 内置：Shell
├── ppt_tools.py             # 内置：PPT
├── image_tools.py           # 内置：图片
├── mcp_tools.py             # 内置：MCP（单独注册，不走自动发现）
└── user/                    # ← 你的插件放这里
    ├── __init__.py
    ├── plugin.md            # 本文件
    ├── weather_tool.py      # 示例
    └── calculator_tool.py   # 示例
```

## 调试与排查

- **日志**：插件注册信息会打印在控制台日志中，搜索 `ToolRegistry` 即可看到加载结果
- **常见问题**：
  - 工具没出现？检查文件名是否以 `_` 开头（会被跳过）
  - 导入报错？确认依赖已安装，异常信息会打在日志中
  - 工具名冲突？同名工具会被覆盖，日志会有 warning
- **管理控制台**：`http://localhost:11556/` 可以查看已注册工具列表、启用/禁用状态
