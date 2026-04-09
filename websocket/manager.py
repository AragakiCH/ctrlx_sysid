from __future__ import annotations


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections = []

    async def connect(self, websocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_json(self, websocket, message: dict) -> None:
        await websocket.send_json(message)

    async def broadcast_json(self, message: dict) -> None:
        dead = []

        for websocket in self.active_connections:
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)

        for websocket in dead:
            self.disconnect(websocket)