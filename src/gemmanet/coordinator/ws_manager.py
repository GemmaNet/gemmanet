"""WebSocket connection pool manager."""
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSConnectionManager:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}
        self.node_info: dict[str, dict] = {}

    async def connect(self, node_id: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[node_id] = websocket
        logger.info(f'Node connected: {node_id}')

    async def disconnect(self, node_id: str):
        self.connections.pop(node_id, None)
        self.node_info.pop(node_id, None)
        logger.info(f'Node disconnected: {node_id}')

    async def send_to_node(self, node_id: str, message: str) -> bool:
        try:
            ws = self.connections.get(node_id)
            if ws is None:
                logger.warning(f'No connection for {node_id}')
                return False
            await ws.send_text(message)
            return True
        except Exception as e:
            logger.error(f'Send to {node_id} failed: {e}')
            await self.disconnect(node_id)
            return False

    async def broadcast(self, message: str, exclude: str | None = None):
        for node_id in list(self.connections):
            if node_id != exclude:
                await self.send_to_node(node_id, message)

    def register_node(self, node_id: str, info: dict):
        self.node_info[node_id] = info

    def get_online_nodes(self) -> list[dict]:
        return list(self.node_info.values())

    def get_node_info(self, node_id: str) -> dict | None:
        return self.node_info.get(node_id)

    def is_online(self, node_id: str) -> bool:
        return node_id in self.connections

    @property
    def online_count(self) -> int:
        return len(self.connections)
