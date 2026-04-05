# Demo: Use GemmaNet with the standard OpenAI Python SDK
# pip install openai
from openai import OpenAI

# Point OpenAI SDK to GemmaNet
client = OpenAI(
    base_url='https://api.gemmanet.net/v1',
    api_key='gn_your_api_key_here',
)

# Use it exactly like OpenAI
response = client.chat.completions.create(
    model='gemmanet/auto',
    messages=[
        {'role': 'user', 'content': 'Hello, GemmaNet!'}
    ],
)
print(response.choices[0].message.content)

# Also works with LangChain:
# from langchain_openai import ChatOpenAI
# llm = ChatOpenAI(base_url='https://api.gemmanet.net/v1',
#                   api_key='gn_xxx', model='gemmanet/auto')
# result = llm.invoke('Hello')
