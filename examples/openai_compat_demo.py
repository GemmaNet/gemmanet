# Demo: Use GemmaNet with the standard OpenAI Python SDK
# pip install openai
from openai import OpenAI

# Point OpenAI SDK to GemmaNet
client = OpenAI(
    base_url='https://api.gemmanet.net/v1',
    api_key='gn_your_api_key_here',
)

# Use it exactly like OpenAI - the whole conversation is forwarded
response = client.chat.completions.create(
    model='gemmanet/auto',
    messages=[
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': 'Hello, GemmaNet!'},
    ],
)
print(response.choices[0].message.content)

# Streaming works too: tokens arrive as the node generates them
for chunk in client.chat.completions.create(
    model='gemmanet/auto',
    messages=[{'role': 'user', 'content': 'Tell me a short story'}],
    stream=True,
):
    print(chunk.choices[0].delta.content or '', end='', flush=True)
print()

# Also works with LangChain:
# from langchain_openai import ChatOpenAI
# llm = ChatOpenAI(base_url='https://api.gemmanet.net/v1',
#                   api_key='gn_xxx', model='gemmanet/auto')
# result = llm.invoke('Hello')
