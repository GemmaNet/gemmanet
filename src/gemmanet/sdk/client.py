"""Client class - developers use this to consume AI services."""
import json
from collections.abc import Iterator

import httpx

from gemmanet.sdk.exceptions import (
    AuthenticationError,
    GemmaNetError,
    NoNodeAvailableError,
    TaskTimeoutError,
)
from gemmanet.sdk.models import NodeInfo, TaskResult


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(data, dict):
        if isinstance(data.get('error'), dict):
            return str(data['error'].get('message', data['error']))
        if 'detail' in data:
            return str(data['detail'])
    return str(data)[:200]


def _check_response(resp: httpx.Response, task_endpoint: bool = True):
    """Raise the SDK exception matching an error response (body must be read).

    On task endpoints 404/503 mean "no node for this task"; elsewhere they are
    ordinary errors (e.g. rating an unknown task).
    """
    if resp.status_code < 400:
        return
    detail = _error_detail(resp)
    if resp.status_code == 401:
        raise AuthenticationError(detail or 'Invalid API key')
    if task_endpoint and resp.status_code in (404, 503):
        raise NoNodeAvailableError(detail or 'No node available for this task')
    if resp.status_code == 504:
        raise TaskTimeoutError(detail or 'Task timed out')
    raise GemmaNetError(f'HTTP {resp.status_code}: {detail}')


class Client:
    def __init__(self, api_key: str,
                 coordinator_url: str = 'http://localhost:8800'):
        self.api_key = api_key
        self.coordinator_url = coordinator_url
        self._client = httpx.Client(
            base_url=coordinator_url,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=30.0,
        )

    @staticmethod
    def _task_body(task: str, content: str, params: dict | None,
                   stream: bool = False) -> dict:
        return {
            'task_type': task,
            'content': content,
            'params': params or {},
            'stream': stream,
        }

    def request(self, task: str, content: str,
                params: dict | None = None,
                timeout: float = 90.0) -> TaskResult:
        try:
            resp = self._client.post('/api/v1/request',
                                     json=self._task_body(task, content, params),
                                     timeout=timeout)
        except httpx.TimeoutException:
            raise TaskTimeoutError('Request timed out') from None
        _check_response(resp)
        return TaskResult.model_validate(resp.json())

    async def request_async(self, task: str, content: str,
                            params: dict | None = None,
                            timeout: float = 90.0) -> TaskResult:
        async with httpx.AsyncClient(
            base_url=self.coordinator_url,
            headers={'Authorization': f'Bearer {self.api_key}'},
            timeout=timeout,
        ) as client:
            try:
                resp = await client.post(
                    '/api/v1/request', json=self._task_body(task, content, params))
            except httpx.TimeoutException:
                raise TaskTimeoutError('Request timed out') from None
            _check_response(resp)
            return TaskResult.model_validate(resp.json())

    def request_stream(self, task: str, content: str,
                       params: dict | None = None,
                       timeout: float = 90.0) -> Iterator[str]:
        """Send a request and yield the result text as the node produces it."""
        try:
            with self._client.stream(
                'POST', '/api/v1/request',
                json=self._task_body(task, content, params, stream=True),
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    _check_response(response)
                for line in response.iter_lines():
                    if not line.startswith('data: '):
                        continue
                    event = json.loads(line[6:])
                    if 'error' in event:
                        raise GemmaNetError(event['error'].get('message', 'Task failed'))
                    if event.get('done'):
                        return
                    if event.get('delta'):
                        yield event['delta']
        except httpx.TimeoutException:
            raise TaskTimeoutError('Request timed out') from None

    def rate(self, task_id: str, rating: int) -> dict:
        """Rate a task you requested (1-5 stars); feeds the node's reputation."""
        resp = self._client.post('/api/v1/rate',
                                 json={'task_id': task_id, 'rating': rating})
        _check_response(resp, task_endpoint=False)
        return resp.json()

    def nodes(self, capability: str | None = None) -> list[NodeInfo]:
        params = {}
        if capability:
            params['capability'] = capability
        resp = self._client.get('/api/v1/nodes', params=params)
        _check_response(resp)
        return [NodeInfo.model_validate(n) for n in resp.json()]

    def network_status(self) -> dict:
        resp = self._client.get('/api/v1/status')
        _check_response(resp)
        return resp.json()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
