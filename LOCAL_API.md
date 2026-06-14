# nanobot Local AI Endpoint API

This API turns a local machine into an HTTP service that other apps, websites, or bots can call for AI text, image generation, video generation, search, YouTube playback links, and browser automation.

## Install

```bash
python -m pip install -e .
python -m pip install selenium
```

Playwright is not required because some hosts fail with `ENOSPC` during browser downloads. This API uses Selenium instead. If Chrome cannot start, install Chrome/Chromium and ChromeDriver for your host.

## Start the API

```bash
nanobot local-api --host 0.0.0.0 --port 19074
```

Important local backend URLs can be changed with environment variables:

```bash
export NANOBOT_OLLAMA_BASE_URL=http://127.0.0.1:11434
export NANOBOT_SD_BASE_URL=http://127.0.0.1:7860
export NANOBOT_VIDEO_BASE_URL=http://127.0.0.1:7861
export NANOBOT_MEDIA_DIR=~/.nanobot/media
```

## Endpoints

### Health

```bash
curl http://127.0.0.1:19074/health
```

### List Ollama models

```bash
curl http://127.0.0.1:19074/v1/models
```

### Chat with a local Ollama model

```bash
curl -X POST http://127.0.0.1:19074/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.1","prompt":"Write a short welcome message for my website."}'
```

Chat style messages:

```bash
curl -X POST http://127.0.0.1:19074/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.1","messages":[{"role":"system","content":"You are concise."},{"role":"user","content":"Make a product tagline."}]}'
```

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

### Search the web

```bash
curl -X POST http://127.0.0.1:19074/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"latest open source local image generation API","max_results":5}'
```

### Play or link YouTube music/videos

Return a YouTube URL for another app to embed or open:

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

## Query-string examples

FastAPI serves interactive docs at:

```text
http://127.0.0.1:19074/docs
http://127.0.0.1:19074/redoc
```

Other apps can call the JSON endpoints with `fetch`, Axios, Python `requests`, curl, or any HTTP client.
