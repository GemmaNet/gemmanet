import os
import pytest
import asyncio
from gemmanet.coordinator.reputation import ReputationSystem

TEST_REDIS = os.getenv('TEST_REDIS_URL', os.getenv('REDIS_URL', 'redis://localhost:6379/1'))


async def make_rep():
    """Create a fresh ReputationSystem and flush the test DB."""
    system = ReputationSystem(redis_url=TEST_REDIS)
    await system.redis.flushdb()
    return system


def test_new_node_default_score():
    async def run():
        rep = await make_rep()
        score = await rep.get_score('new-node')
        return score
    score = asyncio.run(run())
    assert score == 50  # default for new nodes


def test_score_improves_with_success():
    async def run():
        rep = await make_rep()
        for i in range(15):
            await rep.record_task_result('good-node', success=True,
                response_time_ms=500)
        score = await rep.get_score('good-node')
        return score
    score = asyncio.run(run())
    assert score > 60  # should be above neutral


def test_score_drops_with_failures():
    async def run():
        rep = await make_rep()
        for i in range(15):
            await rep.record_task_result('bad-node', success=False,
                response_time_ms=60000)
        score = await rep.get_score('bad-node')
        return score
    score = asyncio.run(run())
    assert score < 40  # should be below neutral


def test_user_rating():
    async def run():
        rep = await make_rep()
        for i in range(5):
            await rep.record_user_rating('rated-node', 5)
        stats = await rep.get_stats('rated-node')
        return stats
    stats = asyncio.run(run())
    assert stats['avg_rating'] == 5.0
    assert stats['total_ratings'] == 5


def test_suspension():
    async def run():
        rep = await make_rep()
        for i in range(15):
            await rep.record_task_result('terrible-node', success=False,
                response_time_ms=60000)
        await rep.check_and_suspend('terrible-node')
        return await rep.is_suspended('terrible-node')
    suspended = asyncio.run(run())
    assert suspended is True


def test_leaderboard():
    async def run():
        rep = await make_rep()
        for i in range(3):
            node_id = f'lb-node-{i}'
            for j in range(10):
                await rep.record_task_result(node_id, success=True,
                    response_time_ms=1000 * (i + 1))
        lb = await rep.get_leaderboard(limit=10)
        return lb
    lb = asyncio.run(run())
    assert len(lb) >= 3
    # First should have highest score (fastest)
    assert lb[0]['score'] >= lb[1]['score']
