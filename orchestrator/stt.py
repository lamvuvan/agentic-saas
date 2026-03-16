"""Speech-to-text node — faster-whisper large-v3 for Vietnamese audio transcription."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "/models/whisper")

# Module-level singleton — initialized once at lifespan startup
_whisper_model = None


def _load_model() -> Any:
    """Load WhisperModel synchronously (runs in executor)."""
    from faster_whisper import WhisperModel  # noqa: PLC0415

    model = WhisperModel(
        "large-v3",
        compute_type="int8",
        download_root=_WHISPER_MODEL_DIR,
    )
    logger.info("whisper_model_loaded", extra={"model": "large-v3", "compute_type": "int8"})
    return model


async def initialize() -> None:
    """Load the Whisper model in a thread executor to avoid blocking the event loop."""
    global _whisper_model  # noqa: PLW0603
    loop = asyncio.get_event_loop()
    try:
        _whisper_model = await loop.run_in_executor(None, _load_model)
    except Exception as exc:  # pragma: no cover
        logger.warning("whisper_model_load_failed", extra={"error": str(exc)})
        _whisper_model = None


class WhisperSTT:
    """Wrapper around faster-whisper for async Vietnamese transcription."""

    def __init__(self, model=None) -> None:
        # Accept explicit model for testing; fall back to module-level singleton
        self._model = model or _whisper_model

    def _transcribe_sync(self, audio_path: str) -> str:
        """Synchronous transcription — called via run_in_executor."""
        if self._model is None:
            raise RuntimeError("Whisper model not initialized; call stt.initialize() at startup")

        segments, _info = self._model.transcribe(
            audio_path,
            language="vi",
            vad_filter=True,
            beam_size=5,
        )
        text = " ".join(seg.text for seg in segments).strip()
        return text

    async def transcribe_audio(self, audio_path: str | Path) -> str:
        """
        Transcribe audio file asynchronously.

        Runs faster-whisper in a thread executor so the event loop is never blocked.
        Returns the full transcription as a stripped string.
        """
        path = str(audio_path)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._transcribe_sync, path)
        logger.info(
            "stt_transcribed",
            extra={"audio_path": path, "text_length": len(result)},
        )
        return result
