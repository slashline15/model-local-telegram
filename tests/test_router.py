from __future__ import annotations

from agents.router import AgentRoute, AgentRouter


def test_route_chat_default() -> None:
    r = AgentRouter()
    d = r.decide([])
    assert d.route == AgentRoute.CHAT


def test_route_code_via_tag() -> None:
    r = AgentRouter()
    d = r.decide(["codigo", "outro"])
    assert d.route == AgentRoute.CODE
    assert "codigo" in d.reason


def test_route_search_via_tag() -> None:
    r = AgentRouter()
    d = r.decide(["pesquisa"])
    assert d.route == AgentRoute.SEARCH


def test_unknown_tags_fall_back_to_chat() -> None:
    r = AgentRouter()
    d = r.decide(["aleatorio", "blarg"])
    assert d.route == AgentRoute.CHAT
    assert d.reason == "default"
