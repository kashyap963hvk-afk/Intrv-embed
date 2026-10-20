# intrv-bridge: run Intrv-embed's AI on your own PC or pendrive

The bridge lets the interview platform use a model that runs on **your** machine: a `.gguf` file on your disk or
a USB stick (served with llama.cpp's `llama-server`), or a model in Ollama / LM Studio / vLLM that is already
running. Your PC dials out to the platform; nothing listens on your network, and the platform can only ask for
chat completions, the model list, and cancellation.

## Install

- Download the single-file build for Windows, macOS or Linux from the release page, or
- with Python: `uvx intrv-bridge --help` (or `pipx install intrv-bridge`).

## Pair and run

1. In the web app open **AI models → Use a model on my PC** and copy the 6-digit code (valid 10 minutes).
2. `intrv-bridge pair https://api.your-domain.com 123456 --name "My laptop"`
3. `intrv-bridge add-folder E:\models` (any folder or pendrive with `.gguf` files; Ollama/LM Studio are found
   automatically)
4. `intrv-bridge install-llama-server` (downloads a llama.cpp CPU build and checks its SHA-256; GPU users can set
   `"llama_server"` in the config file to their own CUDA/Metal/Vulkan build)
5. `intrv-bridge run` and keep it open. Back in the web app the PC shows as online with its models: add one as a
   model profile and press **Test connection**.

Revoke a device in the web app at any time; the bridge then stops and asks to be paired again.

## Honest hardware notes

- A 7B Q4_K_M model is ~4.7 GB. From USB 2.0 it takes ~2.5 minutes to load, from USB 3 about 20–40 s. Models on
  removable drives are loaded fully into RAM (`--no-mmap`) so generation does not stutter.
- CPU-only 7B produces roughly 5–15 tokens/s: fine for text interviews, slow for voice. A GPU with ≥ 8 GB VRAM
  changes that.
- Quality: 7B is Basic (extraction and turn control only), 14B Usable, 32B Good, 70B and frontier models
  Excellent as interviewer or grader. The web app's capability probe rates your model.

## Build the single-file executable

```bash
uv sync --group dev
uv run --with pyinstaller pyinstaller packaging/intrv-bridge.spec
```
