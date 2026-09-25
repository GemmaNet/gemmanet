# Deploying GemmaNet to gemmanet.net (GCP VM behind Cloudflare)

This guide puts GemmaNet on a Google Cloud VM that already serves another
site, with the domain on Cloudflare. It replaces the old site only after the
new stack has passed its checks next to it, and keeps a way back at every step.

Everything runs in Docker: PostgreSQL, Redis, the coordinator and Caddy
(website, docs and HTTPS). Upgrading an existing 0.1 install instead? See
[UPGRADE.md](UPGRADE.md).

## How each step is checked

| Step | Check | If it fails |
|------|-------|-------------|
| 1–5 Prepare | commands in each step | fix before continuing; nothing public has changed |
| 6 Staging on `new.gemmanet.net:8443` | `scripts/smoke_check.py` passes | fix and re-run; the old site is untouched |
| 7 Cut over `gemmanet.net` | `scripts/smoke_check.py` passes | [roll back](#rolling-back) (old site back in a minute) |
| 8 Observe 24–48 h | smoke check + logs, see step 8 | roll back or fix; delete the old site only after this |

The same smoke check runs in CI on every change, against the full Docker stack.

## 1. Confirm where gemmanet.net points

Cloudflare hides the server's address (the orange cloud), so check it in the
Cloudflare dashboard: **DNS → Records**, the `A` record for `gemmanet.net`
shows the origin IP, e.g. `35.254.179.99`. On the VM, `curl -4 -s ifconfig.me`
prints its external IP; the two must match. If they match, this VM serves the
site and you can continue here.

Make sure the VM's external IP is **static**, or it changes when the VM
restarts: GCP console → **VPC network → IP addresses**, and if the address is
"Ephemeral", **Reserve** it (promote to static).

The VM needs about 2 GB of RAM (e2-small or larger). On an e2-micro, add swap
before building: `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`.

## 2. Back up

1. **Snapshot the whole disk** (the easiest full rollback): GCP console →
   **Compute Engine → Disks** → your VM's disk → **Create snapshot**, or
   `gcloud compute disks snapshot DISK_NAME --zone ZONE --snapshot-names gemmanet-before-v02`.
2. **Find what serves the current site**, and note it for the cutover:

   ```bash
   sudo ss -ltnp '( sport = :80 or sport = :443 )'   # which process holds the web ports
   docker ps 2>/dev/null                            # if it runs in Docker
   systemctl list-units --type=service --state=running | grep -Ei 'nginx|apache|httpd|caddy|node|pm2|gunicorn|uvicorn'
   ```

3. **Archive its files and config** (adjust paths to what step 2 found):

   ```bash
   sudo tar czf ~/old-site-$(date +%F).tar.gz /etc/nginx /var/www 2>/dev/null
   ```

## 3. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in afterwards
docker compose version
```

## 4. Get the code and configure it

```bash
sudo mkdir -p /opt/gemmanet && sudo chown $USER /opt/gemmanet
git clone https://github.com/gemmanet/gemmanet.git /opt/gemmanet
cd /opt/gemmanet
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i "s/^ADMIN_KEY=.*/ADMIN_KEY=$(openssl rand -hex 24)/" .env
```

Keep `ADMIN_KEY` somewhere safe: it is the only way to read user feedback.

## 5. Cloudflare

1. **Origin certificate**: **SSL/TLS → Origin Server → Create Certificate**,
   hostnames `gemmanet.net` and `*.gemmanet.net`, 15 years. Save the
   certificate as `/opt/gemmanet/deploy/certs/origin.pem` and the private key
   as `/opt/gemmanet/deploy/certs/origin.key`, then
   `chmod 600 deploy/certs/origin.key`. (This folder is git-ignored.)
2. **SSL/TLS → Overview**: set the mode to **Full (strict)**.
3. **Network**: make sure **WebSockets** is on (nodes connect over WebSocket).
4. **Speed → Optimization**: if **Rocket Loader** is on, turn it off (it rewrites the dashboard's scripts).
5. **DNS → Records**, add (all **Proxied**):
   - `new` → `A` → your VM IP (staging, step 6)
   - `api` → `CNAME` → `gemmanet.net` (the docs point OpenAI clients at `https://api.gemmanet.net/v1`)
   - `www` → `CNAME` → `gemmanet.net`
6. **GCP firewall**: allow TCP 80 and 443 to the VM (usually already open for
   the old site) and, for staging, **8443**: **VPC network → Firewall → Create
   rule**, targets = the VM, source `0.0.0.0/0`, TCP `8443`. Optionally restrict
   the source of all three to [Cloudflare's ranges](https://www.cloudflare.com/ips/)
   so nobody can bypass Cloudflare.

## 6. Staging next to the old site

Run the new stack on port 8443 while the old site keeps 80/443. Cloudflare
proxies port 8443, so it is reachable as `https://new.gemmanet.net:8443`.

Edit `.env`:

```bash
PUBLIC_URL=https://new.gemmanet.net:8443
SITE_ADDRESSES=new.gemmanet.net
API_HOST=api.invalid
TLS_MODE=origin
HTTP_PORT=8080
HTTPS_PORT=8443
```

Start and check:

```bash
docker compose up -d --build --wait
docker compose ps                       # all services "healthy"

docker run --rm gemmanet-app python scripts/smoke_check.py \
    --base https://new.gemmanet.net:8443 \
    --register --admin-key "$(grep ^ADMIN_KEY .env | cut -d= -f2)"
```

The check runs from inside the app image and goes out through Cloudflare like
a real visitor, WebSocket included. All checks must pass. `--register` creates an API key: put it into `.env` as
`GEMMANET_API_KEY` (your seed nodes will use it) and start the seed nodes:

```bash
docker compose --profile seeds up -d
curl -s https://new.gemmanet.net:8443/api/v1/nodes   # three seed nodes
```

Also open `https://new.gemmanet.net:8443/` and `/dashboard/` in a browser.

## 7. Cut over

1. Stop the old site so it frees ports 80/443, using what you found in step 2,
   e.g. `sudo systemctl stop nginx && sudo systemctl disable nginx`
   (or `docker stop <container>`). Don't delete anything yet.
2. Switch `.env` to production:

   ```bash
   PUBLIC_URL=https://gemmanet.net
   SITE_ADDRESSES=gemmanet.net, www.gemmanet.net, api.gemmanet.net
   API_HOST=api.gemmanet.net
   HTTP_PORT=80
   HTTPS_PORT=443
   ```

3. Apply and check:

   ```bash
   docker compose --profile seeds up -d --wait
   docker run --rm gemmanet-app python scripts/smoke_check.py --base https://gemmanet.net \
       --api-base https://api.gemmanet.net \
       --api-key "$(grep ^GEMMANET_API_KEY .env | cut -d= -f2)" \
       --admin-key "$(grep ^ADMIN_KEY .env | cut -d= -f2)"
   ```

   If anything fails, [roll back](#rolling-back) first, then investigate.
4. Remove the staging DNS record `new` and the firewall rule for 8443.

Data carries over from staging (same Docker volumes), including the API key.

## 8. Observe for 24–48 hours

```bash
docker compose ps                                              # nothing restarting
docker compose logs --since 1h coordinator | grep -E 'ERROR|Traceback'
docker run --rm gemmanet-app python scripts/smoke_check.py --base https://gemmanet.net   # after 1 h, 24 h, 48 h
```

When everything stays clean, delete the old site's files, the snapshot from
step 2 and the old service.

## Rolling back

```bash
cd /opt/gemmanet && docker compose --profile seeds stop   # frees 80/443, keeps all data
sudo systemctl enable --now nginx                          # or however the old site ran
```

If the VM itself is broken, restore the disk snapshot from step 2.

## Day-to-day

- **Update to a new version**: `cd /opt/gemmanet && git pull && docker compose --profile seeds up -d --build --wait`, then run the smoke check.
- **Back up data**:

  ```bash
  mkdir -p ~/backups
  docker compose exec -T postgres pg_dump -U gemmanet gemmanet > ~/backups/gemmanet-$(date +%F).sql
  docker compose cp coordinator:/data/forum.db ~/backups/forum-$(date +%F).db
  ```

- **Logs**: `docker compose logs -f coordinator` (also `caddy`, `seed-chat`, ...).
- **Run one coordinator only.** Don't scale the `coordinator` service; a second instance refuses to start.
