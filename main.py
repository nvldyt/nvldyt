from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json

app = FastAPI()

# Trình quản lý các kết nối WebSockets
class ConnectionManager:
    def __init__(self):
        # Lưu trữ các kết nối theo định dạng: {"ten_phong": [websocket1, websocket2, ...]}
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room: str):
        await websocket.accept()
        if room not in self.active_connections:
            self.active_connections[room] = []
        self.active_connections[room].append(websocket)

    def disconnect(self, websocket: WebSocket, room: str):
        if room in self.active_connections:
            self.active_connections[room].remove(websocket)
            if not self.active_connections[room]:
                del self.active_connections[room]

    async def broadcast(self, message: str, room: str):
        if room in self.active_connections:
            for connection in self.active_connections[room]:
                await connection.send_text(message)

manager = ConnectionManager()

# Trang chủ: Trả về file HTML giao diện
@app.get("/")
async def get():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# Điểm cuối WebSocket (Ống dẫn dữ liệu)
@app.websocket("/ws/{room}/{nickname}")
async def websocket_endpoint(websocket: WebSocket, room: str, nickname: str):
    await manager.connect(websocket, room)
    
    # Báo cho cả phòng biết có người mới vào (Tin nhắn hệ thống)
    join_msg = json.dumps({"type": "sys", "text": f"* {nickname} joined"})
    await manager.broadcast(join_msg, room)
    
    try:
        while True:
            # Chờ nhận tin nhắn từ người dùng
            data = await websocket.receive_text()
            
            # Gửi tin nhắn đó cho tất cả mọi người trong phòng
            chat_msg = json.dumps({"type": "chat", "user": nickname, "text": data})
            await manager.broadcast(chat_msg, room)
            
    except WebSocketDisconnect:
        # Xử lý khi người dùng đóng trình duyệt (Rời mạng)
        manager.disconnect(websocket, room)
        leave_msg = json.dumps({"type": "sys", "text": f"* {nickname} left"})
        await manager.broadcast(leave_msg, room)
