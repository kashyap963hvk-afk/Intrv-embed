# Kokoro TTS (GPL dependencies: phonemizer, espeak-ng). Optional; started with the "voice" compose profile.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KOKORO_MODEL_DIR=/models
WORKDIR /app
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir "kokoro-onnx==0.6.1" "fastapi>=0.115" "uvicorn>=0.34" && \
    python - <<'PY'
import os, ssl, urllib.request
base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
os.makedirs("/models", exist_ok=True)
context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE"))
for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
    with urllib.request.urlopen(base + name, context=context) as response, open(f"/models/{name}", "wb") as out:
        out.write(response.read())
PY
COPY app.py .
RUN useradd --uid 10002 --no-create-home tts && chown -R tts /models
USER tts
EXPOSE 8880
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD python -c "import urllib.request,json,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8880/healthz'))['status']=='ok' else 1)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8880"]
