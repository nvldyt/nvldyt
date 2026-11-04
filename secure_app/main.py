from __future__ import annotations

import asyncio
import base64
import hmac
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOM_TTL_SECONDS = int(os.getenv("ROOM_TTL_SECONDS", "7200"))
IDLE_TTL_SECONDS = int(os.getenv("IDLE_TTL_SECONDS", "900"))
MAX_CONNECTIONS_PER_ROOM = 2
MAX_FRAME_BYTES = 16_384
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,32}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")

app = FastAPI(
    title="Private Ephemeral Chat",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

@dataclass
class Room:
    verifier: str
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    connections: Set[WebSocket] = field(default_factory=set)

rooms: Dict[str, Room] = {}
rooms_lock = asyncio.Lock()
create_attempts: Dict[str, list[float]] = {}

class CreateRoomRequest(BaseModel):
    room_id: str = Field(min_length=20, max_length=32)
    verifier: str = Field(min_length=40, max_length=64)

def clean_expired_rooms() -> None:
    now = time.monotonic()
    expired = [
        room_id for room_id, room in rooms.items()
        if not room.connections and (
            now - room.last_activity > IDLE_TTL_SECONDS
            or now - room.created_at > ROOM_TTL_SECONDS
        )
    ]
    for room_id in expired:
        rooms.pop(room_id, None)

def origin_is_allowed(origin: str | None, host: str | None) -> bool:
    if not origin:
        return False
    configured = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
    if configured:
        return origin in configured
    # Same-origin only by default. Set ALLOWED_ORIGINS explicitly behind a proxy
    # if the public Host header differs from the browser-visible host.
    from urllib.parse import urlparse
    parsed = urlparse(origin)
    return bool(host and parsed.netloc.lower() == host.lower() and parsed.scheme in {"https", "http"})

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' wss: ws:; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response

@app.get("/")
async def index():
    return FileResponse(
        os.path.join(APP_DIR, "static", "index.html"),
        headers={"Cache-Control": "no-store"},
    )

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/api/rooms", status_code=201)
async def create_room(payload: CreateRoomRequest, request: Request):
    if not ROOM_ID_RE.fullmatch(payload.room_id) or not VERIFIER_RE.fullmatch(payload.verifier):
        raise HTTPException(status_code=422, detail="Invalid room credentials")
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    recent = [t for t in create_attempts.get(client_ip, []) if now - t < 60]
    if len(recent) >= 10:
        raise HTTPException(status_code=429, detail="Too many room creation attempts")
    recent.append(now)
    create_attempts[client_ip] = recent

    async with rooms_lock:
        clean_expired_rooms()
        if payload.room_id in rooms:
            raise HTTPException(status_code=409, detail="Room already exists")
        rooms[payload.room_id] = Room(verifier=payload.verifier)
    return JSONResponse({"created": True}, headers={"Cache-Control": "no-store"})

@app.websocket("/ws/{room_id}")
async def websocket_chat(websocket: WebSocket, room_id: str):
    if not ROOM_ID_RE.fullmatch(room_id):
        await websocket.close(code=1008)
        return
    if not origin_is_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return

    async with rooms_lock:
        clean_expired_rooms()
        room = rooms.get(room_id)
        if room is None or len(room.connections) >= MAX_CONNECTIONS_PER_ROOM:
            await websocket.close(code=1008)
            return
        expected_verifier = room.verifier

    await websocket.accept()
    try:
        # First frame is a bearer proof derived from the room secret. No chat
        # plaintext or encryption key is sent to this server.
        auth_frame = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        if len(auth_frame) > 256:
            await websocket.close(code=1008)
            return
        import json
        auth = json.loads(auth_frame)
        supplied = auth.get("verifier", "") if isinstance(auth, dict) else ""
        if auth.get("type") != "auth" or not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected_verifier):
            await websocket.close(code=1008)
            return

        async with rooms_lock:
            room = rooms.get(room_id)
            if room is None or len(room.connections) >= MAX_CONNECTIONS_PER_ROOM:
                await websocket.close(code=1008)
                return
            room.connections.add(websocket)
            room.last_activity = time.monotonic()

        await websocket.send_text('{"type":"ready"}')
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                await websocket.close(code=1009)
                break
            # Only opaque AES-GCM envelopes are relayed. The server deliberately
            # does not parse, log, or persist message contents.
            try:
                packet = json.loads(raw)
                if not isinstance(packet, dict) or packet.get("type") != "ciphertext":
                    continue
                iv, ciphertext = packet.get("iv"), packet.get("ciphertext")
                if not isinstance(iv, str) or not isinstance(ciphertext, str):
                    continue
                if len(iv) > 32 or len(ciphertext) > MAX_FRAME_BYTES * 2:
                    continue
            except (ValueError, TypeError):
                continue

            async with rooms_lock:
                room = rooms.get(room_id)
                peers = list(room.connections) if room else []
                if room:
                    room.last_activity = time.monotonic()
            for peer in peers:
                if peer is not websocket:
                    try:
                        await peer.send_text(raw)
                    except Exception:
                        pass
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
        pass
    finally:
        async with rooms_lock:
            room = rooms.get(room_id)
            if room:
                room.connections.discard(websocket)
                room.last_activity = time.monotonic()
                if not room.connections:
                    # Destroy the room verifier immediately after the last
                    # participant leaves: no history and no reconnect window.
                    rooms.pop(room_id, None)
