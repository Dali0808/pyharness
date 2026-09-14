from __future__ import annotations

import ast
import inspect
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.schemas import ToolCallPart, ToolDefinition, ToolResultMessage

ToolHandler = Callable[[BaseModel], str | Awaitable[str]]


@dataclass(frozen=True, slots=True)
class AgentTool:
    definition: ToolDefinition
    args_model: type[BaseModel]
    handler: ToolHandler

    @classmethod
    def from_handler(
        cls,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: ToolHandler,
    ) -> "AgentTool":
        return cls(
            definition=ToolDefinition(
                name=name,
                description=description,
                parameters=args_model.model_json_schema(),
            ),
            args_model=args_model,
            handler=handler,
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def definitions(self) -> list[ToolDefinition]:
        return [
            tool.definition.model_copy(deep=True)
            for tool in self._tools.values()
        ]

    async def execute(self, call: ToolCallPart) -> ToolResultMessage:
        tool = self._tools.get(call.name)
        if tool is None:
            return self._error(call, "unknown_tool", call.name)

        try:
            raw_arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError as exc:
            return self._error(call, "invalid_json", exc.msg)

        if not isinstance(raw_arguments, dict):
            return self._error(
                call,
                "invalid_arguments",
                "tool arguments must be a JSON object",
            )

        try:
            arguments = tool.args_model.model_validate(raw_arguments)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors(include_url=False)
            )
            return self._error(call, "invalid_arguments", details)

        try:
            result = tool.handler(arguments)
            if inspect.isawaitable(result):
                result = await result

            if not isinstance(result, str):
                raise TypeError("tool handler must return a string")
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            return self._error(call, "tool_execution_failed", detail)

        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=result,
        )

    @staticmethod
    def _error(
        call: ToolCallPart,
        code: str,
        detail: str,
    ) -> ToolResultMessage:
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=f"{code}: {detail}",
            is_error=True,
        )


class CalculatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expression: str = Field(min_length=1, max_length=200)


def create_calculator_tool() -> AgentTool:
    return AgentTool.from_handler(
        name="calculator",
        description="Evaluate a restricted arithmetic expression.",
        args_model=CalculatorArgs,
        handler=_calculate,
    )


def _calculate(arguments: BaseModel) -> str:
    if not isinstance(arguments, CalculatorArgs):
        raise TypeError("calculator received unexpected arguments")

    try:
        tree = ast.parse(arguments.expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("invalid arithmetic expression") from exc

    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("expression is too complex")

    return str(_evaluate_node(tree.body))


def _evaluate_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        return _ensure_finite(value)

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return _ensure_finite(+operand)
        if isinstance(node.op, ast.USub):
            return _ensure_finite(-operand)
        raise ValueError("unsupported unary operator")

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)

        try:
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            elif isinstance(node.op, ast.FloorDiv):
                result = left // right
            elif isinstance(node.op, ast.Mod):
                result = left % right
            else:
                raise ValueError("unsupported binary operator")
        except ZeroDivisionError as exc:
            raise ValueError("division by zero") from exc

        return _ensure_finite(result)

    raise ValueError("only arithmetic expressions are allowed")


def _ensure_finite(value: int | float) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("result must be finite")
    return value