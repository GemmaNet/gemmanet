"""Post-deploy smoke check for a GemmaNet site.

Checks the main site (--base: website, docs) and the coordinator
(--api-base: dashboard, forum, APIs) and, given an API key, runs a real task
end to end: a temporary node connects over WebSocket, and a request, a stream
and an OpenAI-style call are routed to it.

    # website on Cloudflare Pages, coordinator on the VM
    python scripts/smoke_check.py --base https://gemmanet.net \\
        --api-base https://api.gemmanet.net --api-key gn_...
    # coordinator only
    python scripts/smoke_check.py --api-base https://api.gemmanet.net --api-key gn_...
    # everything on one host
    python scripts/smoke_check.py --base http://localhost --register

Exits non-zero if any check fails.
"""
import argparse
import sys
import threading
import time

import httpx

from gemmanet import Client, Node

SLOGAN = 'The Open Network for AI Services'
SMOKE_CAPABILITY = 'smoke-echo'  # unique, so real traffic never lands on the smoke node


class Report:
    def __init__(self):
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn):
        try:
            detail = fn() or ''
            self.results.append((name, True, detail))
        except Exception as e:
            self.results.append((name, False, f'{type(e).__name__}: {e}'))

    def print(self) -> bool:
        width = max(len(name) for name, _, _ in self.results)
        for name, ok, detail in self.results:
            print(f'{"PASS" if ok else "FAIL"}  {name.ljust(width)}  {detail}')
        failed = sum(not ok for _, ok, _ in self.results)
        print(f'\n{len(self.results) - failed} passed, {failed} failed')
        return failed == 0


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def get(http: httpx.Client, url: str, **kwargs) -> httpx.Response:
    resp = http.get(url, **kwargs)
    expect(resp.status_code == 200, f'HTTP {resp.status_code} for {url}')
    return resp


def smoke_handler(content, **params):
    for piece in ('pong', ':', content):
        time.sleep(0.4)  # spaced out so buffering anywhere on the path shows up
        yield piece


def run_end_to_end(report: Report, base: str, api_key: str):
    ws_url = base.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws/node'
    node = Node(name='smoke-check', capabilities=[SMOKE_CAPABILITY],
                coordinator_url=ws_url, api_key=api_key)
    node.register_handler(SMOKE_CAPABILITY, smoke_handler)
    errors = []

    def run():
        try:
            node.start()
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        def node_connects():
            expect(node._registered.wait(20), f'node did not register: {errors or "timeout"}')
            return f'node_id={node.node_id}'
        report.check('node connects over WebSocket', node_connects)
        if not node._registered.is_set():
            return

        with Client(api_key=api_key, coordinator_url=base) as client:
            def request():
                result = client.request(SMOKE_CAPABILITY, 'ping')
                expect(result.status.value == 'completed', f'status {result.status.value}')
                expect(result.result == 'pong:ping', f'result {result.result!r}')
                return f'{result.processing_time_ms} ms'
            report.check('task request round trip', request)

            def stream():
                start = time.monotonic()
                arrivals, pieces = [], []
                for piece in client.request_stream(SMOKE_CAPABILITY, 'ping'):
                    pieces.append(piece)
                    arrivals.append(time.monotonic() - start)
                expect(''.join(pieces) == 'pong:ping', f'stream {pieces!r}')
                expect(len(pieces) == 3, f'{len(pieces)} pieces (expected 3)')
                # Unbuffered: the first piece arrives well before the last.
                expect(arrivals[-1] - arrivals[0] > 0.5,
                       f'pieces arrived together ({arrivals}); something buffers the stream')
                return f'{len(pieces)} pieces over {arrivals[-1] - arrivals[0]:.1f}s'
            report.check('streaming is incremental', stream)

        def openai_compatible():
            resp = httpx.post(f'{base}/v1/chat/completions', timeout=30,
                              headers={'Authorization': f'Bearer {api_key}'},
                              json={'model': f'gemmanet/{SMOKE_CAPABILITY}',
                                    'messages': [{'role': 'user', 'content': 'hi'}]})
            expect(resp.status_code == 200, f'HTTP {resp.status_code}: {resp.text[:200]}')
            content = resp.json()['choices'][0]['message']['content']
            expect(content == 'pong:hi', f'content {content!r}')
        report.check('OpenAI-compatible chat completion', openai_compatible)
    finally:
        node.stop()
        thread.join(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', help='main site (website + docs), e.g. https://gemmanet.net')
    parser.add_argument('--api-base',
                        help='coordinator (API, dashboard, forum), e.g. https://api.gemmanet.net; '
                             'defaults to --base')
    parser.add_argument('--api-key', help='API key for the end-to-end checks')
    parser.add_argument('--register', action='store_true',
                        help='register a new API key for the end-to-end checks')
    parser.add_argument('--admin-key', help='ADMIN_KEY, to check the feedback endpoint')
    parser.add_argument('--version', help='expected coordinator version')
    parser.add_argument('--no-redirect-check', action='store_true',
                        help='skip checking that the main site forwards API paths '
                             '(for static servers that ignore _redirects)')
    args = parser.parse_args()
    if not args.base and not args.api_base:
        parser.error('give --base, --api-base or both')
    base = args.base.rstrip('/') if args.base else None
    api = (args.api_base or args.base).rstrip('/')
    report = Report()
    http = httpx.Client(timeout=20, follow_redirects=True)

    if base:
        def website():
            resp = get(http, f'{base}/')
            expect(SLOGAN in resp.text, 'slogan missing: is this still the old site?')
        report.check('website', website)

        def docs():
            expect('GemmaNet' in get(http, f'{base}/docs/').text, 'unexpected docs page')
        report.check('docs', docs)

    def dashboard():
        expect(SLOGAN in get(http, f'{api}/dashboard/').text, 'unexpected dashboard page')
    report.check('dashboard', dashboard)

    report.check('forum', lambda: get(http, f'{api}/talk/') and None)

    def status():
        data = get(http, f'{api}/api/v1/status').json()
        expect(data.get('status') == 'running', f'status {data}')
        if args.version:
            expect(data.get('version') == args.version, f'version {data.get("version")}')
        return f'version {data.get("version")}, {data.get("online_nodes")} nodes online'
    report.check('coordinator status', status)

    def models():
        ids = [m['id'] for m in get(http, f'{api}/v1/models').json()['data']]
        expect('gemmanet/auto' in ids, f'models {ids}')
    report.check('OpenAI models endpoint', models)

    if base and base != api:
        site_origin = '/'.join(base.split('/')[:3])

        def cross_origin():
            # The homepage fetches the forum preview from the API origin.
            resp = get(http, f'{api}/talk/api/recent', headers={'Origin': site_origin})
            allowed = resp.headers.get('access-control-allow-origin')
            expect(allowed in ('*', site_origin), f'access-control-allow-origin: {allowed}')
        report.check('website may call the API (CORS)', cross_origin)

        if not args.no_redirect_check:
            def forwards_api_paths():
                data = get(http, f'{base}/api/v1/status').json()
                expect(data.get('status') == 'running', f'status {data}')
            report.check('main site forwards API paths', forwards_api_paths)

    if args.admin_key:
        def feedback_admin():
            expect(http.get(f'{api}/api/v1/feedback').status_code == 401,
                   'feedback readable without ADMIN_KEY')
            get(http, f'{api}/api/v1/feedback',
                headers={'Authorization': f'Bearer {args.admin_key}'})
        report.check('feedback requires ADMIN_KEY', feedback_admin)

    api_key = args.api_key
    if args.register and not api_key:
        def register():
            nonlocal api_key
            resp = http.post(f'{api}/api/v1/register', json={})
            expect(resp.status_code == 200, f'HTTP {resp.status_code}: {resp.text[:200]}')
            api_key = resp.json()['api_key']
            return f'key {api_key[:11]}... (save it: registration is limited to 5/hour)'
        report.check('register API key', register)

    if api_key:
        run_end_to_end(report, api, api_key)
    else:
        print('(no --api-key/--register: skipping the end-to-end checks)\n')

    return 0 if report.print() else 1


if __name__ == '__main__':
    sys.exit(main())
