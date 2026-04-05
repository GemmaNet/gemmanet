"""Reputation system: tracks and scores node reputation based on performance."""
import time
import json
import redis.asyncio as aioredis
import logging

logger = logging.getLogger('gemmanet.reputation')


class ReputationSystem:
    """Tracks and scores node reputation based on performance."""

    def __init__(self, redis_url: str = 'redis://localhost:6379/0'):
        self.redis = aioredis.from_url(redis_url, decode_responses=True)
        self.prefix = 'gn:rep'

    async def record_task_result(self, node_id: str, success: bool,
                                  response_time_ms: int):
        """Record a completed task for reputation calculation."""
        key = f'{self.prefix}:stats:{node_id}'
        pipe = self.redis.pipeline()
        pipe.hincrby(key, 'total_tasks', 1)
        if success:
            pipe.hincrby(key, 'successful_tasks', 1)
        else:
            pipe.hincrby(key, 'failed_tasks', 1)
        pipe.hincrby(key, 'total_response_time_ms', response_time_ms)
        pipe.hset(key, 'last_task_at', str(int(time.time())))
        await pipe.execute()

        # Recalculate avg response time
        stats = await self.redis.hgetall(key)
        total = int(stats.get('total_tasks', 0))
        total_rt = int(stats.get('total_response_time_ms', 0))
        if total > 0:
            await self.redis.hset(key, 'avg_response_time_ms', str(total_rt // total))

    async def record_user_rating(self, node_id: str, rating: int):
        """Record a user rating (1-5 stars) for a node."""
        key = f'{self.prefix}:ratings:{node_id}'
        await self.redis.lpush(key, str(rating))
        await self.redis.ltrim(key, 0, 99)  # keep last 100

    async def get_score(self, node_id: str) -> float:
        """Get reputation score (0-100) for a node."""
        stats_key = f'{self.prefix}:stats:{node_id}'
        stats = await self.redis.hgetall(stats_key)

        if not stats or int(stats.get('total_tasks', 0)) == 0:
            return 50.0  # default for new nodes

        total = int(stats.get('total_tasks', 0))
        successful = int(stats.get('successful_tasks', 0))
        avg_rt = int(stats.get('avg_response_time_ms', 5000))
        last_task_at = int(stats.get('last_task_at', 0))

        # Completion rate (0-1)
        completion_rate = successful / total if total > 0 else 0.0

        # Speed score: <1s = 1.0, >10s = 0.0
        speed_score = 1.0 - min(avg_rt / 10000.0, 1.0)

        # Uptime score: based on recency of last task
        now = time.time()
        age_hours = (now - last_task_at) / 3600.0 if last_task_at > 0 else 999
        uptime_score = max(0.0, 1.0 - (age_hours / 24.0))  # decays over 24h

        # Rating score
        ratings_key = f'{self.prefix}:ratings:{node_id}'
        ratings = await self.redis.lrange(ratings_key, 0, -1)
        if ratings:
            avg_rating = sum(int(r) for r in ratings) / len(ratings)
            rating_score = avg_rating / 5.0
        else:
            rating_score = 0.5  # neutral if no ratings

        score = (
            completion_rate * 40
            + speed_score * 20
            + uptime_score * 20
            + rating_score * 20
        )

        return round(min(100.0, max(0.0, score)), 1)

    async def get_stats(self, node_id: str) -> dict:
        """Get full reputation stats for a node."""
        stats_key = f'{self.prefix}:stats:{node_id}'
        stats = await self.redis.hgetall(stats_key)

        total = int(stats.get('total_tasks', 0))
        successful = int(stats.get('successful_tasks', 0))
        avg_rt = int(stats.get('avg_response_time_ms', 0))

        ratings_key = f'{self.prefix}:ratings:{node_id}'
        ratings = await self.redis.lrange(ratings_key, 0, -1)
        avg_rating = (sum(int(r) for r in ratings) / len(ratings)) if ratings else 0.0
        total_ratings = len(ratings)

        score = await self.get_score(node_id)

        return {
            'score': score,
            'total_tasks': total,
            'success_rate': round(successful / total, 3) if total > 0 else 0.0,
            'avg_response_ms': avg_rt,
            'avg_rating': round(avg_rating, 2),
            'total_ratings': total_ratings,
        }

    async def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Get top nodes by reputation score."""
        cursor = 0
        entries = []
        while True:
            cursor, keys = await self.redis.scan(
                cursor=cursor, match=f'{self.prefix}:stats:*', count=100)
            for key in keys:
                node_id = key.split(':')[-1]
                score = await self.get_score(node_id)
                stats = await self.get_stats(node_id)
                entries.append({'node_id': node_id, **stats})
            if not cursor or str(cursor) == '0':
                break

        entries.sort(key=lambda x: x['score'], reverse=True)
        return entries[:limit]

    async def is_suspended(self, node_id: str) -> bool:
        """Check if node is suspended due to low reputation."""
        val = await self.redis.get(f'{self.prefix}:suspended:{node_id}')
        return val is not None

    async def check_and_suspend(self, node_id: str):
        """Suspend node if reputation is too low."""
        score = await self.get_score(node_id)
        stats = await self.get_stats(node_id)
        if score < 35 and stats.get('total_tasks', 0) > 10:
            await self.redis.setex(
                f'{self.prefix}:suspended:{node_id}', 86400, '1')
            logger.warning(f'Node {node_id} suspended: score={score}')
