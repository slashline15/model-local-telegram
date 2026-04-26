from __future__ import annotations

from pathlib import Path

import aiohttp

from core.exceptions import ConfigError, TranscriptionError
from core.logger import get_logger

log = get_logger(__name__)


class WhisperTranscriber:
    """Cliente HTTP direto para o endpoint /v1/audio/transcriptions da OpenAI.

    Não usa o SDK oficial — apenas aiohttp + multipart/form-data.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "whisper-1",
        timeout_s: int = 120,
    ) -> None:
        if not api_key:
            raise ConfigError("OPENAI_API_KEY ausente — necessária para transcrição.")
        self._api_key: str = api_key
        self._endpoint: str = f"{api_base.rstrip('/')}/audio/transcriptions"
        self._model: str = model
        self._timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=timeout_s)

    async def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        prompt: str | None = None,
    ) -> str:
        if not audio_path.exists():
            raise TranscriptionError(f"Arquivo de áudio não encontrado: {audio_path}")

        headers = {"Authorization": f"Bearer {self._api_key}"}

        form = aiohttp.FormData()
        form.add_field("model", self._model)
        form.add_field("response_format", "json")
        if language:
            form.add_field("language", language)
        if prompt:
            form.add_field("prompt", prompt)

        with audio_path.open("rb") as fh:
            form.add_field(
                name="file",
                value=fh,
                filename=audio_path.name,
                content_type="application/octet-stream",
            )

            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                try:
                    async with session.post(
                        self._endpoint, headers=headers, data=form
                    ) as resp:
                        body = await resp.text()
                        if resp.status >= 400:
                            log.error("Whisper falhou %s: %s", resp.status, body)
                            raise TranscriptionError(
                                f"Whisper retornou {resp.status}: {body[:500]}"
                            )
                        data = await resp.json(content_type=None)
                except aiohttp.ClientError as exc:
                    raise TranscriptionError(f"Erro de rede no Whisper: {exc}") from exc

        text = data.get("text", "") if isinstance(data, dict) else ""
        if not text:
            raise TranscriptionError("Whisper retornou texto vazio.")
        return text.strip()
