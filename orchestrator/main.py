"""Orchestrator FastAPI application — port 8000."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi import Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.a2a.models import AgentCard, AgentSkill
from shared.auth_context import set_auth
from shared.logging_middleware import StructuredLoggingMiddleware

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_redis_pool: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_pool
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _redis_pool = aioredis.from_url(redis_url, decode_responses=True)
    app.state.redis = _redis_pool
    logger.info("orchestrator_startup", extra={"redis_url": redis_url.split("@")[-1]})

    # Initialize AsyncRedisSaver checkpointer (thread_id=session_id)
    from orchestrator.graph import setup_checkpointer  # noqa: PLC0415

    await setup_checkpointer(redis_url)

    # Pre-load Whisper model in background (non-blocking)
    try:
        from orchestrator.stt import initialize as stt_initialize  # noqa: PLC0415

        asyncio.create_task(stt_initialize(), name="whisper_model_load")
    except Exception:
        pass  # STT optional

    yield
    await _redis_pool.aclose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Orchestrator", version="1.0.0", lifespan=lifespan)
app.add_middleware(StructuredLoggingMiddleware, agent_id="orchestrator", agent_role="orchestrator")

# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------

_AGENT_CARD = AgentCard(
    name="Orchestrator",
    version="1.0.0",
    role="orchestrator",
    url=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000"),
    description="Classifies intent, plans execution, delegates to Domain Agents via A2A",
    skills=[
        AgentSkill(
            id="chat",
            description="Process natural language messages and route to Domain Agents",
            input_modes=["text", "audio"],
            output_modes=["text"],
        )
    ],
    slo={"p95_latency_ms": 3000, "availability_pct": 99.0},
)


@app.get("/.well-known/agent.json", tags=["meta"])
async def agent_card() -> dict[str, Any]:
    return _AGENT_CARD.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health(request: Request) -> dict[str, Any]:
    redis: aioredis.Redis = request.app.state.redis
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    status = "healthy" if redis_ok else "degraded"
    code = 200 if redis_ok else 503
    return JSONResponse(
        {"status": status, "dependencies": {"redis": "ok" if redis_ok else "error"}},
        status_code=code,
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent: str
    trace_id: str
    requires_input: bool = False
    metadata: dict[str, Any] | None = None


class VoiceResponse(BaseModel):
    session_id: str
    transcription: str
    reply: str
    intent: str
    trace_id: str
    requires_input: bool = False
    metadata: dict[str, Any] | None = None


_MAX_AUDIO_DURATION_S = 60


def _normalize_audio(input_path: str, output_path: str) -> float:
    """
    Convert audio to 16kHz mono WAV via ffmpeg.

    Returns duration in seconds.
    Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    # First pass: probe duration
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            input_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(probe.stdout.strip() or "0")

    # Second pass: transcode
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return duration


# ---------------------------------------------------------------------------
# POST /chat  (wired to LangGraph in Phase 4 / T030)
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    start = time.monotonic()

    # Auth context from Authorization header
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    tenant_id = request.headers.get("X-Tenant-Id", "default")
    set_auth(token, tenant_id)

    session_id = body.session_id or request.headers.get("X-Session-Id") or str(uuid.uuid4())
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())

    # Graph invocation wired in T030
    from orchestrator.graph import invoke_chat  # noqa: PLC0415

    result = await invoke_chat(
        message=body.message,
        session_id=session_id,
        trace_id=trace_id,
        redis=request.app.state.redis,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    return ChatResponse(
        session_id=session_id,
        reply=result["reply"],
        intent=result["intent"],
        trace_id=trace_id,
        requires_input=result.get("requires_input", False),
        metadata={"duration_ms": duration_ms, "model_used": result.get("model_used")},
    )


# ---------------------------------------------------------------------------
# POST /voice  (US6 — voice input via faster-whisper)
# ---------------------------------------------------------------------------


@app.post("/voice", response_model=VoiceResponse, tags=["voice"])
async def voice(
    request: Request,
    audio: UploadFile,
    session_id: str | None = Form(default=None),
) -> VoiceResponse:
    """
    Accept a multipart audio upload, transcribe via Whisper, and route through
    the same pipeline as POST /chat.

    Rejects audio longer than 60 seconds with 422 AUDIO_TOO_LONG.
    Returns VoiceResponse with transcription + chat reply fields.
    """
    start = time.monotonic()

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    tenant_id = request.headers.get("X-Tenant-Id", "default")
    set_auth(token, tenant_id)

    resolved_session_id = session_id or request.headers.get("X-Session-Id") or str(uuid.uuid4())
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())

    # Write uploaded file to a temp location
    suffix = Path(audio.filename or "audio.bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as raw_f:
        raw_path = raw_f.name
        content = await audio.read()
        raw_f.write(content)

    wav_path = raw_path + "_16k.wav"
    try:
        try:
            loop = asyncio.get_event_loop()
            duration_s = await loop.run_in_executor(None, _normalize_audio, raw_path, wav_path)
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=422, detail="AUDIO_PROCESSING_ERROR") from exc

        if duration_s > _MAX_AUDIO_DURATION_S:
            raise HTTPException(status_code=422, detail="AUDIO_TOO_LONG")

        # Transcribe
        from orchestrator.stt import WhisperSTT  # noqa: PLC0415

        stt = WhisperSTT()
        transcription_start = time.monotonic()
        transcription = await stt.transcribe_audio(wav_path)
        transcription_ms = int((time.monotonic() - transcription_start) * 1000)

        if not transcription:
            raise HTTPException(status_code=422, detail="TRANSCRIPTION_EMPTY")

        # Route through chat pipeline
        from orchestrator.graph import invoke_chat  # noqa: PLC0415

        result = await invoke_chat(
            message=transcription,
            session_id=resolved_session_id,
            trace_id=trace_id,
            redis=request.app.state.redis,
        )

    finally:
        for p in (raw_path, wav_path):
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass

    duration_ms = int((time.monotonic() - start) * 1000)
    return VoiceResponse(
        session_id=resolved_session_id,
        transcription=transcription,
        reply=result["reply"],
        intent=result["intent"],
        trace_id=trace_id,
        requires_input=result.get("requires_input", False),
        metadata={
            "duration_ms": duration_ms,
            "transcription_duration_ms": transcription_ms,
            "model_used": result.get("model_used"),
        },
    )
