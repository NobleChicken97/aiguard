import ast
import operator
from tools.base import Tool, ToolResult


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value}")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_type = type(node.op)
        if op_type not in _OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        return _OPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op_type = type(node.op)
        if op_type not in _OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        return _OPS[op_type](operand)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def safe_eval(expression):
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree)


class CalculatorTool(Tool):
    def get_name(self):
        return "calculator"

    def get_description(self):
        return "Evaluate a mathematical expression. Supports +, -, *, /, //, %, **, and parentheses. Example: '2 + 3 * 4'."

    def get_input_schema(self):
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to evaluate, e.g. '(15 + 27) * 3 / 9'",
                }
            },
            "required": ["expression"],
        }

    def execute(self, expression=None, **kwargs):
        if not expression:
            return ToolResult(
                status="failed",
                output="Error: 'expression' parameter is required.",
            )
        try:
            result = safe_eval(expression)
            return ToolResult(status="success", output=str(result))
        except Exception as e:
            return ToolResult(
                status="failed",
                output=f"Error evaluating expression '{expression}': {e}",
            )
