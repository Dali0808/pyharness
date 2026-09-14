import pytest
import json
from pydantic import BaseModel

from agent.tools import AgentTool, ToolRegistry, create_calculator_tool
from ai.schemas import ToolCallPart


def make_call(
    name: str,
    arguments_json: str,
    call_id: str = "call-1",
) -> ToolCallPart:
    return ToolCallPart(
        id=call_id,
        name=name,
        arguments_json=arguments_json,
    )


def assert_error(
    result,
    call: ToolCallPart,
    code: str,
) -> None:
    assert result.is_error is True
    assert result.tool_call_id == call.id
    assert result.tool_name == call.name
    assert result.content.startswith(f"{code}:")


@pytest.fixture
def registry() -> ToolRegistry:
    tool_registry = ToolRegistry()
    tool_registry.register(create_calculator_tool())
    return tool_registry


@pytest.mark.asyncio
async def test_calculator_returns_result_for_valid_expression(
    registry: ToolRegistry,
) -> None:
    call = make_call("calculator", '{"expression":"21 * 2"}')

    result = await registry.execute(call)

    assert result.is_error is False
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "calculator"
    assert result.content == "42"


@pytest.mark.asyncio
async def test_invalid_json_returns_tool_error(
    registry: ToolRegistry,
) -> None:
    call = make_call("calculator", '{"expression":')

    result = await registry.execute(call)

    assert_error(result, call, "invalid_json")


@pytest.mark.asyncio
async def test_unknown_tool_returns_tool_error() -> None:
    registry = ToolRegistry()
    call = make_call("missing_tool", "{}")

    result = await registry.execute(call)

    assert_error(result, call, "unknown_tool")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments_json",
    [
        "{}",
        '{"expression": 42}',
        '{"expression":"1 + 1","unexpected":true}',
        "[]",
    ],
)
async def test_invalid_arguments_return_tool_error(
    registry: ToolRegistry,
    arguments_json: str,
) -> None:
    call = make_call("calculator", arguments_json)

    result = await registry.execute(call)

    assert_error(result, call, "invalid_arguments")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "x + 1",
        '__import__("os")',
        "2 ** 8",
    ],
)
async def test_unsafe_expression_returns_tool_error(
    registry: ToolRegistry,
    expression: str,
) -> None:
    call = make_call(
        "calculator",
        json.dumps({"expression": expression}),
    )

    result = await registry.execute(call)

    assert_error(result, call, "tool_execution_failed")


class ExplodingArgs(BaseModel):
    value: str


def explode(_: BaseModel) -> str:
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_handler_exception_returns_tool_error() -> None:
    registry = ToolRegistry()
    registry.register(
        AgentTool.from_handler(
            name="explode",
            description="Always fails.",
            args_model=ExplodingArgs,
            handler=explode,
        )
    )
    call = make_call("explode", '{"value":"test"}')

    result = await registry.execute(call)

    assert_error(result, call, "tool_execution_failed")
    assert "boom" in result.content


def test_duplicate_tool_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(create_calculator_tool())

    with pytest.raises(ValueError, match="Tool already registered: calculator"):
        registry.register(create_calculator_tool())


def test_registry_exports_generated_tool_schema(
    registry: ToolRegistry,
) -> None:
    definition = registry.definitions()[0]

    assert definition.name == "calculator"
    assert definition.parameters["properties"]["expression"]["type"] == "string"
    assert definition.parameters["required"] == ["expression"]

    definition.parameters["properties"]["expression"]["type"] = "number"

    exported_again = registry.definitions()[0]
    assert exported_again.parameters["properties"]["expression"]["type"] == "string"