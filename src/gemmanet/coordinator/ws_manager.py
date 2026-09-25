"""WebSocket connection pool manager."""
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSConnectionManager:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}
        self.node_info: dict[str, dict] = {}

    def attach(self, node_id: str, websocket: WebSocket, info: dict) -> WebSocket | None:
        """Make `websocket` the live connection for node_id.

        Returns the connection it replaced (same API key + node name
        reconnecting), which the caller should close.
        """
        previous = self.connections.get(node_id)
        self.connections[node_id] = websocket
        self.node_info[node_id] = info
        logger.info(f'Node connected: {node_id}')
        return previous if previous is not websocket else None

    def detach(self, node_id: str, websocket: WebSocket) -> bool:
        """Forget node_id if `websocket` is still its live connection.

        Returns False when a newer connection has already taken over, so a
        replaced connection never tears down its successor's state.
        """
        if self.connections.get(node_id) is not websocket:
            return False
        self.connections.pop(node_id, None)
        self.node_info.pop(node_id, None)
        logger.info(f'Node disconnected: {node_id}')
        return True

    def get(self, node_id: str) -> WebSocket | None:
        return self.connections.get(node_id)

    async def send(self, node_id: str, websocket: WebSocket, message: str) -> bool:
        try:
            await websocket.send_text(message)
            return True
        except Exception as e:
            logger.error(f'Send to {node_id} failed: {e}')
            return False

    def get_online_nodes(self) -> list[dict]:
        return list(self.node_info.values())

    def get_node_info(self, node_id: str) -> dict | None:
        return self.node_info.get(node_id)

    def is_online(self, node_id: str) -> bool:
        return node_id in self.connections

    @property
    def online_count(self) -> int:
        return len(self.connections)
