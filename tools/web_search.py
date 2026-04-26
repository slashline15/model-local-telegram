from __future__ import annotations

from core.logger import get_logger
from tools.registry import ToolRegistry, ToolSpec

log = get_logger(__name__)


async def _web_search_handler(query: str, max_results: int = 3) -> dict[str, object]:
    """Mock de busca web — substituir por integração real (SerpAPI, Tavily, etc)."""
    log.info("[mock web_search] query=%r max_results=%d", query, max_results)
    fake_hits = [
        {
            "title": f"Resultado {i + 1} para '{query}'",
            "url": f"https://example.com/{i + 1}",
            "snippet": f"Trecho mockado #{i + 1} relacionado a {query}.",
        }
        for i in range(max(1, min(max_results, 5)))
    ]
    return {"query": query, "results": fake_hits}


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="web_search",
            description="Busca informações na web. Use para fatos atuais ou desconhecidos.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Termo de busca."},
                    "max_results": {
                        "type": "integer",
                        "description": "Máximo de resultados (1..5).",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
            handler=_web_search_handler,
        )
    )
