from gemmanet import Client

# Use your API key from POST /api/v1/register
client = Client(api_key='gn_your_api_key_here')

# Check network status
print('Network status:', client.network_status())

# List available nodes
nodes = client.nodes()
print(f'Online nodes: {len(nodes)}')
for n in nodes:
    print(f'  - {n.name}: {n.capabilities}')

# Send a request
result = client.request(
    task='echo',
    content='Hello GemmaNet!',
    params={'prefix': 'Test'},
)
print(f'Status: {result.status.value}')
print(f'Result: {result.result}')
print(f'Node: {result.node_id}')

# Rate the node that served you (1-5); this feeds its reputation
client.rate(result.task_id, 5)

client.close()
