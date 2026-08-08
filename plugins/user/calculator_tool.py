"""
calculator_tool.py - 安全计算器示例外部插件

演示如何通过 AST 安全求值实现一个无沙箱风险的计算器工具。
继承 BaseTool → 用 ast.parse + 白名单运算符 → 安全执行数学表达式。
"""

import ast
import operator

from HelixCore.tools.base import BaseTool
from modules.utils.logger import log_tool_call


class CalculatorTool(BaseTool):
    """安全地执行数学表达式计算。"""

    name = "calculator"
    description = "计算数学表达式。支持加减乘除、幂运算、括号。如 '2*(3+4)' 返回 '14'"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 '2 + 3 * 4'、'(100 - 32) * 5/9'"
            }
        },
        "required": ["expression"]
    }

    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
    }

    def _safe_eval(self, node):
        """递归安全地求值 AST 节点，只允许白名单中的运算。"""
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
