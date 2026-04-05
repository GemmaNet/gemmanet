"""GemmaNet Demo: Multi-Node Translation Service.

Starts coordinator + 3 translation nodes, then sends requests.
"""
import subprocess
import sys
import os
import time


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_process(cmd, label):
    """Start a subprocess with proper env."""
    print(f'Starting {label}...')
    venv = os.path.join(PROJECT_ROOT, '.venv')
    env = {
        **os.environ,
        'VIRTUAL_ENV': venv,
        'PATH': os.path.join(venv, 'bin') + ':' + os.environ['PATH'],
    }
    return subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env)


def main():
    procs = []

    print('==========================================')
    print('  GemmaNet Demo: Multi-Node Translation')
    print('==========================================')
    print()

    # 1. Start coordinator
    coord = start_process(
        [sys.executable, '-m', 'uvicorn',
         'gemmanet.coordinator.server:app',
         '--host', '0.0.0.0', '--port', '8800'],
        'Coordinator')
    procs.append(coord)
    time.sleep(3)

    # 2. Start 3 translation nodes with different specialties
    node_scripts = [
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
        'n = Node(name="general-translator", capabilities=["translate","echo"], languages=["en","zh","ja","ko"])\n'
        'n.register_handler("translate", handler)\n'
        'n.register_handler("echo", lambda c, **p: f"Echo: {c}")\n'
        'n.start()',
    ]

    for i, script in enumerate(node_scripts):
        p = start_process([sys.executable, '-c', script],
                          f'Node {i + 1}')
        procs.append(p)

    time.sleep(5)  # Wait for all nodes to connect

    # 3. Use Client to interact
    from gemmanet import Client
    import httpx

    # Show network status
    resp = httpx.get('http://localhost:8800/api/v1/status')
    status = resp.json()
    print(f'\nNetwork Status:')
    print(f'  Online nodes: {status["online_nodes"]}')
    print()

    # Show available nodes
    resp = httpx.get('http://localhost:8800/api/v1/nodes')
    nodes = resp.json()
    print('Available Nodes:')
    for n in nodes:
        print(f'  - {n["name"]}: capabilities={n["capabilities"]}, '
              f'languages={n["languages"]}')
    print()

    # Send requests
    client = Client(api_key='demo-user')
    print(f'Client balance: {client.balance()} credits')
    print()

    # Request 1: Short translation
    print('--- Request 1: Short Translation ---')
    r1 = client.request(task='translate',
                        content='Hello world, this is GemmaNet!',
                        params={'source_lang': 'en', 'target_lang': 'zh'})
    print(f'Result: {r1.result}')
    print(f'Cost: {r1.cost} credits, Node: {r1.node_id}')
    print()

    # Request 2: Echo test
    print('--- Request 2: Echo Test ---')
    r2 = client.request(task='echo', content='Testing echo!')
    print(f'Result: {r2.result}')
    print(f'Cost: {r2.cost} credits')
    print()

    # Request 3: Long text (should trigger split)
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
    print(f'Cost: {r3.cost} credits')
    print()

    # Final balance
    print(f'Final balance: {client.balance()} credits')
    print(f'Transactions: {len(client.history())}')
    print()

    # Dashboard info
    print('==========================================')
    print('  Dashboard: http://localhost:8800/dashboard/')
    print('  API Docs:  http://localhost:8800/docs')
    print('==========================================')
    print()
    print('Press Ctrl+C to stop all services...')

    client.close()

    try:
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
