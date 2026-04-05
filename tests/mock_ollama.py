from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

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


@app.post('/api/chat')
async def chat(req: ChatRequest):
    user_msg = ''
    for m in req.messages:
        if m['role'] == 'user':
            user_msg = m['content']
    return {
        'message': {
            'role': 'assistant',
            'content': f'[MockOllama:{req.model}] Response to: {user_msg[:100]}'
        },
        'done': True
    }


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=11434)
