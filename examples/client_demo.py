from gemmanet import Client

# Create client with demo API key
client = Client(api_key='demo-key-001')

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
print(f'Result: {result.result}')
print(f'Cost: {result.cost} credits')
print(f'Node: {result.node_id}')

# Check balance
print(f'Balance: {client.balance()} credits')

client.close()
