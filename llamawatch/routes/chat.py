"""Chat & terminal WebSockets plus chat-attachment text extraction."""

import asyncio
import fcntl
import json
import os
from pathlib import Path
import pty
import queue
import signal
import struct
import subprocess
import termios
import threading
import time
import urllib.request

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

from . import srv
from .. import security
from ..auth import is_auth_enabled

router = APIRouter()

_DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant."
_CHAT_LOG_DIR = os.path.expanduser("~/.config/llamawatch/chat_history")

_MAX_TERMINALS = 4
_active_terminals: set[asyncio.Task] = set()


def _stream_chat(
    url: str,
    model_id: str,
    messages: list[dict],
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
):
    """Blocking generator that yields SSE lines from a llama-server stream."""
    payload_dict: dict = {
        "model": model_id,
        "messages": messages,
        "stream": True,
    }
    if temperature is not None:
        payload_dict["temperature"] = temperature
    if top_p is not None:
        payload_dict["top_p"] = top_p
    if max_tokens is not None:
        payload_dict["max_tokens"] = max_tokens
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=30)
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield {"type": "token", "content": content}
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    finally:
        resp.close()


def _log_chat_message(model: str, role: str, content: str):
    """Append a message to the dashboard chat session log (JSONL format, one message per line)."""
    try:
        os.makedirs(_CHAT_LOG_DIR, exist_ok=True)
        from datetime import datetime
        safe_model = model.lower().replace(" ", "_")
        log_path = os.path.join(_CHAT_LOG_DIR, f"dashboard_{safe_model}.jsonl")
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Don't let logging failures break chat


@router.websocket("/ws/chat/{model}")
async def websocket_chat(websocket: WebSocket, model: str):
    # Auth/localhost gate BEFORE accept — WS bypasses the HTTP middleware
    if not security.action_allowed(websocket, is_auth_enabled()):
        await websocket.close(code=1008, reason="Not permitted")
        return
    # Resolve backend via adapter registry
    adapter = srv._adapters.get_by_name(model.lower()) if srv._adapters else None
    if adapter is None and srv._adapters:
        adapter = srv._adapters.get_primary()
    if adapter is None:
        await websocket.close(code=1008, reason="No backend configured")
        return

    url = adapter.chat_completions_url()
    model_id = adapter.model_name()
    await websocket.accept()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            messages = msg.get("messages", [])
            if not messages:
                await websocket.send_json({"type": "error", "message": "No messages provided"})
                continue

            # Extract optional generation parameters
            temperature = msg.get("temperature")
            top_p = msg.get("top_p")
            max_tokens = msg.get("max_tokens")

            # Ensure system prompt is present
            system_prompt = srv._config.get("chat_system_prompt", _DEFAULT_SYSTEM_PROMPT) if srv._config else _DEFAULT_SYSTEM_PROMPT
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_prompt})

            # Log the user message (last one in the list is the new one)
            last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
            if last_user:
                _log_chat_message(model, "user", last_user.get("content", ""))

            try:
                loop = asyncio.get_running_loop()
                token_q: queue.Queue = queue.Queue()
                assistant_chunks: list[str] = []
                start_time = time.monotonic()

                _stop = threading.Event()

                def _produce():
                    try:
                        for tok in _stream_chat(url, model_id, messages, temperature=temperature, top_p=top_p, max_tokens=max_tokens):
                            if _stop.is_set():
                                break  # client disconnected — stop pulling from the backend
                            token_q.put(tok)
                    except Exception as e:
                        token_q.put({"type": "error", "message": str(e)})
                    finally:
                        token_q.put(None)  # sentinel

                loop.run_in_executor(None, _produce)
                try:
                    while True:
                        item = await asyncio.to_thread(token_q.get)
                        if item is None:
                            break
                        await websocket.send_json(item)
                        if item.get("type") == "token" and item.get("content"):
                            assistant_chunks.append(item["content"])
                        if item.get("type") == "error":
                            break
                except WebSocketDisconnect:
                    _stop.set()  # signal the producer to stop streaming
                    raise

                # Log the full assistant response
                if assistant_chunks:
                    _log_chat_message(model, "assistant", "".join(assistant_chunks))

                await websocket.send_json({"type": "done"})
                # Log to request history
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                user_text = last_user.get("content", "") if last_user else ""
                assistant_text = "".join(assistant_chunks)
                srv._get_request_log().log_request(
                    model=model,
                    prompt_preview=user_text,
                    response_preview=assistant_text,
                    duration_ms=elapsed_ms,
                    source="dashboard-chat",
                )
            except Exception as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/terminal/{session_id}")
async def websocket_terminal(websocket: WebSocket, session_id: str):
    # Auth/localhost gate BEFORE accept — this WS spawns a shell, so it must
    # never be reachable unauthenticated or off-localhost. WS bypasses the
    # HTTP middleware, so the check lives here.
    if not security.action_allowed(websocket, is_auth_enabled()):
        await websocket.close(code=1008, reason="Not permitted")
        return
    # Validate session_id shape (used to readlink /proc/<pid>/cwd)
    if session_id != "new" and not session_id.isdigit():
        await websocket.close(code=1008, reason="Invalid session id")
        return
    # Enforce max concurrent terminals
    _active_terminals.discard(None)  # clean up
    alive = {t for t in _active_terminals if not t.done()}
    _active_terminals.clear()
    _active_terminals.update(alive)
    if len(_active_terminals) >= _MAX_TERMINALS:
        await websocket.close(code=1008, reason="Max terminals reached")
        return

    await websocket.accept()

    master_fd, slave_fd = pty.openpty()

    # Determine working directory
    cwd = os.path.expanduser("~")
    if session_id != "new":
        proc_cwd = f"/proc/{session_id}/cwd"
        try:
            cwd = os.readlink(proc_cwd)
        except (OSError, ValueError):
            pass  # fall back to home

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"

    proc = subprocess.Popen(
        ["bash"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=cwd,
        env=env,
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)

    loop = asyncio.get_running_loop()

    async def pty_to_ws():
        """Read from PTY master and send to WebSocket."""
        try:
            while True:
                data = await loop.run_in_executor(None, _read_pty, master_fd)
                if not data:
                    break
                await websocket.send_bytes(data)
        except (WebSocketDisconnect, Exception):
            pass

    async def ws_to_pty():
        """Read from WebSocket and write to PTY master."""
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if "text" in msg:
                    text = msg["text"]
                    # Check for resize JSON
                    try:
                        parsed = json.loads(text)
                        if parsed.get("type") == "resize":
                            cols = int(parsed.get("cols", 80))
                            rows = int(parsed.get("rows", 24))
                            winsize = struct.pack("HHHH", rows, cols, 0, 0)
                            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                    await loop.run_in_executor(None, os.write, master_fd, text.encode())
                elif "bytes" in msg:
                    await loop.run_in_executor(None, os.write, master_fd, msg["bytes"])
        except (WebSocketDisconnect, Exception):
            pass

    # Track this terminal session
    current_task = asyncio.current_task()
    _active_terminals.add(current_task)

    try:
        await asyncio.gather(pty_to_ws(), ws_to_pty(), return_exceptions=True)
    finally:
        _active_terminals.discard(current_task)
        # Kill process group and clean up
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            proc.wait(timeout=1)
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


def _read_pty(fd: int) -> bytes:
    """Blocking read from PTY fd. Returns empty bytes on EOF/error."""
    try:
        return os.read(fd, 4096)
    except OSError:
        return b""


@router.post("/api/chat/extract")
async def chat_extract(request: Request):
    """Extract plain text from an uploaded chat attachment.

    Decodes text/code/markdown natively; uses pypdf / python-docx for PDF and
    .docx when those libraries are installed. Returns {text, name, chars,
    truncated} or {error}. Output capped so it can't blow the context window.
    """
    _MAX_CHARS = 60000
    form = await request.form()
    field = None
    for _, f in form.multi_items():
        if hasattr(f, "filename") and f.filename:
            field = f
            break
    if field is None:
        return JSONResponse({"error": "no file"}, status_code=400)

    name = Path(field.filename).name
    raw = await field.read()
    _MAX_BYTES = 25 * 1024 * 1024  # 25 MB raw upload cap
    if len(raw) > _MAX_BYTES:
        return JSONResponse({"error": "File too large (max 25 MB)"}, status_code=413)
    suffix = Path(name).suffix.lower()

    def _decode_text(b: bytes):
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1", errors="replace")

    text = None
    if suffix == ".pdf":
        try:
            import io, pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except ImportError:
            return JSONResponse({"error": "PDF support needs 'pypdf' installed on the server"}, status_code=415)
        except Exception as e:
            return JSONResponse({"error": f"Could not read PDF: {e}"}, status_code=422)
    elif suffix in (".docx",):
        try:
            import io, docx
            d = docx.Document(io.BytesIO(raw))
            text = "\n".join(p.text for p in d.paragraphs)
        except ImportError:
            return JSONResponse({"error": "Word support needs 'python-docx' installed on the server"}, status_code=415)
        except Exception as e:
            return JSONResponse({"error": f"Could not read document: {e}"}, status_code=422)
    else:
        # Treat everything else as text (covers txt/md/code/json/csv/log/...)
        text = _decode_text(raw)

    text = text or ""
    truncated = len(text) > _MAX_CHARS
    if truncated:
        text = text[:_MAX_CHARS]
    return {"name": name, "text": text, "chars": len(text), "truncated": truncated}
