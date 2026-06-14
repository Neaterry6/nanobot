"""Local AI HTTP API for Ollama, media generation, search, memory, and automation."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


OLLAMA_BASE_URL = _env("NANOBOT_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
SD_BASE_URL = _env("NANOBOT_SD_BASE_URL", "http://127.0.0.1:7860")
VIDEO_BASE_URL = _env("NANOBOT_VIDEO_BASE_URL", "http://127.0.0.1:7861")
TTS_BASE_URL = _env("NANOBOT_TTS_BASE_URL", "http://127.0.0.1:8880")
MEDIA_DIR = Path(os.environ.get("NANOBOT_MEDIA_DIR", "~/.nanobot/media")).expanduser()
MEMORY_FILE = Path(os.environ.get("NANOBOT_MEMORY_FILE", "~/.nanobot/local-api-memory.jsonl")).expanduser()
UPLOAD_DIR = MEDIA_DIR / "uploads"
BUNDLE_DIR = MEDIA_DIR / "bundles"
for directory in (MEDIA_DIR, UPLOAD_DIR, BUNDLE_DIR, MEMORY_FILE.parent):
    directory.mkdir(parents=True, exist_ok=True)

LOCAL_MODEL_CATALOG: dict[str, Any] = {
    "chat": ["llama3.1", "qwen2.5", "mistral", "gemma2"],
    "coder": ["qwen2.5-coder", "deepseek-coder-v2", "codellama", "starcoder2"],
    "voice": [
        {
            "name": "piper-female-multilingual",
            "type": "local-tts",
            "voice": "female",
            "note": "Configure NANOBOT_TTS_BASE_URL to a local Piper/Coqui/OpenAI-compatible TTS server.",
        }
    ],
    "image": ["stable-diffusion-webui", "sdxl", "flux-local"],
    "video": ["local-video-backend", "comfyui-video", "animatediff"],
}

app = FastAPI(
    title="nanobot Local AI API",
    version="0.2.0",
    description=(
        "No-key local endpoints for Ollama chat/coder models, memory, voice, image/video, "
        "zip/file transfer, deep scraping, CLI-agent runs, YouTube helpers, and automation."
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
    remember: bool = True
    user_id: str = "local"
    memory_limit: int = 12


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


class MemoryWriteRequest(BaseModel):
    user_id: str = "local"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    user_id: str = "local"
    query: str = ""
    limit: int = 20


class VoiceRequest(BaseModel):
    text: str
    model: str = "piper-female-multilingual"
    voice: str = "female"
    language: str = "auto"
    speed: float = 1.0


class CoderRequest(BaseModel):
    prompt: str
    app_name: str = "local_app"
    model: str = "qwen2.5-coder"
    include_memory: bool = True


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = 10
    depth: int = 1
    same_domain: bool = True
    save_zip: bool = False


class AgentRequest(BaseModel):
    message: str
    session_id: str = "api:local"


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ollama": OLLAMA_BASE_URL,
        "tts": TTS_BASE_URL,
        "media_dir": str(MEDIA_DIR),
        "memory_file": str(MEMORY_FILE),
        "no_api_key_required": True,
    }


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    ollama: dict[str, Any] = {"models": []}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            response.raise_for_status()
            ollama = response.json()
        except httpx.HTTPError:
            ollama = {"models": [], "warning": f"Ollama is unavailable at {OLLAMA_BASE_URL}"}
    return {"catalog": LOCAL_MODEL_CATALOG, "ollama": ollama}


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    if not req.prompt and not req.messages:
        raise HTTPException(400, "Send either prompt or messages.")
    messages = await _messages_with_memory(req)
    payload = {
        "model": req.model,
        "messages": messages,
        "stream": req.stream,
        "options": {"temperature": req.temperature},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Ollama chat failed: {exc}") from exc
    content = data.get("message", {}).get("content", "")
    if req.remember:
        await _append_memory(req.user_id, "user", _request_text(req), {"endpoint": "chat"})
        await _append_memory(req.user_id, "assistant", content, {"endpoint": "chat", "model": req.model})
    return {"model": req.model, "message": data.get("message", {}), "memory": req.remember, "raw": data}


@app.post("/v1/agent/run")
async def run_cli_agent(req: AgentRequest) -> dict[str, Any]:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.cli.commands import _make_provider
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.service import CronService
    from nanobot.utils.helpers import sync_workspace_templates

    config = load_config()
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    cron = CronService(get_data_dir() / "cron" / "jobs.json")
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=None,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )
    try:
        response = await agent_loop.process_direct(req.message, req.session_id)
    finally:
        await agent_loop.close_mcp()
    return {"session_id": req.session_id, "response": response}


@app.post("/v1/memory/write")
async def memory_write(req: MemoryWriteRequest) -> dict[str, Any]:
    record = await _append_memory(req.user_id, "note", req.content, req.metadata)
    return {"ok": True, "memory": record}


@app.post("/v1/memory/search")
async def memory_search(req: MemorySearchRequest) -> dict[str, Any]:
    return {"memories": _search_memory(req.user_id, req.query, req.limit)}


@app.post("/v1/voice/speak")
async def voice_speak(req: VoiceRequest) -> dict[str, Any]:
    audio_path = MEDIA_DIR / f"voice-{uuid.uuid4().hex}.wav"
    payload = {
        "model": req.model,
        "voice": req.voice,
        "input": req.text,
        "language": req.language,
        "speed": req.speed,
    }
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            response = await client.post(f"{TTS_BASE_URL}/v1/audio/speech", json=payload)
            response.raise_for_status()
            audio_path.write_bytes(response.content)
            return {"audio_url": f"/v1/media/{audio_path.name}", "backend": TTS_BASE_URL}
        except httpx.HTTPError:
            fallback = _speak_with_local_binary(req.text, audio_path, req.speed)
            if fallback:
                return {"audio_url": f"/v1/media/{audio_path.name}", "backend": fallback}
    raise HTTPException(
        502,
        "No local TTS backend available. Start Piper/Coqui/OpenAI-compatible TTS at NANOBOT_TTS_BASE_URL or install espeak-ng.",
    )


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


@app.post("/v1/coder/app")
async def coder_app(req: CoderRequest) -> dict[str, Any]:
    app_slug = _safe_name(req.app_name)
    bundle_root = BUNDLE_DIR / f"{app_slug}-{uuid.uuid4().hex[:8]}"
    bundle_root.mkdir(parents=True, exist_ok=True)
    memories = _search_memory("local", req.prompt, 10) if req.include_memory else []
    system = (
        "You are a local coding model. Return ONLY JSON with a 'files' object. "
        "Keys are relative file paths, values are complete file contents. Build a full runnable app structure."
    )
    prompt = f"User request: {req.prompt}\nRelevant memory: {json.dumps(memories, ensure_ascii=False)}"
    data = await _ollama_chat_json(req.model, system, prompt)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        files = {
            "README.md": f"# {req.app_name}\n\nThe local coder model did not return valid JSON. Original request:\n\n{req.prompt}\n",
            "app.py": "print('Hello from generated app')\n",
        }
    written = _write_bundle_files(bundle_root, files)
    zip_path = _zip_directory(bundle_root)
    await _append_memory("local", "assistant", f"Generated app bundle {zip_path.name} for: {req.prompt}", {"endpoint": "coder"})
    return {"files": written, "zip_url": f"/v1/media/{zip_path.name}", "root": str(bundle_root)}


@app.post("/v1/files/upload")
async def upload_file(file: UploadFile = File(...), description: str = Form("")) -> dict[str, Any]:
    name = _safe_filename(file.filename or f"upload-{uuid.uuid4().hex}")
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}-{name}"
    target.write_bytes(await file.read())
    await _append_memory("local", "file", f"Uploaded file {target.name}. {description}", {"path": str(target)})
    return {"filename": target.name, "url": f"/v1/files/{target.name}", "size": target.stat().st_size}


@app.get("/v1/files/{name}")
async def download_file(name: str) -> FileResponse:
    path = UPLOAD_DIR / name
    if not path.exists() or path.parent != UPLOAD_DIR:
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=name)


@app.post("/v1/zip/create")
async def create_zip(paths: list[str]) -> dict[str, Any]:
    zip_path = MEDIA_DIR / f"archive-{uuid.uuid4().hex}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for raw in paths:
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                continue
            if path.is_file():
                archive.write(path, arcname=path.name)
            else:
                for child in path.rglob("*"):
                    if child.is_file():
                        archive.write(child, arcname=str(Path(path.name) / child.relative_to(path)))
    return {"zip_url": f"/v1/media/{zip_path.name}"}


@app.post("/v1/scrape/deep")
async def deep_scrape(req: ScrapeRequest) -> dict[str, Any]:
    pages = await _crawl(req.url, req.max_pages, req.depth, req.same_domain)
    result: dict[str, Any] = {"start_url": req.url, "pages": pages}
    if req.save_zip:
        root = BUNDLE_DIR / f"scrape-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "scrape.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        for index, page in enumerate(pages, 1):
            (root / f"page-{index}.txt").write_text(page.get("text", ""), encoding="utf-8")
        zip_path = _zip_directory(root)
        result["zip_url"] = f"/v1/media/{zip_path.name}"
    return result


@app.post("/v1/search")
async def search(req: SearchRequest) -> dict[str, Any]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(req.query)}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "nanobot-local-api/0.2"})
        response.raise_for_status()
    pattern = re.compile(r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)">(?P<title>.*?)</a>', re.S)
    results = []
    for match in pattern.finditer(response.text):
        title = re.sub("<.*?>", "", match.group("title"))
        results.append({"title": unescape(title), "url": unescape(match.group("url"))})
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
    return FileResponse(path, filename=name)


async def _messages_with_memory(req: ChatRequest) -> list[dict[str, str]]:
    messages = [m.model_dump() for m in req.messages] if req.messages else [{"role": "user", "content": req.prompt or ""}]
    if not req.remember:
        return messages
    memories = _search_memory(req.user_id, _request_text(req), req.memory_limit)
    if not memories:
        return messages
    memory_text = "\n".join(f"- {item['role']}: {item['content']}" for item in memories)
    return [{"role": "system", "content": f"Relevant local memory:\n{memory_text}"}, *messages]


def _request_text(req: ChatRequest) -> str:
    if req.prompt:
        return req.prompt
    return "\n".join(message.content for message in req.messages or [])


async def _append_memory(user_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    record = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(),
        "user_id": user_id,
        "role": role,
        "content": content,
        "metadata": metadata or {},
    }
    await asyncio.to_thread(_append_jsonl, MEMORY_FILE, record)
    return record


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _search_memory(user_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    if not MEMORY_FILE.exists():
        return []
    terms = {term.lower() for term in re.findall(r"\w+", query)}
    scored: list[tuple[int, dict[str, Any]]] = []
    for line in MEMORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("user_id") != user_id:
            continue
        content = str(record.get("content", ""))
        content_terms = set(re.findall(r"\w+", content.lower()))
        score = len(terms & content_terms) if terms else 1
        if score or not terms:
            scored.append((score, record))
    scored.sort(key=lambda item: (item[0], item[1].get("created_at", "")), reverse=True)
    return [record for _, record in scored[:limit]]


async def _ollama_chat_json(model: str, system: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "{}")
            return json.loads(content)
        except (httpx.HTTPError, json.JSONDecodeError):
            return {}


def _write_bundle_files(root: Path, files: dict[str, Any]) -> list[str]:
    written = []
    for raw_path, content in files.items():
        rel = Path(_safe_relative_path(raw_path))
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        written.append(str(rel))
    return written


def _zip_directory(root: Path) -> Path:
    zip_path = MEDIA_DIR / f"{root.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for child in root.rglob("*"):
            if child.is_file():
                archive.write(child, arcname=str(child.relative_to(root)))
    return zip_path


async def _crawl(start_url: str, max_pages: int, depth: int, same_domain: bool) -> list[dict[str, Any]]:
    parsed_start = urlparse(start_url)
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    pages: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "nanobot-deep-scraper/0.2"}) as client:
        while queue and len(pages) < max_pages:
            url, level = queue.pop(0)
            if url in visited or level > depth:
                continue
            visited.add(url)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            html = response.text
            text = _html_to_text(html)
            links = _extract_links(url, html)
            pages.append({"url": str(response.url), "title": _extract_title(html), "text": text[:20000], "links": links[:100]})
            if level < depth:
                for link in links:
                    parsed = urlparse(link)
                    if same_domain and parsed.netloc != parsed_start.netloc:
                        continue
                    if link not in visited:
                        queue.append((link, level + 1))
    return pages


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""


def _extract_links(base_url: str, html: str) -> list[str]:
    links = []
    for raw in re.findall(r"href=[\"']([^\"'#]+)", html, re.I):
        absolute = urljoin(base_url, unescape(raw))
        if absolute.startswith(("http://", "https://")):
            links.append(absolute)
    return list(dict.fromkeys(links))


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _speak_with_local_binary(text: str, output: Path, speed: float) -> str | None:
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        return None
    words_per_minute = max(80, min(450, int(175 * speed)))
    subprocess.run([espeak, "-w", str(output), "-s", str(words_per_minute), text], check=False)
    return espeak if output.exists() else None


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


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-") or "local-app"


def _safe_filename(name: str) -> str:
    return _safe_name(Path(name).name)


def _safe_relative_path(path: str) -> str:
    rel = Path(path.replace("\\", "/"))
    safe_parts = [_safe_name(part) for part in rel.parts if part not in {"", ".", ".."}]
    return str(Path(*safe_parts)) if safe_parts else "README.md"


def run(host: str = "0.0.0.0", port: int = 19074) -> None:
    import uvicorn

    uvicorn.run("nanobot.local_api:app", host=host, port=port, reload=False)
