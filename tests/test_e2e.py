"""GemmaNet End-to-End Test - standalone script."""
import subprocess
import time
import httpx
import sys
import os


def main():
    print('=== GemmaNet End-to-End Test ===')

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv = os.path.join(project_root, '.venv')
    env = {
        **os.environ,
        'VIRTUAL_ENV': venv,
        'PATH': os.path.join(venv, 'bin') + ':' + os.environ['PATH'],
    }
    cwd = project_root

    # Step 1: Start coordinator in background
    print('Starting coordinator...')
    coord_proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn',
         'gemmanet.coordinator.server:app',
         '--host', '0.0.0.0', '--port', '8800'],
        cwd=cwd, env=env,
    )
    time.sleep(3)

    try:
        # Step 2: Verify coordinator is running
        resp = httpx.get('http://localhost:8800/api/v1/status')
        assert resp.status_code == 200
        status = resp.json()
        print(f'Coordinator running: {status}')
        assert status['online_nodes'] == 0

        # Step 3: Start echo node in background
        print('Starting echo node...')
        node_proc = subprocess.Popen(
            [sys.executable, 'examples/echo_node.py'],
            cwd=cwd, env=env,
        )
        time.sleep(3)

        try:
            # Step 4: Verify node is registered
            resp = httpx.get('http://localhost:8800/api/v1/nodes')
            nodes = resp.json()
            print(f'Online nodes: {len(nodes)}')
            assert len(nodes) >= 1, f'Expected at least 1 node, got {len(nodes)}'
            print(f'Node: {nodes[0]}')

            # Step 5: Send request via Client
            print('Sending echo request...')
            from gemmanet import Client
            client = Client(api_key='e2e-test-client')
            result = client.request(task='echo', content='Hello GemmaNet E2E!')
            print(f'Result: {result.result}')
            print(f'Cost: {result.cost}')
            print(f'Node: {result.node_id}')
            assert result.result is not None
            assert 'Hello GemmaNet E2E' in result.result
            assert result.cost > 0

            # Step 6: Check balance
            balance = client.balance()
            print(f'Client balance: {balance}')
            assert balance < 1000  # Should have spent some credits

            # Step 7: Check transaction history
            history = client.history()
            print(f'Transaction count: {len(history)}')
            assert len(history) >= 1

            # Step 8: Check network status updated
            resp = httpx.get('http://localhost:8800/api/v1/status')
            status = resp.json()
            print(f'Final status: {status}')
            assert status['online_nodes'] >= 1
            assert status['total_tasks_today'] >= 1

            client.close()

        finally:
            node_proc.terminate()
            node_proc.wait(timeout=5)

    finally:
        coord_proc.terminate()
        coord_proc.wait(timeout=5)

    print('\n=== ALL E2E TESTS PASSED ===')


if __name__ == '__main__':
    main()
