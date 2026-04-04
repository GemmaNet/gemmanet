"""Node registry backed by Redis."""
import json
import redis.asyncio as aioredis


class NodeRegistry:
    def __init__(self, redis_url: str = 'redis://localhost:6379/0'):
        self.redis_url = redis_url
        self.redis: aioredis.Redis | None = None
        self.prefix = 'gn:node:'

    async def init(self):
        self.redis = aioredis.from_url(self.redis_url, decode_responses=True)

    async def close(self):
        if self.redis:
            await self.redis.close()

    async def register(self, node_id: str, info: dict):
        key = f'{self.prefix}{node_id}'
        await self.redis.set(key, json.dumps(info), ex=120)
        await self.redis.sadd('gn:online_nodes', node_id)

    async def update_heartbeat(self, node_id: str, load_info: dict):
        key = f'{self.prefix}{node_id}'
        existing = await self.redis.get(key)
        if existing:
            node_data = json.loads(existing)
            node_data.update(load_info)
            await self.redis.set(key, json.dumps(node_data), ex=120)
        load_key = f'gn:load:{node_id}'
        await self.redis.set(load_key, json.dumps(load_info), ex=120)

    async def unregister(self, node_id: str):
        key = f'{self.prefix}{node_id}'
        await self.redis.delete(key)
        await self.redis.delete(f'gn:load:{node_id}')
        await self.redis.srem('gn:online_nodes', node_id)

    async def get_node(self, node_id: str) -> dict | None:
        key = f'{self.prefix}{node_id}'
        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def get_online_nodes(self) -> list[dict]:
        members = await self.redis.smembers('gn:online_nodes')
        nodes = []
        for node_id in members:
            info = await self.get_node(node_id)
            if info:
                nodes.append(info)
        return nodes

    async def get_nodes_by_capability(self, capability: str) -> list[dict]:
        all_nodes = await self.get_online_nodes()
        return [n for n in all_nodes if capability in n.get('capabilities', [])]

    async def get_load(self, node_id: str) -> dict:
        load_key = f'gn:load:{node_id}'
        data = await self.redis.get(load_key)
        if data:
            return json.loads(data)
        return {}

    async def cleanup_stale(self):
        members = await self.redis.smembers('gn:online_nodes')
        for node_id in members:
            key = f'{self.prefix}{node_id}'
            if not await self.redis.exists(key):
                await self.redis.srem('gn:online_nodes', node_id)
                await self.redis.delete(f'gn:load:{node_id}')
