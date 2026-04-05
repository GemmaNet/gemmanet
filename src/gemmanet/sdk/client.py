"""Client class - developers use this to consume AI services."""
import json
from typing import Iterator

import httpx

from gemmanet.sdk.models import TaskResult, NodeInfo
from gemmanet.sdk.exceptions import (
    AuthenticationError,
    InsufficientCreditsError,
    NoNodeAvailableError,
    GemmaNetError,
    TaskTimeoutError,
)


def _check_response(resp: httpx.Response):
    if resp.status_code == 401:
        raise AuthenticationError('Invalid API key')
    if resp.status_code == 402:
        raise InsufficientCreditsError('Not enough credits')
    if resp.status_code == 404:
        raise NoNodeAvailableError('No node available for this task')
    if resp.status_code >= 500:
        raise GemmaNetError(f'Server error: {resp.text}')
    resp.raise_for_status()


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

    def request(self, task: str, content: str,
                params: dict | None = None,
                max_cost: int | None = None,
                timeout: float = 60.0) -> TaskResult:
        body = {
            'task_type': task,
            'content': content,
            'params': params or {},
            'max_cost': max_cost,
            'api_key': self.api_key,
        }
        try:
            resp = self._client.post('/api/v1/request', json=body,
                                     timeout=timeout)
        except httpx.TimeoutException:
            raise TaskTimeoutError('Request timed out')
        _check_response(resp)
        return TaskResult.model_validate(resp.json())

    async def request_async(self, task: str, content: str,
                            params: dict | None = None,
                            max_cost: int | None = None,
                            timeout: float = 60.0) -> TaskResult:
        body = {
            'task_type': task,
            'content': content,
            'params': params or {},
            'max_cost': max_cost,
            'api_key': self.api_key,
        }
        async with httpx.AsyncClient(
            base_url=self.coordinator_url,
            headers={'Authorization': f'Bearer {self.api_key}'},
            timeout=timeout,
        ) as client:
            try:
                resp = await client.post('/api/v1/request', json=body)
            except httpx.TimeoutException:
                raise TaskTimeoutError('Request timed out')
            _check_response(resp)
            return TaskResult.model_validate(resp.json())

    def request_stream(self, task: str, content: str,
                       params: dict | None = None) -> Iterator[str]:
        """Send request and receive streaming response."""
        messages = [{'role': 'user', 'content': content}]
        with self._client.stream(
            'POST', '/v1/chat/completions',
            json={'model': f'gemmanet/{task}', 'messages': messages,
                  'stream': True},
        ) as response:
            _check_response(response)
            for line in response.iter_lines():
                if line.startswith('data: '):
                    data = line[6:]
                    if data == '[DONE]':
                        return
                    chunk = json.loads(data)
                    chunk_content = chunk['choices'][0]['delta'].get('content', '')
                    if chunk_content:
                        yield chunk_content

    def balance(self) -> int:
        resp = self._client.get('/api/v1/balance')
        _check_response(resp)
        return resp.json().get('balance', 0)

    def nodes(self, capability: str | None = None) -> list[NodeInfo]:
        params = {}
        if capability:
            params['capability'] = capability
        resp = self._client.get('/api/v1/nodes', params=params)
        _check_response(resp)
        return [NodeInfo.model_validate(n) for n in resp.json()]

    def history(self, limit: int = 20) -> list[dict]:
        resp = self._client.get('/api/v1/history', params={'limit': limit})
        _check_response(resp)
        return resp.json()

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
