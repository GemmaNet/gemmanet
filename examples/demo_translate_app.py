"""GemmaNet Demo: Multi-Node Translation Service.

Starts coordinator + 3 translation nodes, then sends requests.
Needs PostgreSQL + Redis (DATABASE_URL / REDIS_URL, e.g. in .env).
"""
import os
import subprocess
import sys
import time

import httpx

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = 'http://localhost:8800'


def start_process(cmd, label, extra_env=None):
    """Start a subprocess with proper env."""
    print(f'Starting {label}...')
    env = {**os.environ, **(extra_env or {})}
    return subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env)


NODE_SCRIPTS = [
    # Node 1: EN-ZH
    'import logging; logging.basicConfig(level=logging.INFO)\n'
    'from gemmanet import Node\n'
    'def handler(content, source_lang="en", target_lang="zh", **kw):\n'
    '    return f"[EN->ZH by Node1] {content}"\n'
    'n = Node(name="zh-specialist", capabilities=["translate"], languages=["en","zh"])\n'
    'n.register_handler("translate", handler)\n'
    'n.start()',
    # Node 2: EN-JA
    'import logging; logging.basicConfig(level=logging.INFO)\n'
    'from gemmanet import Node\n'
    'def handler(content, source_lang="en", target_lang="ja", **kw):\n'
    '    return f"[EN->JA by Node2] {content}"\n'
    'n = Node(name="ja-specialist", capabilities=["translate"], languages=["en","ja"])\n'
    'n.register_handler("translate", handler)\n'
    'n.start()',
    # Node 3: General
    'import logging; logging.basicConfig(level=logging.INFO)\n'
    'from gemmanet import Node\n'
    'def handler(content, source_lang="en", target_lang="any", **kw):\n'
    '    return f"[General Node3 {source_lang}->{target_lang}] {content}"\n'
    'n = Node(name="general-translator", capabilities=["translate","echo"],'
    ' languages=["en","zh","ja","ko"])\n'
    'n.register_handler("translate", handler)\n'
    'n.register_handler("echo", lambda c, **p: f"Echo: {c}")\n'
    'n.start()',
]


def main():
    procs = []

    print('==========================================')
    print('  GemmaNet Demo: Multi-Node Translation')
    print('==========================================')
    print()

    # 1. Start coordinator
    procs.append(start_process(
        [sys.executable, '-m', 'uvicorn', 'gemmanet.coordinator.server:app',
         '--host', '0.0.0.0', '--port', '8800'],
        'Coordinator'))
    time.sleep(3)

    try:
        # 2. One API key: nodes authenticate with it, the client uses it too
        api_key = httpx.post(f'{BASE_URL}/api/v1/register', json={}).json()['api_key']

        # 3. Start 3 translation nodes with different specialties
        for i, script in enumerate(NODE_SCRIPTS):
            procs.append(start_process([sys.executable, '-c', script], f'Node {i + 1}',
                                       {'GEMMANET_API_KEY': api_key}))
        time.sleep(5)  # Wait for all nodes to connect

        # 4. Use Client to interact
        from gemmanet import Client

        status = httpx.get(f'{BASE_URL}/api/v1/status').json()
        print('\nNetwork Status:')
        print(f'  Online nodes: {status["online_nodes"]}')
        print()

        print('Available Nodes:')
        for n in httpx.get(f'{BASE_URL}/api/v1/nodes').json():
            print(f'  - {n["name"]}: capabilities={n["capabilities"]}, '
                  f'languages={n["languages"]}')
        print()

        client = Client(api_key=api_key, coordinator_url=BASE_URL)

        print('--- Request 1: Short Translation ---')
        r1 = client.request(task='translate',
                            content='Hello world, this is GemmaNet!',
                            params={'source_lang': 'en', 'target_lang': 'zh'})
        print(f'Result: {r1.result}')
        print(f'Node: {r1.node_id}')
        client.rate(r1.task_id, 5)
        print()

        print('--- Request 2: Echo Test ---')
        r2 = client.request(task='echo', content='Testing echo!')
        print(f'Result: {r2.result}')
        print()

        print('--- Request 3: Long Text Translation (split test) ---')
        long_text = '\n\n'.join([
            f'Paragraph {i + 1}: This is a test paragraph for the GemmaNet '
            f'multi-node translation demo. Each paragraph demonstrates how '
            f'the platform splits long texts across multiple nodes for '
            f'parallel processing, making translation faster.'
            for i in range(5)
        ])
        r3 = client.request(task='translate', content=long_text,
                            params={'source_lang': 'en', 'target_lang': 'zh'})
        print(f'Result (first 200 chars): {r3.result[:200]}...')
        print()

        client.close()

        print('==========================================')
        print(f'  Dashboard: {BASE_URL}/dashboard/')
        print(f'  API Docs:  {BASE_URL}/docs')
        print('==========================================')
        print()
        print('Press Ctrl+C to stop all services...')
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nShutting down...')
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=5)
        print('All services stopped.')


if __name__ == '__main__':
    main()
