"""Demo: Streaming responses from GemmaNet"""
from gemmanet import Client

client = Client(api_key='gn_your_key')

print('Streaming response:')
for chunk in client.request_stream('chat', 'Tell me about AI'):
    print(chunk, end='', flush=True)
print()  # newline at end

client.close()
