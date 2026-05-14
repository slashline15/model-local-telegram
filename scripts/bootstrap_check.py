"""Verificação de ambiente antes de iniciar o bot.

Roda um checklist de dependências, serviços e conectividade,
retornando um relatório estruturado que pode ser enviado ao
BOOTSTRAP_SUPERADMIN_TELEGRAM_ID e/ou logado.

Como rodar standalone:
    python -m scripts.bootstrap_check

Uso interno (main.py):
    from scripts.bootstrap_check import run_checks, format_report
    report = asyncio.run(run_checks(settings))
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from core.config import Settings, get_settings
from core.logger import get_logger

log = get_logger(__name__)

# Checks que contêm dados técnicos (paths, tabelas, modelos, OS).
# Não entram no relatório público enviado ao Telegram.
_SENSITIVE_CHECKS: set[str] = {
    "environment",
    "dependencies",
    "sqlite",
    "faiss",
    "pytest",
}


@dataclass
class CheckResult:
    name: str
    status: str          # ok | warning | error
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    critical: bool = False


async def _http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    """GET simples via httpx (usa Windows cert store se disponível)."""
    import ssl
    kwargs: dict[str, Any] = {"timeout": timeout}
    if sys.platform == "win32":
        kwargs["verify"] = ssl.create_default_context()
    async with httpx.AsyncClient(**kwargs) as client:
        resp = await client.get(url)
        return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Checks individuais
# ---------------------------------------------------------------------------

async def check_environment() -> CheckResult:
    """OS e arquitetura — informativo, não crítico."""
    return CheckResult(
        name="environment",
        status="ok",
        message=f"{platform.system()} {platform.release()} ({platform.machine()})",
        details={
            "os": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
    )


async def check_python_version() -> CheckResult:
    """Info — versão do interpretador."""
    v = sys.version_info
    return CheckResult(
        name="python_version",
        status="ok",
        message=f"Python {v.major}.{v.minor}.{v.micro}",
    )


async def check_dependencies() -> CheckResult:
    """Tenta importar os módulos críticos."""
    required = [
        "aiosqlite",
        "httpx",
        "faiss",
        "pydantic",
        "pydantic_settings",
        "telegram",
        "numpy",
    ]
    optional = [
        "openai",
        "aiohttp",
    ]
    missing_required: list[str] = []
    missing_optional: list[str] = []

    for mod in required:
        try:
            __import__(mod)
        except Exception:
            missing_required.append(mod)

    for mod in optional:
        try:
            __import__(mod)
        except Exception:
            missing_optional.append(mod)

    if missing_required:
        return CheckResult(
            name="dependencies",
            status="error",
            message=f"Faltam dependências obrigatórias: {', '.join(missing_required)}",
            details={"missing_required": missing_required, "missing_optional": missing_optional},
            critical=True,
        )

    msg = f"{len(required)} obrigatórias OK"
    if missing_optional:
        msg += f"; opcionais faltando: {', '.join(missing_optional)}"
    return CheckResult(
        name="dependencies",
        status="warning" if missing_optional else "ok",
        message=msg,
        details={"missing_optional": missing_optional},
    )


async def check_env(s: Settings) -> CheckResult:
    """Verifica variáveis críticas do .env."""
    errors: list[str] = []
    warnings: list[str] = []

    if not s.telegram_bot_token:
        errors.append("TELEGRAM_BOT_TOKEN ausente")
    if not s.ollama_host:
        errors.append("OLLAMA_HOST ausente")
    if not s.bootstrap_superadmin_telegram_id:
        warnings.append("BOOTSTRAP_SUPERADMIN_TELEGRAM_ID não definido (sem auto-superadmin)")
    if not s.openai_api_key:
        warnings.append("OPENAI_API_KEY ausente (transcrição e fallback desligados)")

    if errors:
        return CheckResult(
            name="env_critical",
            status="error",
            message="; ".join(errors),
            details={"errors": errors, "warnings": warnings},
            critical=True,
        )

    msg = "Tokens principais OK"
    if warnings:
        msg += f"; avisos: {', '.join(warnings)}"
    return CheckResult(
        name="env_critical",
        status="warning" if warnings else "ok",
        message=msg,
        details={"warnings": warnings},
    )


async def check_ollama(s: Settings) -> CheckResult:
    """Ping no Ollama + presença dos modelos locais configurados.

    Modelos "cloud" (nome contendo '-cloud' ou URL remota) são ignorados
    nesta verificação — eles não precisam estar no Ollama local.
    """
    url = f"{s.ollama_host}/api/tags"
    try:
        code, body = await _http_get(url, timeout=15.0)
    except Exception as exc:
        return CheckResult(
            name="ollama",
            status="error",
            message=f"Ollama não responde em {url}: {exc}",
            critical=True,
        )

    if code != 200:
        return CheckResult(
            name="ollama",
            status="error",
            message=f"Ollama respondeu HTTP {code} em {url}",
            critical=True,
        )

    try:
        data = json.loads(body)
        available = {m.get("name", m.get("model", "")) for m in data.get("models", [])}
    except Exception:
        available = set()

    def _is_local_model(name: str) -> bool:
        """Modelos cloud/OpenAI não precisam estar no Ollama local."""
        if not name:
            return False
        lower = name.lower()
        if "-cloud" in lower:
            return False
        if lower.startswith("http://") or lower.startswith("https://"):
            return False
        return True

    all_expected = [s.ollama_default_model, s.ollama_embedding_model, *s.chat_fallback_models]
    local_expected = [m for m in all_expected if _is_local_model(m)]
    missing = [m for m in local_expected if m and m not in available]

    if missing:
        return CheckResult(
            name="ollama",
            status="warning",
            message=f"Ollama OK, mas modelos locais faltando: {', '.join(missing)}",
            details={"available": sorted(available), "missing": missing, "ignored_cloud": [m for m in all_expected if not _is_local_model(m)]},
        )

    return CheckResult(
        name="ollama",
        status="ok",
        message=f"Ollama OK ({len(available)} modelos)",
        details={"available": sorted(available)},
    )


async def check_sqlite(s: Settings) -> CheckResult:
    """Verifica se consegue abrir o banco e conta tabelas."""
    import aiosqlite

    db = s.sqlite_path
    if not db.parent.exists():
        return CheckResult(
            name="sqlite",
            status="error",
            message=f"Pasta do banco não existe: {db.parent}",
            critical=True,
        )

    try:
        async with aiosqlite.connect(db) as conn:
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            rows = await cur.fetchall()
            tables = [r[0] for r in rows]
    except Exception as exc:
        return CheckResult(
            name="sqlite",
            status="error",
            message=f"Não conseguiu abrir SQLite: {exc}",
            critical=True,
        )

    return CheckResult(
        name="sqlite",
        status="ok",
        message=f"SQLite OK ({len(tables)} tabelas)",
        details={"tables": tables},
    )


async def check_faiss(s: Settings) -> CheckResult:
    """Tenta carregar o índice FAISS existente."""
    try:
        import faiss
    except Exception as exc:
        return CheckResult(
            name="faiss",
            status="error",
            message=f"FAISS não instalado: {exc}",
            critical=True,
        )

    idx_path = s.faiss_index_path
    if not idx_path.exists():
        return CheckResult(
            name="faiss",
            status="warning",
            message=f"Índice FAISS não encontrado em {idx_path} (será criado no startup)",
        )

    try:
        index = faiss.read_index(str(idx_path))
        ntotal = index.ntotal
        dim = index.d
    except Exception as exc:
        return CheckResult(
            name="faiss",
            status="error",
            message=f"FAISS corrompido ou ilegível: {exc}",
            critical=True,
        )

    dim_ok = dim == s.embedding_dim
    status = "ok" if dim_ok else "warning"
    msg = f"FAISS OK ({ntotal} vetores, dim={dim})"
    if not dim_ok:
        msg += f" — dimensão diverge da config (esperado {s.embedding_dim})"

    return CheckResult(
        name="faiss",
        status=status,
        message=msg,
        details={"ntotal": ntotal, "dim": dim},
    )


async def check_ssl_telegram() -> CheckResult:
    """Confirma que consegue falar HTTPS com api.telegram.org."""
    try:
        code, _ = await _http_get("https://api.telegram.org", timeout=15.0)
    except Exception as exc:
        return CheckResult(
            name="ssl_telegram",
            status="error",
            message=f"SSL/API do Telegram falhou: {exc}",
            details={"error_type": type(exc).__name__},
            critical=True,
        )

    if code != 200 and code != 302:
        return CheckResult(
            name="ssl_telegram",
            status="warning",
            message=f"Telegram API respondeu HTTP {code}",
        )

    return CheckResult(
        name="ssl_telegram",
        status="ok",
        message="SSL/API do Telegram OK",
    )


async def check_pytest() -> CheckResult:
    """Roda pytest e conta pass/fail. Não crítico — só informativo."""
    import subprocess
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pytest", "--tb=no",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        text = stdout.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return CheckResult(
            name="pytest",
            status="warning",
            message="pytest estourou timeout (120s)",
        )
    except Exception as exc:
        return CheckResult(
            name="pytest",
            status="warning",
            message=f"Não conseguiu rodar pytest: {exc}",
        )

    # Procura por "X passed, Y failed, Z skipped" na última linha
    m = re.search(r"(\d+) passed", text)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", text)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", text)
    errors = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) collected", text)
    collected = int(m.group(1)) if m else None

    if not passed and not failed and not errors:
        # pytest pode ter rodado mas não encontrou testes ou output foi estranho
        if collected == 0:
            return CheckResult(
                name="pytest",
                status="warning",
                message="pytest não encontrou testes (0 collected)",
                details={"raw_tail": text[-300:]},
            )
        if "no tests ran" in text.lower():
            return CheckResult(
                name="pytest",
                status="warning",
                message="pytest: nenhum teste executado",
                details={"raw_tail": text[-300:]},
            )

    status = "warning" if (failed or errors) else "ok"
    msg = f"{passed} testes passaram"
    if failed:
        msg += f", {failed} falharam"
    if errors:
        msg += f", {errors} com erro de setup"
    if collected is not None:
        msg += f" ({collected} coletados)"

    return CheckResult(
        name="pytest",
        status=status,
        message=msg,
        details={"passed": passed, "failed": failed, "errors": errors, "collected": collected},
    )


# ---------------------------------------------------------------------------
# Orquestração e relatório
# ---------------------------------------------------------------------------

async def run_checks(settings: Settings | None = None) -> list[CheckResult]:
    """Executa todos os checks em paralelo (onde possível) e retorna resultados."""
    s = settings or get_settings()
    coros = [
        check_environment(),
        check_python_version(),
        check_dependencies(),
        check_env(s),
        check_ollama(s),
        check_sqlite(s),
        check_faiss(s),
        check_ssl_telegram(),
        check_pytest(),
    ]
    return await asyncio.gather(*coros)


def format_report(results: list[CheckResult]) -> str:
    """Relatório completo (log interno) — inclui todos os checks."""
    lines: list[str] = ["Bootstrap Check"]
    for r in results:
        icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(r.status, "❓")
        lines.append(f"{icon} {r.name} — {r.message}")
    return "\n".join(lines)


def format_report_public(results: list[CheckResult]) -> str:
    """Relatório enxuto para envio ao Telegram — omite dados técnicos sensíveis.

    Inclui apenas: python_version, env_critical, ollama (sem lista), ssl_telegram.
    """
    lines: list[str] = ["🩺 *Check de ambiente*"]
    for r in results:
        if r.name in _SENSITIVE_CHECKS:
            continue
        icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(r.status, "❓")
        lines.append(f"{icon} *{r.name}* — {r.message}")
    return "\n".join(lines)


def has_critical_failure(results: list[CheckResult]) -> bool:
    return any(r.critical and r.status == "error" for r in results)


async def send_report_to_telegram(
    token: str,
    chat_id: int | str,
    text: str,
) -> bool:
    """Envia relatório via HTTP direto (não depende do bot rodando)."""
    import ssl
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        # Sem parse_mode — o relatório contém paths e caracteres que quebram Markdown.
    }
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["verify"] = ssl.create_default_context()
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(url, json=payload, timeout=15.0)
            if resp.status_code != 200:
                log.warning("Falha ao enviar relatório: HTTP %s — %s", resp.status_code, resp.text[:200])
                return False
            return True
    except Exception as exc:
        log.warning("Falha ao enviar relatório: %s", exc)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> int:
    t0 = time.monotonic()
    results = await run_checks()
    dt = time.monotonic() - t0

    report = format_report(results)
    report += f"\n\n⏱ *{dt:.1f}s*"

    print(report)
    print()

    if has_critical_failure(results):
        print("❌ Falhas críticas detectadas — abortar startup.")
        return 1

    print("✅ Ambiente OK.")
    return 0


if __name__ == "__main__":
    # Windows: força UTF-8 no stdout para não quebrar com emojis.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
