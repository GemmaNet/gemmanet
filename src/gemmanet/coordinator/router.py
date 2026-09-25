"""Routing engine: match requests to best nodes."""
import json
import os
import random

from gemmanet.coordinator.registry import NodeRegistry
from gemmanet.coordinator.reputation import ReputationSystem
from gemmanet.coordinator.ws_manager import WSConnectionManager

SPLIT_THRESHOLD_CHARS = 1000
# Only task types whose output for a chunk is independent of the other
# chunks can be split (translating paragraphs works; summarizing or chatting
# about a fragment does not). Comma-separated, configurable per deployment.
DEFAULT_SPLITTABLE_TASKS = 'translate'


def _splittable_from_env() -> frozenset[str]:
    raw = os.getenv('GEMMANET_SPLIT_TASKS', DEFAULT_SPLITTABLE_TASKS)
    return frozenset(t.strip() for t in raw.split(',') if t.strip())


class RoutingEngine:
    def __init__(self, registry: NodeRegistry, ws_manager: WSConnectionManager,
                 reputation: ReputationSystem | None = None,
                 splittable_tasks: set[str] | frozenset[str] | None = None):
        self.registry = registry
        self.ws_manager = ws_manager
        self.reputation = reputation
        self.splittable_tasks = (frozenset(splittable_tasks)
                                 if splittable_tasks is not None
                                 else _splittable_from_env())

    async def find_best_node(self, task_type: str, params: dict | None = None,
                             exclude: set[str] | None = None) -> str | None:
        candidates = await self.registry.get_nodes_by_capability(task_type)
        candidates = [n for n in candidates
                      if self.ws_manager and self.ws_manager.is_online(n['node_id'])
                      and n['node_id'] not in (exclude or ())]
        if not candidates:
            return None

        # Filter out suspended nodes
        if self.reputation:
            filtered = []
            for n in candidates:
                if not await self.reputation.is_suspended(n['node_id']):
                    filtered.append(n)
            candidates = filtered
            if not candidates:
                return None

        scored = []
        for node in candidates:
            load = await self.registry.get_load(node['node_id'])
            load_ratio = load.get('cpu_percent', 0.0) / 100.0
            capability_match = 1.0 if task_type in node.get('capabilities', []) else 0.0

            # Benchmark speed bonus
            bench_speed_bonus = 0.0
            bench_penalty = 0.0
            bench_data = await self.registry.redis.get(
                f'gn:bench:{node["node_id"]}')
            if bench_data:
                bench = json.loads(bench_data)
                avg_ms = bench.get('avg_response_ms', 5000)
                bench_speed_bonus = 1.0 - min(avg_ms / 10000.0, 1.0)
                if not bench.get('benchmark_passed', True):
                    bench_penalty = 0.2

            if self.reputation:
                rep_score = await self.reputation.get_score(node['node_id'])
                stats = await self.reputation.get_stats(node['node_id'])
                avg_response_ms = stats.get('avg_response_ms', 5000)
                speed_bonus = 1.0 - min(avg_response_ms / 10000.0, 1.0)
                score = (0.25 * capability_match
                         + 0.25 * rep_score / 100.0
                         + 0.15 * (1 - load_ratio)
                         + 0.1 * speed_bonus
                         + 0.15 * bench_speed_bonus
                         + 0.1 * random.random()
                         - bench_penalty)
            else:
                score = (0.4 * capability_match
                         + 0.2 * (1 - load_ratio)
                         + 0.2 * bench_speed_bonus
                         + 0.2 * random.random()
                         - bench_penalty)
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

        # Filter out suspended nodes
        if self.reputation:
            filtered = []
            for n in candidates:
                if not await self.reputation.is_suspended(n['node_id']):
                    filtered.append(n)
            candidates = filtered
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
        return (task_type in self.splittable_tasks
                and len(content) > SPLIT_THRESHOLD_CHARS)

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
