# nanobot Local AI Endpoint API

This API exposes local, no-API-key endpoints that other apps, websites, or bots can call for Ollama chat/coder models, memory, voice, image generation, video generation, file/zip transfer, deep web scraping, YouTube helpers, Selenium automation, and the full nanobot CLI agent.

## Install

```bash
python -m pip install -e .
python -m pip install selenium python-multipart
```

Playwright is not required because some hosts fail with `ENOSPC` during browser downloads. Selenium is used for automation. For voice, run a local Piper/Coqui/OpenAI-compatible TTS server or install `espeak-ng` as a fallback.

## Start the API

```bash
nanobot local-api --host 0.0.0.0 --port 19074
```

Important local backend URLs can be changed with environment variables:

```bash
export NANOBOT_OLLAMA_BASE_URL=http://127.0.0.1:11434
export NANOBOT_SD_BASE_URL=http://127.0.0.1:7860
export NANOBOT_VIDEO_BASE_URL=http://127.0.0.1:7861
export NANOBOT_TTS_BASE_URL=http://127.0.0.1:8880
export NANOBOT_MEDIA_DIR=~/.nanobot/media
export NANOBOT_MEMORY_FILE=~/.nanobot/local-api-memory.jsonl
```

## Endpoints

### Health

```bash
curl http://127.0.0.1:19074/health
```

### List local model catalog and installed Ollama models

```bash
curl http://127.0.0.1:19074/v1/models
```

The response includes recommended local chat, coder, voice, image, and video models plus the models already installed in Ollama.

### Chat with memory

```bash
curl -X POST http://127.0.0.1:19074/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.1","user_id":"broken","remember":true,"prompt":"Remember that my app name is Nova Market."}'
```

Chat style messages:

```bash
curl -X POST http://127.0.0.1:19074/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.1","user_id":"broken","messages":[{"role":"system","content":"You are concise."},{"role":"user","content":"What app name did I tell you?"}]}'
```

### Write/search memory directly

```bash
curl -X POST http://127.0.0.1:19074/v1/memory/write \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"broken","content":"The user prefers female multilingual voice output.","metadata":{"source":"manual"}}'
```

```bash
curl -X POST http://127.0.0.1:19074/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"broken","query":"voice output","limit":5}'
```

### Run the full nanobot CLI agent over HTTP

```bash
curl -X POST http://127.0.0.1:19074/v1/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"api:broken","message":"Use your local tools and help me plan a landing page."}'
```

### Local female multilingual voice / TTS

Run a local TTS server at `NANOBOT_TTS_BASE_URL` with an OpenAI-compatible `/v1/audio/speech` endpoint, or install `espeak-ng` for a basic local fallback.

```bash
curl -X POST http://127.0.0.1:19074/v1/voice/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello, I can speak for your local AI app.","model":"piper-female-multilingual","voice":"female","language":"auto"}'
```

The response contains an `audio_url`, for example `/v1/media/voice-xxxx.wav`.

### Generate an image

Requires a local Stable Diffusion WebUI API at `NANOBOT_SD_BASE_URL`.

```bash
curl -X POST http://127.0.0.1:19074/v1/images/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a futuristic robot cat logo, neon, clean vector style","width":768,"height":768,"steps":25}'
```

### Edit an image

```bash
curl -X POST http://127.0.0.1:19074/v1/images/edit \
  -H 'Content-Type: application/json' \
  -d '{"image_path":"/home/me/input.png","prompt":"make the background cyberpunk blue","strength":0.45}'
```

### Generate a video

Point `NANOBOT_VIDEO_BASE_URL` at your local video generation service. The API forwards the prompt to `/v1/videos/generate`.

```bash
curl -X POST http://127.0.0.1:19074/v1/videos/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a robot cat walking through Lagos at night","model":"local-video-model"}'
```

### Generate a full app with the coder model and download it as zip

Pull a local coder model first, for example `ollama pull qwen2.5-coder`.

```bash
curl -X POST http://127.0.0.1:19074/v1/coder/app \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-coder","app_name":"todo-web-app","prompt":"Create a full HTML CSS JS todo app with local storage and a README."}'
```

The response contains `zip_url`, for example `/v1/media/todo-web-app-ab12cd34.zip`. Download it:

```bash
curl -L http://127.0.0.1:19074/v1/media/todo-web-app-ab12cd34.zip -o todo-web-app.zip
```

### Upload and download files

```bash
curl -X POST http://127.0.0.1:19074/v1/files/upload \
  -F 'description=source logo for my generated app' \
  -F 'file=@/home/me/logo.png'
```

Download an uploaded file using the `url` returned by upload:

```bash
curl -L http://127.0.0.1:19074/v1/files/RETURNED_FILE_NAME -o downloaded-file
```

### Create a zip from local files/folders

```bash
curl -X POST http://127.0.0.1:19074/v1/zip/create \
  -H 'Content-Type: application/json' \
  -d '["/home/me/project","/home/me/readme.txt"]'
```

### Deep web scraper

```bash
curl -X POST http://127.0.0.1:19074/v1/scrape/deep \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","max_pages":10,"depth":2,"same_domain":true,"save_zip":true}'
```

### Search the web

```bash
curl -X POST http://127.0.0.1:19074/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"latest open source local image generation API","max_results":5}'
```

### Play or link YouTube music/videos

```bash
curl -X POST http://127.0.0.1:19074/v1/youtube/play \
  -H 'Content-Type: application/json' \
  -d '{"query":"lofi hip hop radio","open_browser":false}'
```

Open the local browser too:

```bash
curl -X POST http://127.0.0.1:19074/v1/youtube/play \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=jfKfPfyJRdk","open_browser":true}'
```

### Browser automation with Selenium

Open a page:

```bash
curl -X POST http://127.0.0.1:19074/v1/browser \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","action":"open"}'
```

Click a CSS selector and save a screenshot:

```bash
curl -X POST http://127.0.0.1:19074/v1/browser \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","action":"click","selector":"a","headless":true}'
```

## Interactive docs

```text
http://127.0.0.1:19074/docs
http://127.0.0.1:19074/redoc
```

Other apps can call the JSON endpoints with `fetch`, Axios, Python `requests`, curl, or any HTTP client.
