# Deploying GemmaNet to gemmanet.net

| Part | Runs on | Hostnames |
|------|---------|-----------|
| Website + docs (static) | Cloudflare Pages | `gemmanet.net`, `www.gemmanet.net` |
| Coordinator: API, WebSocket, dashboard, forum | Docker on a GCP VM, behind Cloudflare | `api.gemmanet.net` |

The API goes live first on its own hostname, which touches nothing that exists
today. Only then does `gemmanet.net` move from the old Pages project to the
GemmaNet Pages project; moving it back is the rollback. Upgrading an existing
0.1 install instead? See [UPGRADE.md](UPGRADE.md). Everything on one server
instead? See [the last section](#alternative-everything-on-the-vm).

## How each step is checked

| Step | Check | If it fails |
|------|-------|-------------|
| A1–A2 Prepare VM and Cloudflare | commands in each step | fix; nothing public has changed |
| A3 Staging on `new.gemmanet.net:8443` | `smoke_check.py --api-base` | fix; nothing public has changed |
| A4 API live on `api.gemmanet.net` | `smoke_check.py --api-base` | delete the `api` DNS record; the main site never depended on it |
| B1–B2 Pages project, preview | `smoke_check.py` against `*.pages.dev` | fix; `gemmanet.net` is untouched |
| B3 Audit + rollback drill | checklist, timed drill | resolve before switching |
| B4 Switch `gemmanet.net` | `smoke_check.py` against the live site | move the domain back to the old project |
| C Observe 24–48 h | hourly smoke check with an alert | roll back or fix |

The smoke check also runs in CI on every change, against the Docker stack and
against the site as built for Pages.

## Part A — the coordinator on the VM

### A1. Prepare the VM

1. **Static IP**: GCP console → **VPC network → IP addresses**; if the VM's
   external address is "Ephemeral", **Reserve** it, or it changes on restart.
2. **Memory**: 2 GB or more (e2-small+). On less, add swap:
   `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`.
3. **Snapshot** before installing or changing Docker if the VM runs anything
   else: GCP console → **Compute Engine → Disks** → the VM's disk →
   **Create snapshot**, or `gcloud compute disks snapshot DISK --zone ZONE --snapshot-names before-gemmanet`.
4. **Docker** (skip if installed): `curl -fsSL https://get.docker.com | sudo sh`,
   then `docker compose version`. Ports that Docker publishes bypass `ufw`;
   the GCP firewall (A2) is what limits who can reach them.
5. **Code**, pinned to a reviewed commit:

   ```bash
   sudo mkdir -p /opt/gemmanet && sudo chown $USER /opt/gemmanet
   git clone https://github.com/GemmaNet/gemmanet.git /opt/gemmanet
   cd /opt/gemmanet && git checkout <commit>
   ```

6. **Configuration**: the defaults in `.env.example` are for this layout.

   ```bash
   cp .env.example .env && chmod 600 .env
   sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
   sed -i "s/^ADMIN_KEY=.*/ADMIN_KEY=$(openssl rand -hex 24)/" .env
   docker compose build
   ```

   Keep `ADMIN_KEY` somewhere safe: it is the only way to read user feedback.

### A2. Cloudflare and GCP settings for the API

1. **Origin certificate**: **SSL/TLS → Origin Server → Create Certificate**,
   hostnames `gemmanet.net` and `*.gemmanet.net`. On the VM, paste the
   certificate into `deploy/certs/origin.pem` and the private key into
   `deploy/certs/origin.key` yourself (e.g. with `nano`); the key should not
   pass through chats or tickets. Then check:

   ```bash
   chmod 600 deploy/certs/origin.key
   openssl x509 -in deploy/certs/origin.pem -noout -subject -dates -ext subjectAltName
   # certificate and key belong together if these two hashes match:
   openssl x509 -in deploy/certs/origin.pem -noout -pubkey | sha256sum
   openssl pkey -in deploy/certs/origin.key -pubout | sha256sum
   ```

2. **Scope settings to GemmaNet's hostnames.** The zone-wide SSL/TLS mode and
   Rocket Loader also apply to the old site and every other record in the
   zone, so don't change them. Note the zone's current SSL/TLS mode, then
   **Rules → Configuration Rules → Create rule**: when *Hostname* is in
   `new.gemmanet.net`, `api.gemmanet.net`, set **SSL = Full (strict)** and
   **Rocket Loader = Off**.
3. **Network → WebSockets**: on (nodes connect over WebSocket).
4. **Bots and WAF**: SDK clients, OpenAI clients and node WebSockets are not
   browsers. If **Security → Bots → Bot Fight Mode** or a WAF challenge rule is
   on, the smoke check fails with 403 or a challenge page. Add a WAF custom
   rule that skips challenges for hostname `api.gemmanet.net` (and
   `new.gemmanet.net`); Bot Fight Mode on the Free plan may not be skippable
   per hostname, in which case it has to be off.
5. **DNS**: add `new` → **A** → the VM's IP, **Proxied** (staging).
6. **GCP firewall** (**VPC network → Firewall → Create rule**, target = the
   VM): allow TCP **8443** (staging) and **443** (live) from
   [Cloudflare's IPv4 ranges](https://www.cloudflare.com/ips-v4) only.
   Cloudflare reaches the origin on 443 in Full (strict) mode; port 80 need
   not be open.

### A3. Staging on new.gemmanet.net:8443

Set in `.env` (ports 8080 and 8443 must be free on the VM):

```bash
PUBLIC_URL=https://new.gemmanet.net:8443
SITE_ADDRESSES=new.gemmanet.net
API_HOST=new.gemmanet.net
HTTP_PORT=8080
HTTPS_PORT=8443
```

Start and check:

```bash
docker compose up -d --wait && docker compose ps      # all services healthy
docker run --rm gemmanet-app python scripts/smoke_check.py \
    --api-base https://new.gemmanet.net:8443 \
    --register --admin-key "$(grep ^ADMIN_KEY .env | cut -d= -f2)"
```

The check runs from inside the app image and goes out through Cloudflare like
a real client, WebSocket included. `--register` creates an API key:
registration is limited to 5 per hour per IP, so **register once**, save the
key as `GEMMANET_API_KEY` in `.env`, and use `--api-key` from then on. Start
the seed nodes with it:

```bash
docker compose --profile seeds up -d --wait
curl -s https://new.gemmanet.net:8443/api/v1/nodes    # three seed nodes
```

Also open `https://new.gemmanet.net:8443/dashboard/` and `/talk/` in a browser.

### A4. API live on api.gemmanet.net

The main site does not change in this step.

1. Add `api.gemmanet.net` to the Configuration Rule from A2 (if not there yet).
2. In `.env`:

   ```bash
   PUBLIC_URL=https://api.gemmanet.net
   SITE_ADDRESSES=api.gemmanet.net
   API_HOST=api.gemmanet.net
   HTTP_PORT=80
   HTTPS_PORT=443
   ```

3. `docker compose --profile seeds up -d --wait`
4. **DNS**: add `api` → **A** → the VM's IP, **Proxied**.
5. Check:

   ```bash
   docker run --rm gemmanet-app python scripts/smoke_check.py \
       --api-base https://api.gemmanet.net \
       --api-key "$(grep ^GEMMANET_API_KEY .env | cut -d= -f2)" \
       --admin-key "$(grep ^ADMIN_KEY .env | cut -d= -f2)"
   ```

   If it fails, delete the `api` record and investigate.
6. Remove the staging DNS record `new` and the firewall rule for 8443.

Until B4 the dashboard's and forum's "Home" and "Docs" links lead to
`gemmanet.net`, which still shows the old site.

## Part B — the website on Cloudflare Pages

### B1. Create the Pages project

**Workers & Pages → Create → Pages → Connect to Git**, pick
`GemmaNet/gemmanet` (allow Cloudflare's GitHub app to access it), then:

| Setting | Value |
|---------|-------|
| Production branch | `main` |
| Framework preset | None |
| Build command | `pip install -r docs/requirements.txt && python scripts/build_pages.py` |
| Build output directory | `pages-dist` |
| Environment variables | `PYTHON_VERSION` = `3.11`, `GEMMANET_API_ORIGIN` = `https://api.gemmanet.net` |

**Save and Deploy.** The site appears at `https://<project>.pages.dev`. From
now on every push to `main` redeploys the website automatically; the VM only
changes when you update it.

The build points the website's forum, dashboard and forum-preview links at
`GEMMANET_API_ORIGIN` and adds `_redirects` so `/talk`, `/dashboard`, `/api`
and `/v1` on the main domain lead there.

### B2. Check the preview

```bash
docker run --rm gemmanet-app python scripts/smoke_check.py \
    --base https://<project>.pages.dev --api-base https://api.gemmanet.net \
    --api-key "$(grep ^GEMMANET_API_KEY .env | cut -d= -f2)"
```

This also checks that the website may call the API (CORS) and that the
redirects work. Open the preview in a browser: the "Join the Conversation"
section should list recent forum posts.

### B3. Before switching: audit and rollback drill

Audit the zone and write down what you find:

- **The old Pages project → Custom domains**: exactly which domains it has
  (`gemmanet.net`, `www`?). These are what you move in B4 and move back on
  rollback.
- **Workers Routes** matching `gemmanet.net/*`: a route would keep handling
  requests whichever project owns the domain.
- **Page Rules, Cache Rules, Redirect Rules, Transform Rules** for
  `gemmanet.net`: make sure none rewrite or redirect paths the new site needs.
- **DNS** records for `gemmanet.net` and `www` (Pages manages them as CNAMEs
  to `<project>.pages.dev`).

Rollback drill, on a throwaway hostname such as `rbtest.gemmanet.net`:

1. Add it as a custom domain of the **old** project; wait until *Active*.
2. Remove it there and add it to the **GemmaNet** project. Time how long until
   it serves GemmaNet.
3. Move it back. Time how long until it serves the old site again.
4. Remove `rbtest` from both projects and DNS.

The two times are what to expect for the switch and for a rollback.

### B4. Switch gemmanet.net

1. **Old project → Custom domains**: remove `gemmanet.net` (and `www` if listed).
2. **GemmaNet project → Custom domains**: add `gemmanet.net` and
   `www.gemmanet.net`; wait until both are *Active*.
3. Check:

   ```bash
   docker run --rm gemmanet-app python scripts/smoke_check.py \
       --base https://gemmanet.net --api-base https://api.gemmanet.net \
       --api-key "$(grep ^GEMMANET_API_KEY .env | cut -d= -f2)" \
       --admin-key "$(grep ^ADMIN_KEY .env | cut -d= -f2)"
   ```

4. If anything fails, roll back: remove the domains from the GemmaNet project
   and add them back to the old project.

## Part C — observe and operate

**Observe for 24–48 hours.** Run the B4 check hourly and alert on failure.
For example, with cron (replace `ALERT_COMMAND` with however this machine
sends alerts, e.g. a Telegram message):

```cron
0 * * * * cd /opt/gemmanet && docker run --rm gemmanet-app python scripts/smoke_check.py --base https://gemmanet.net --api-base https://api.gemmanet.net --api-key "$(grep ^GEMMANET_API_KEY .env | cut -d= -f2)" >> $HOME/gemmanet-smoke.log 2>&1 || ALERT_COMMAND
```

Also watch `docker compose ps` (nothing restarting) and
`docker compose logs --since 1h coordinator | grep -E 'ERROR|Traceback'`.
Keep the old Pages project, backups and the snapshot until the site has been
stable for the whole period.

**Daily backups** (cron at 03:00, keeping 7 days):

```cron
0 3 * * * mkdir -p $HOME/backups/gemmanet && cd /opt/gemmanet && docker compose exec -T postgres pg_dump -U gemmanet gemmanet | gzip > $HOME/backups/gemmanet/db-$(date +\%F).sql.gz && docker compose cp coordinator:/data/forum.db $HOME/backups/gemmanet/forum-$(date +\%F).db && find $HOME/backups/gemmanet -mtime +7 -delete
```

**Updating**:

- Website: automatic on every push to `main`.
- Coordinator: `cd /opt/gemmanet && git fetch && git checkout <commit> && docker compose --profile seeds up -d --build --wait`, then the smoke check.

**Run one coordinator only.** Don't scale the `coordinator` service; a second
instance refuses to start.

## Rolling back

| What | How |
|------|-----|
| Main site (B4) | Move `gemmanet.net` / `www` back to the old Pages project |
| API (A4) | Delete the `api` DNS record, or `docker compose --profile seeds stop` |
| The VM itself | Restore the snapshot from A1 |

## Alternative: everything on the VM

The web image also serves the website and docs. To run without Pages, use the
alternative values in `.env.example` (`SITE_ADDRESSES` with `gemmanet.net`,
`www.gemmanet.net` and `api.gemmanet.net`; `PUBLIC_URL=https://gemmanet.net`;
`SITE_URL=/`), include those hostnames in the Configuration Rule, point
`gemmanet.net` and `www` at the VM (**A**, proxied) instead of doing Part B, and
check with `--base https://gemmanet.net --api-base https://api.gemmanet.net`.
