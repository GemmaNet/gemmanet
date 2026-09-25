"""Minimal stand-in for the Ollama HTTP API (chat, streaming, tags)."""
import json
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False
    options: dict = {}


@app.get('/api/tags')
async def list_models():
    return {'models': [
        {'name': 'gemma2:9b', 'size': 5000000000},
        {'name': 'llama3:8b', 'size': 4500000000},
    ]}


def _answer(req: ChatRequest) -> str:
    user_msg = ''
    for m in req.messages:
        if m['role'] == 'user':
            user_msg = m['content']
    return (f'[MockOllama:{req.model}] turns={len(req.messages)} '
            f'Response to: {user_msg[:100]}')


@app.post('/api/chat')
async def chat(req: ChatRequest):
    answer = _answer(req)
    usage = {'prompt_eval_count': 7, 'eval_count': len(answer.split())}
    if not req.stream:
        return {'message': {'role': 'assistant', 'content': answer}, 'done': True, **usage}

    def lines():
        for word in answer.split(' '):
            yield json.dumps({'message': {'role': 'assistant', 'content': word + ' '},
                              'done': False}) + '\n'
        yield json.dumps({'message': {'role': 'assistant', 'content': ''},
                          'done': True, **usage}) + '\n'

    return StreamingResponse(lines(), media_type='application/x-ndjson')


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
