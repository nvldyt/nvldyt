from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pathlib import Path
import json

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room: str):
        await websocket.accept()
        if room not in self.active_connections:
            self.active_connections[room] = []
        self.active_connections[room].append(websocket)

    def disconnect(self, websocket: WebSocket, room: str):
        if room in self.active_connections:
            if websocket in self.active_connections[room]:
                self.active_connections[room].remove(websocket)
            if not self.active_connections[room]:
                del self.active_connections[room]

    async def broadcast(self, message: str, room: str):
        if room in self.active_connections:
            for connection in list(self.active_connections[room]):
                try:
                    await connection.send_text(message)
                except Exception:
                    self.disconnect(connection, room)


manager = ConnectionManager()


@app.get("/")
async def get():
    html_file = BASE_DIR / "index.html"
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


@app.websocket("/ws/{room}/{nickname}")
async def websocket_endpoint(websocket: WebSocket, room: str, nickname: str):
    await manager.connect(websocket, room)

    join_msg = json.dumps({"type": "sys", "text": f"* {nickname} joined"})
    await manager.broadcast(join_msg, room)

    try:
        while True:
            data = await websocket.receive_text()
            chat_msg = json.dumps({"type": "chat", "user": nickname, "text": data})
            await manager.broadcast(chat_msg, room)

    except WebSocketDisconnect:
        manager.disconnect(websocket, room)
        leave_msg = json.dumps({"type": "sys", "text": f"* {nickname} left"})
        await manager.broadcast(leave_msg, room)
