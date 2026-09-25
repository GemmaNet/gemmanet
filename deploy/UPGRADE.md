# Upgrading a coordinator from 0.1.0a1 to 0.2.0a1

0.2.0a1 removes the credit system, requires API keys for nodes and runs the
coordinator as a single process. Paths below follow the layout the Caddy
configs expect (`/opt/gemmanet`); adjust them to your setup.

**Downtime:** a few minutes while the coordinator restarts; nodes reconnect
on their own once they have an API key.

## 1. Before you start

- Every node needs an API key after the upgrade. Old node scripts that don't
  set `GEMMANET_API_KEY` (or pass `api_key=`) will be rejected.
- Keep the coordinator to **one process**: remove `--workers` from your
  service definition if you use it. A second instance refuses to start.

## 2. Back up

```bash
cd /opt/gemmanet
mkdir -p backups
pg_dump "$DATABASE_URL" > backups/gemmanet-$(date +%F).sql
cp "${FORUM_DB:-forum.db}" backups/forum-$(date +%F).db   # the forum migrates its data on first start
```

## 3. Update the code

```bash
cd /opt/gemmanet
git pull
source .venv/bin/activate
pip install -e ".[server]"        # the coordinator's dependencies moved into [server]
```

## 4. Configure the environment (`.env`)

| Variable | Required | Notes |
|----------|----------|-------|
| `DATABASE_URL` | yes | unchanged; `postgresql://...` works as before |
| `REDIS_URL` | yes | unchanged |
| `ADMIN_KEY` | to read feedback | **new requirement**: without it `GET /api/v1/feedback` always returns 401. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `COORDINATOR_URL` | for the dashboard | public URL the dashboard calls, e.g. `https://api.gemmanet.net` |
| `GEMMANET_TASK_TIMEOUT` | no | seconds per task (default 60) |
| `GEMMANET_STREAM_MAX_SECONDS` | no | cap for a streamed task (default 600) |
| `GEMMANET_SPLIT_TASKS` | no | task types that may be split (default `translate`) |
| `FORUM_DB` | no | forum SQLite file (default `forum.db` in the working directory) |

## 5. Restart the coordinator

Stop the seed nodes and the old coordinator, then start a single process:

```bash
uvicorn gemmanet.coordinator.server:app --host 127.0.0.1 --port 8800
```

Binding to `127.0.0.1` keeps port 8800 behind Caddy, which also makes the
client IPs used for rate limits trustworthy. On first start the coordinator
creates any missing tables (the existing `api_keys` and `feedback` tables are
reused as-is) and migrates the forum's stored text once.

### Optional: reset reputation data

0.1 gave nodes a random id on every start, so the reputation stored so far
belongs to ids that will never come back and would linger on the leaderboard.
To start clean:

```bash
for pattern in 'gn:rep:*' 'gn:bench:*' 'gn:task:node:*'; do
  redis-cli -u "$REDIS_URL" --scan --pattern "$pattern" | xargs -r redis-cli -u "$REDIS_URL" del
done
```

## 6. Give your nodes an API key

```bash
curl -X POST https://api.gemmanet.net/api/v1/register
# {"api_key": "gn_...", "account_id": "..."}   (limited to 5 per hour per IP)
```

One key can serve all your nodes; each node's id comes from the key's account
plus the node name, so give every node a distinct name (the seed scripts
already do). Start the seed nodes with the key:

```bash
export GEMMANET_API_KEY=gn_...
export GEMMANET_COORDINATOR=wss://api.gemmanet.net/ws/node
python seeds/chat_seed.py &
python seeds/echo_seed.py &
python seeds/translate_seed.py &
```

## 7. Update Caddy and the static sites

- The Caddy configs now route `/v1/*` (the OpenAI-compatible API) on the main
  domain. Copy the config you use and reload:
  `sudo cp deploy/Caddyfile.full /etc/caddy/Caddyfile && sudo systemctl reload caddy`
- Rebuild the docs Caddy serves from `/opt/gemmanet/site`:
  `pip install mkdocs-material && mkdocs build -d /opt/gemmanet/site`
- `website/index.html` is served straight from the checkout; `git pull` already updated it.

## 8. Verify

```bash
curl -s https://api.gemmanet.net/api/v1/status          # "version": "0.2.0a1", online_nodes = your seed count
curl -s https://api.gemmanet.net/api/v1/nodes           # your seed nodes, with stable node_ids
curl -s -X POST https://api.gemmanet.net/api/v1/request \
  -H "Authorization: Bearer $GEMMANET_API_KEY" -H 'Content-Type: application/json' \
  -d '{"task_type": "echo", "content": "hello"}'      # "status": "completed"
curl -s https://api.gemmanet.net/api/v1/feedback -H "Authorization: Bearer $ADMIN_KEY"
```

Then open the dashboard and check that the nodes and leaderboard show up.

## 9. Clean up (optional, once you're happy)

The credit tables are no longer used. After confirming the backup from step 2:

```sql
DROP TABLE transactions;
DROP TABLE accounts;
```

## Rolling back

1. Stop the coordinator and nodes.
2. `git checkout 56abce2 && pip install -e .` (0.1.0a1).
3. Restore the forum file from `backups/` (0.1 stores forum text differently).
4. Start the coordinator as before. The seed scripts run unchanged; 0.1 ignores `GEMMANET_API_KEY`.

PostgreSQL needs no restore unless you dropped the credit tables in step 9.
If 0.1 fails to start with `No module named 'psycopg'`, pip upgraded
SQLAlchemy to 2.1: set `DATABASE_URL=postgresql+psycopg2://...` (0.2 does
this automatically).
