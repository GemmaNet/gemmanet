"""Routing engine: match requests to best nodes."""
import random

from gemmanet.coordinator.registry import NodeRegistry
from gemmanet.coordinator.ws_manager import WSConnectionManager


class RoutingEngine:
    def __init__(self, registry: NodeRegistry, ws_manager: WSConnectionManager):
        self.registry = registry
        self.ws_manager = ws_manager

    async def find_best_node(self, task_type: str,
                             params: dict = None) -> str | None:
        candidates = await self.registry.get_nodes_by_capability(task_type)
        candidates = [n for n in candidates
                      if self.ws_manager and self.ws_manager.is_online(n['node_id'])]
        if not candidates:
            return None

        scored = []
        for node in candidates:
            load = await self.registry.get_load(node['node_id'])
            load_ratio = load.get('cpu_percent', 0.0) / 100.0
            capability_match = 1.0 if task_type in node.get('capabilities', []) else 0.0
            score = (0.5 * capability_match
                     + 0.3 * (1 - load_ratio)
                     + 0.2 * random.random())
            scored.append((node['node_id'], score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    async def find_nodes_for_split(self, task_type: str,
                                   num_needed: int) -> list[str]:
        candidates = await self.registry.get_nodes_by_capability(task_type)
        candidates = [n for n in candidates
                      if self.ws_manager and self.ws_manager.is_online(n['node_id'])]
        if not candidates:
            return []

        scored = []
        for node in candidates:
            load = await self.registry.get_load(node['node_id'])
            load_ratio = load.get('cpu_percent', 0.0) / 100.0
            score = 0.5 + 0.3 * (1 - load_ratio) + 0.2 * random.random()
            scored.append((node['node_id'], score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [nid for nid, _ in scored[:num_needed]]

    def should_split(self, content: str, task_type: str) -> bool:
        return len(content) > 1000

    def split_content(self, content: str, num_chunks: int) -> list[str]:
        paragraphs = content.split('\n\n')
        if len(paragraphs) >= num_chunks:
            chunk_size = len(paragraphs) // num_chunks
            chunks = []
            for i in range(num_chunks):
                start = i * chunk_size
                end = start + chunk_size if i < num_chunks - 1 else len(paragraphs)
                chunks.append('\n\n'.join(paragraphs[start:end]))
            return chunks

        # Fall back to sentence splitting
        sentences = content.replace('. ', '.\n').split('\n')
        if len(sentences) >= num_chunks:
            chunk_size = len(sentences) // num_chunks
            chunks = []
            for i in range(num_chunks):
                start = i * chunk_size
                end = start + chunk_size if i < num_chunks - 1 else len(sentences)
                chunks.append(' '.join(sentences[start:end]))
            return chunks

        # Last resort: split by characters
        char_size = len(content) // num_chunks
        chunks = []
        for i in range(num_chunks):
            start = i * char_size
            end = start + char_size if i < num_chunks - 1 else len(content)
            chunks.append(content[start:end])
        return chunks

    def merge_results(self, results: list[str]) -> str:
        return '\n\n'.join(chunk.strip() for chunk in results)
