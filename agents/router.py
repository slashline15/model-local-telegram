from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.logger import get_logger

log = get_logger(__name__)


class AgentRoute(str, Enum):
    """Caminhos possíveis ao processar uma mensagem."""

    CHAT = "chat"
    CODE = "code"
    SEARCH = "search"
    SUMMARIZE = "summarize"


@dataclass(slots=True, frozen=True)
class RoutingDecision:
    route: AgentRoute
    reason: str


class AgentRouter:
    """Roteador determinístico (placeholder).

    Versão futura pode usar regras + LLM-as-router. Por ora, mapeia tags conhecidas
    para uma rota; default é CHAT.
    """

    _TAG_TO_ROUTE: dict[str, AgentRoute] = {
        "codigo": AgentRoute.CODE,
        "code": AgentRoute.CODE,
        "erro_tecnico": AgentRoute.CODE,
        "busca": AgentRoute.SEARCH,
        "pesquisa": AgentRoute.SEARCH,
        "web": AgentRoute.SEARCH,
        "pedido_resumo": AgentRoute.SUMMARIZE,
        "resumo": AgentRoute.SUMMARIZE,
    }

    def decide(self, tags: list[str]) -> RoutingDecision:
        for t in tags:
            route = self._TAG_TO_ROUTE.get(t)
            if route is not None:
                return RoutingDecision(route=route, reason=f"tag={t}")
        return RoutingDecision(route=AgentRoute.CHAT, reason="default")
