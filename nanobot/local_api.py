"""Local AI HTTP API for Ollama, media generation, search, and browser automation."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


OLLAMA_BASE_URL = _env("NANOBOT_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
SD_BASE_URL = _env("NANOBOT_SD_BASE_URL", "http://127.0.0.1:7860")
COMFYUI_BASE_URL = _env("NANOBOT_COMFYUI_BASE_URL", "http://127.0.0.1:8188")
VIDEO_BASE_URL = _env("NANOBOT_VIDEO_BASE_URL", "http://127.0.0.1:7861")
MEDIA_DIR = Path(os.environ.get("NANOBOT_MEDIA_DIR", "~/.nanobot/media")).expanduser()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="nanobot Local AI API",
    version="0.1.0",
    description=(
        "Developer endpoints for local Ollama chat, image generation, video generation, "
        "media editing, search, YouTube playback helpers, and browser automation."
    ),
)


class ChatMessage(BaseModel):
    role: str = Field(..., examples=["user"])
    content: str


class ChatRequest(BaseModel):
    prompt: str | None = None
    messages: list[ChatMessage] | None = None
    model: str = "llama3.1"
    temperature: float = 0.7
    stream: bool = False


class PromptRequest(BaseModel):
    prompt: str
    model: str | None = None
    negative_prompt: str = ""


class ImageRequest(PromptRequest):
    width: int = 768
    height: int = 768
    steps: int = 25


class EditRequest(BaseModel):
    image_url: str | None = None
    image_path: str | None = None
    prompt: str
    strength: float = 0.45


class SearchRequest(BaseModel):
    query: str
    max_results: int = 5


class YoutubeRequest(BaseModel):
    query: str | None = None
    url: str | None = None
    open_browser: bool = False


class BrowserRequest(BaseModel):
    url: str
    action: str = "open"
    selector: str | None = None
    text: str | None = None
    headless: bool = True


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "ollama": OLLAMA_BASE_URL, "media_dir": str(MEDIA_DIR)}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Ollama is unavailable at {OLLAMA_BASE_URL}: {exc}") from exc


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    if not req.prompt and not req.messages:
        raise HTTPException(400, "Send either prompt or messages.")
    messages = [m.model_dump() for m in req.messages] if req.messages else [{"role": "user", "content": req.prompt}]
    payload = {"model": req.model, "messages": messages, "stream": req.stream, "options": {"temperature": req.temperature}}
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Ollama chat failed: {exc}") from exc
    return {"model": req.model, "message": data.get("message", {}), "raw": data}


@app.post("/v1/images/generate")
async def image_generate(req: ImageRequest) -> dict[str, Any]:
    payload = {
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt,
        "width": req.width,
        "height": req.height,
        "steps": req.steps,
    }
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            response = await client.post(f"{SD_BASE_URL}/sdapi/v1/txt2img", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Stable Diffusion API is unavailable at {SD_BASE_URL}: {exc}") from exc
    return {"backend": "stable-diffusion-webui", "images": data.get("images", []), "info": data.get("info")}


@app.post("/v1/images/edit")
async def image_edit(req: EditRequest) -> dict[str, Any]:
    if not req.image_url and not req.image_path:
        raise HTTPException(400, "Send image_url or image_path.")
    image_b64 = await _load_image_b64(req)
    payload = {
        "init_images": [image_b64],
        "prompt": req.prompt,
        "denoising_strength": req.strength,
    }
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            response = await client.post(f"{SD_BASE_URL}/sdapi/v1/img2img", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Stable Diffusion img2img API is unavailable at {SD_BASE_URL}: {exc}") from exc
    return {"backend": "stable-diffusion-webui", "images": data.get("images", []), "info": data.get("info")}


@app.post("/v1/videos/generate")
async def video_generate(req: PromptRequest) -> dict[str, Any]:
    payload = req.model_dump(exclude_none=True)
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            response = await client.post(f"{VIDEO_BASE_URL}/v1/videos/generate", json=payload)
            response.raise_for_status()
            return {"backend": VIDEO_BASE_URL, "result": response.json()}
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Video backend is unavailable at {VIDEO_BASE_URL}: {exc}") from exc


@app.post("/v1/search")
async def search(req: SearchRequest) -> dict[str, Any]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(req.query)}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "nanobot-local-api/0.1"})
        response.raise_for_status()
    pattern = re.compile(r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)">(?P<title>.*?)</a>', re.S)
    results = []
    for match in pattern.finditer(response.text):
        title = re.sub("<.*?>", "", match.group("title"))
        results.append({"title": title, "url": match.group("url")})
        if len(results) >= req.max_results:
            break
    return {"query": req.query, "results": results}


@app.post("/v1/youtube/play")
async def youtube_play(req: YoutubeRequest) -> dict[str, Any]:
    if not req.url and not req.query:
        raise HTTPException(400, "Send url or query.")
    url = req.url or f"https://www.youtube.com/results?search_query={quote_plus(req.query or '')}"
    if req.open_browser:
        _open_url(url)
    return {"url": url, "embed_hint": url.replace("watch?v=", "embed/") if "watch?v=" in url else None}


@app.post("/v1/browser")
async def browser(req: BrowserRequest) -> dict[str, Any]:
    if req.action == "open":
        _open_url(req.url)
        return {"ok": True, "action": "open", "url": req.url}
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "Install selenium and a browser driver first: python -m pip install selenium") from exc
    options = webdriver.ChromeOptions()
    if req.headless:
        options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(req.url)
        if req.action == "click" and req.selector:
            driver.find_element(By.CSS_SELECTOR, req.selector).click()
        elif req.action == "type" and req.selector and req.text is not None:
            driver.find_element(By.CSS_SELECTOR, req.selector).send_keys(req.text)
        screenshot = MEDIA_DIR / f"browser-{uuid.uuid4().hex}.png"
        driver.save_screenshot(str(screenshot))
        return {"ok": True, "screenshot": f"/v1/media/{screenshot.name}"}
    finally:
        driver.quit()


@app.get("/v1/media/{name}")
async def media(name: str) -> FileResponse:
    path = MEDIA_DIR / name
    if not path.exists() or path.parent != MEDIA_DIR:
        raise HTTPException(404, "Media not found")
    return FileResponse(path)


async def _load_image_b64(req: EditRequest) -> str:
    if req.image_url:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(req.image_url)
            response.raise_for_status()
            return base64.b64encode(response.content).decode("ascii")
    path = Path(req.image_path or "").expanduser()
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"Image file not found: {path}")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
    elif os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, url])


def run(host: str = "0.0.0.0", port: int = 19074) -> None:
    import uvicorn

    uvicorn.run("nanobot.local_api:app", host=host, port=port, reload=False)
