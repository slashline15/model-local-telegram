from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError, ToolNotFoundError
from tools.registry import ToolRegistry, ToolSpec


def _async_handler(echo_value: str) -> object:
    async def _h(value: str) -> dict[str, str]:
        return {"echo": value, "fixed": echo_value}
    return _h


def test_to_ollama_format() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="x", description="desc",
            parameters={"type": "object", "properties": {}},
            handler=_async_handler("z"),
        )
    )
    spec = reg.specs_for_ollama()[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "x"
    assert "parameters" in spec["function"]


def test_register_sync_handler_raises() -> None:
    reg = ToolRegistry()
    def sync_h(x: str) -> str:
        return x
    with pytest.raises(ToolExecutionError):
        reg.register(
            ToolSpec(
                name="bad", description="d",
                parameters={"type": "object"}, handler=sync_h,  # type: ignore[arg-type]
            )
        )


@pytest.mark.asyncio
async def test_dispatch_unknown_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await reg.dispatch("nope", {})


@pytest.mark.asyncio
async def test_dispatch_routes_arguments() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="echo", description="d",
            parameters={"type": "object"},
            handler=_async_handler("fix"),
        )
    )
    out = await reg.dispatch("echo", {"value": "hi"})
    assert out == {"echo": "hi", "fixed": "fix"}


@pytest.mark.asyncio
async def test_dispatch_invalid_args_wraps_error() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="echo", description="d",
            parameters={"type": "object"},
            handler=_async_handler("fix"),
        )
    )
    with pytest.raises(ToolExecutionError):
        await reg.dispatch("echo", {"wrong_kw": "x"})
