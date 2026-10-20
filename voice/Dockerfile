# Voice worker (optional; compose profile "voice"). Separate from the backend image: aiortc's PyAV wheels bundle
# FFmpeg with GPL codecs, and speech models are large. Behind a TLS-intercepting proxy pass the CA bundle as the
# `extra_ca` build secret (see backend/Dockerfile).
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.11.32
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --no-install-project

# faster-whisper model baked into the image (no download at runtime). Pick a size for your CPU/GPU budget,
# or build with WHISPER_MODEL_REPO="" and use STT_BACKEND=groq / openai_compatible instead.
ARG WHISPER_MODEL_REPO=Systran/faster-whisper-small.en
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -n "$WHISPER_MODEL_REPO" ]; then \
      if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca REQUESTS_CA_BUNDLE=/run/secrets/extra_ca; fi; \
      HF_HUB_OFFLINE=0 python -c "from huggingface_hub import snapshot_download; snapshot_download('$WHISPER_MODEL_REPO', local_dir='/models/whisper')"; \
    fi

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd --uid 10003 --no-create-home voice
USER voice
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz').status==200 else 1)"
CMD ["uvicorn", "intrv_voice.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
