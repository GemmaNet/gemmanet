# Design: Security Audit Follow-ups (2026-09)

**Status:** Implemented (PRs #5, #6 and the supply-chain PR) · **Source:** VM deploy assistant's audit hand-off, 2026-09-27 · **Delivery:** three PRs, each merged to `main` and then deployed by the VM assistant after the owner confirms the commit

Every item below names the check that proves it (the feedback loop), where
that check runs, and how to back the change out. An item is done only when its
check runs in CI and passed.

## 1. Constraints from the VM

| Constraint | Consequence here |
|------------|------------------|
| The VM deploys only merge commits on `main`, after the owner confirms; each rebuild of `coordinator` or `caddy` interrupts service for about 35 s | Few PRs (three), each deployable on its own; no follow-up fixes planned between them |
| The hourly monitor expects `smoke_check.py` to print exactly `13 passed, 0 failed` | The count stays **13** in all three PRs; new assertions go *into* existing checks (see §2.1) |
| A local `docker-compose.override.yml` sets `cgroup_parent`, `mem_limit`, `pids_limit` on all 7 services | The repo never sets those keys; it may add `security_opt`, `cap_drop`, `read_only`, `user`, `tmpfs`, `logging` |
| The VM runs `TLS_MODE=origin-mtls` | Every Caddyfile change keeps that mode working, and CI keeps checking it (with Caddy now non-root) |

## 2. PR 1 — Hardening (M2, M4, L3, L1, L2)

### 2.1 M2 Security response headers

| Where | Headers | Set by |
|-------|---------|--------|
| Coordinator host (`api.gemmanet.net`) | `Strict-Transport-Security: max-age=86400` (no `includeSubDomains`, no `preload`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` | Caddyfile `header` block, all site addresses |
| Website + docs on Pages | the same four | `website/_headers`, copied into the Pages build |
| `/dashboard/`, `/talk/` | `Content-Security-Policy` | the app (the policy belongs next to the page code it describes) |

HSTS starts at one day so a mistake expires quickly; raising it (and adding
`includeSubDomains`) is a later, separate decision because it also binds the
other hostnames in the zone.

**CSP without breaking the pages.** Both pages were audited for inline code:

- Dashboard: one inline `<script>` (it embeds `COORDINATOR_URL`), four inline
  `onclick` handlers, inline `<style>`, and `style=` attributes produced by
  the script. The script moves to `/dashboard/static/dashboard.js`; the URL is
  passed in a `data-` attribute; handlers become `addEventListener`.
- Forum: two inline `oninput` character counters, inline `<style>`, `style=`
  attributes. The counters move to `/talk/forum.js`, driven by `data-` attributes.

Resulting policies (no `'unsafe-inline'` for scripts, so an injected script
cannot read the API key the dashboard keeps in `localStorage`):

```
dashboard: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';
           connect-src 'self' <COORDINATOR_URL origin>; img-src 'self' data:;
           base-uri 'none'; form-action 'none'; frame-ancestors 'none'
forum:     default-src 'none'; script-src 'self'; style-src 'unsafe-inline';
           img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'
```

`style-src 'unsafe-inline'` stays: inline styles cannot run code, and removing
them would mean rewriting both pages' markup for little gain.

**Feedback loop.**
- Unit tests assert each page's CSP and that its HTML contains no inline `<script>` body and no `on*=` attribute (so a future inline handler fails CI instead of silently being blocked in browsers).
- `smoke_check.py` asserts the headers inside existing checks (count stays 13): `dashboard` and `forum` check CSP and the four Caddy headers; `coordinator status` checks the four Caddy headers; `website` checks the four headers from `_headers` (skipped by `--no-redirect-check`, which now means "plain static server that ignores `_redirects` and `_headers`").
- CI runs the smoke check against the Docker stack, and `test_build_pages` checks that `_headers` is in the build.
- A headless browser (local, before the PR) loads both pages under the real headers and confirms: no CSP violation reports in the console, and the dashboard's Quick Test and the forum counter still work.

**Roll back:** revert the PR (headers and CSP are stateless).

### 2.2 M4 Resource limits

| Limit | Value (env override) | Enforced at | Response |
|-------|---------------------|-------------|----------|
| WebSocket connection attempts per client IP | 30 / minute (`GEMMANET_WS_CONNECTS_PER_MINUTE`) | before `accept()` | HTTP 403 |
| Registration must arrive within | 10 s (was 30 s) | handshake | close |
| Online nodes per account (API key owner) | 5 (`GEMMANET_MAX_NODES_PER_ACCOUNT`) | registration; re-checked right after attaching, so concurrent registrations cannot overshoot | error `too_many_nodes`, close 1008 |
| Size of one WebSocket message | 4 MiB (`GEMMANET_WS_MAX_MESSAGE_BYTES`; uvicorn `--ws-max-size` in the image) | app and server | close 1009 |
| Result text per task, streamed chunks in total and the final result each | 1 MiB UTF-8 (`GEMMANET_MAX_RESULT_BYTES`) | `TaskTracker` | task fails: `result_too_large` (HTTP 502 / SSE error); counts as a node failure |
| HTTP request body | 2 MB | Caddy `request_body` on every site address, plus ASGI middleware (also counts chunked bodies) | 413 |
| `params` | ≤ 32 keys, key ≤ 64 chars, any string ≤ 8,192 chars, nesting ≤ 4 levels, ≤ 32 KiB as JSON | request model | 422 |
| OpenAI `model`, `role` | ≤ 128 / ≤ 32 chars | request model | 422 |

Why 2 MB rather than the hand-off's example of 1 MB: `content` may be 200,000
characters, and a client whose JSON encoder escapes non-ASCII text
(`\uXXXX`, 6 bytes per character) sends up to 1.2 MB for a valid request. 2 MB
keeps every valid request working; the SDK's WebSocket client gets the same
4 MiB message limit so a large valid task can still reach a node.

**Feedback loop.** One test per row: the limit + 1 is rejected with the stated
response and the limit itself is accepted; the per-account cap test also
reconnects a node under the same name (a replacement must not count twice);
the stream cap test checks the requester gets `result_too_large` and the task is
released. The e2e test (real uvicorn) covers the message-size close.

**Roll back:** each value is an env var; set it high to disable, or revert.

### 2.3 L3 CSRF on the forum

Forum `POST`s (`/submit`, `/reply/*`, `/upvote/*`) are rejected with 403 when
`Origin` — or, if absent, `Referer` — names a different host than the request's
`Host`. Requests with neither header are allowed: browsers always send `Origin`
on cross-site form posts (a privacy-stripped origin arrives as `null`, which is
rejected), and non-browser clients cannot carry a victim's session anyway.

**Feedback loop.** Tests: foreign `Origin` → 403 and nothing stored; `Origin: null`
→ 403; foreign `Referer` without `Origin` → 403; same-origin → 303 and stored.

### 2.4 L1 Containers

| Service | User | Capabilities | Root FS | Writable |
|---------|------|--------------|---------|----------|
| postgres | `postgres` (image user) | none | read-only | data volume, tmpfs `/var/run/postgresql`, `/tmp` |
| redis | `redis` (image user) | none | read-only | data volume, tmpfs `/tmp` |
| coordinator | `gemmanet` (10001, already) | none | read-only | data volume, tmpfs `/tmp` |
| seed nodes | `gemmanet` (10001) | none | read-only | tmpfs `/tmp` |
| caddy | `caddy` (10002, new) | none | read-only | tmpfs `/data`, `/config` (owned by 10002) |

All services get `no-new-privileges:true` and `cap_drop: [ALL]`.

- **Caddy on 443 without root.** The official binary carries a file capability
  (`cap_net_bind_service`), and with every capability dropped the kernel then
  refuses to execute it at all. The image copies the binary without the
  capability; Docker (20.10+) already sets `net.ipv4.ip_unprivileged_port_start=0`
  in the container's network namespace, and compose states it explicitly, so a
  non-root Caddy binds 80/443 directly: no port remapping, no change to Cloudflare or the firewall.
- **Caddy state.** Certificates come from files, so `/data` and `/config` need no
  persistence and become tmpfs; the `caddy-data`/`caddy-config` volumes are
  no longer used. A tmpfs takes its mode but not its owner from the image, so
  these two are mounted with `uid=10002,gid=10002` (found in testing: Caddy
  kept serving but logged `permission denied`).
- **Postgres and Redis** start directly as their image users, so their entrypoints
  skip the chown/gosu step and need no capabilities. Tried on fresh volumes and
  on volumes created by the old root-started containers: both start, accept
  writes, and Redis can rewrite its AOF.
- **Certificates must be readable by uid/gid 10002:** on the VM, once:
  `sudo chgrp 10002 deploy/certs/* && sudo chmod 640 deploy/certs/*`.
- **Logs:** every service logs with `json-file`, 20 MB × 3 files (matches the VM and makes §3.2's log statement true by default).

**Feedback loop.** CI already starts every service with `--wait` (health
checks) and runs the smoke check, including the seed nodes. A new CI step,
`scripts/check_hardening.py --expect 7`, asserts for each running container:
`CapDrop=[ALL]` and no `CapAdd`, `no-new-privileges`, read-only root FS,
size-capped logs, no process with uid 0, and no `permission denied` in its
logs, so a later compose edit cannot silently drop the hardening or break a
service that still passes its health check. The VM can run the same script
after deploying. The origin-mtls CI check runs the new Caddy image with the
same flags.

**Roll back:** revert; the old volumes are untouched (Caddy's are simply unused).

### 2.5 L2 Public API schema

`/openapi.json`, `/docs` (Swagger UI) and `/redoc` on the coordinator host stay
public, since this is a developer platform and the schema only describes endpoints that are
public anyway. `docs/api_reference.md` states this. The owner can turn them off
later with one line (`FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`).

## 3. PR 2 — Privacy statements match behavior (H3, M1)

### 3.1 H3 Task content goes to independent node operators

1. `docs/privacy.md` states it plainly: requests are processed by the node that
   serves them; its operator can see the content; community operators are not
   bound by this policy.
2. **Trust tiers.** A node is *official* when its account is listed in
   `GEMMANET_OFFICIAL_ACCOUNTS` (comma-separated account ids; the seed nodes'
   account), otherwise *community*. The tier is decided by the coordinator from the
   authenticated key, never claimed by the node, and is shown in `/api/v1/nodes`
   and on the dashboard. Requests may set `trust: "official"` (REST body, OpenAI
   body field, SDK `trust=` argument); routing then never falls back to a
   community node — no official node means "no node available".
   Default stays `"any"`.
   `GET /api/v1/account` returns the caller's account id, so the operator can
   find the id of the seed key.
3. Per-account node cap: shared with §2.2.

**Feedback loop.** Tests with one official and one community node online:
`trust: "official"` always lands on the official node (many requests, both
REST and OpenAI paths, and split tasks); with only community nodes online it is
refused; `"any"` may use both; a node cannot make itself official by what it sends.

### 3.2 M1 Privacy policy vs. reality

| Statement / fact | Change |
|------------------|--------|
| Forum `votes.voter_ip` kept forever in clear | Store `HMAC-SHA256(key, ip)`; the key comes from `FORUM_IP_SECRET`, else is derived from `ADMIN_KEY`, and never sits in the forum DB. Votes older than 30 days are deleted. A migration hashes existing rows in place. Policy states what is kept and for how long. |
| "We remove your data within 30 days of account deletion", but no deletion exists | Implement `DELETE /api/v1/account` (keys, feedback, the account's node reputation/benchmark data, live node connections). Live data goes immediately; backups roll over within 7 days, so the promise holds. |
| "Minimal cookies for session management" | Truth: no cookies; the dashboard keeps the API key in the browser's `localStorage` on the user's device. |
| Access logs contain visitor IPs (20 MB × 3, size-rotated) | Stated, with purpose and the size bound. |

**Feedback loop.** Tests: votes table holds no raw IP after a vote and after the
migration, and dedup still works; expired votes are purged; a deleted account's
key no longer authenticates, its nodes are disconnected, its feedback and node
stats are gone. The policy text is checked against the code in review.

## 4. PR 3 — Supply chain (M7 and 17 HIGH findings in Caddy)

Baseline, Trivy 0.74 (`--severity HIGH,CRITICAL --ignore-unfixed`): official
`caddy:2` (v2.11.4) 17 findings (Go 1.26.3 standard library, `x/crypto`,
`x/net`, `x/text`, `grpc`); `postgres:16` 22 (its `gosu`, built with Go 1.24);
our app image 2 (`wheel` and a library vendored by `setuptools`, both from the
base image); `redis:7` none.

| Item | Design | Feedback loop |
|------|--------|---------------|
| Caddy | Dockerfile stage builds Caddy v2.11.4 with `xcaddy` v0.4.7 on `golang:1.26.8`, replacing `x/crypto` v0.57.0, `x/net` v0.59.0, `x/text` v0.42.0, `grpc` v1.84.0 (each at or above what the build resolves by itself; lower pins would silently downgrade). The web image copies that binary over the official one | Trivy on the web image: 0 |
| PostgreSQL | Our `db` image is `postgres:16` without `gosu`: compose starts it as the `postgres` user (PR 1), so the entrypoint never uses it | Trivy: 0. The same data volume starts |
| App image | Installs only `requirements/app.txt`; the code runs from `/app/src` (`PYTHONPATH`), so `setuptools`/`wheel` are uninstalled | Trivy: 0 |
| Python dependencies | Hash-locked files from `pip-compile --generate-hashes` (`scripts/lock.sh`): `requirements/app.txt` (image), `requirements/dev.txt` (CI, all extras plus the build backend), `docs/requirements.txt` (docs stage and the Pages build, whose command is unchanged; pip enforces hashes when a file has them). Everything installs with `--require-hashes` | CI installs only from the locks and runs `pip check`; a lock that does not match fails the build |
| Base images | Every `FROM` and the compose `redis` image pinned by digest (tag kept for readability and for Dependabot) | Dependabot (`docker`, `docker-compose`, `pip` for `/` and `/docs`, `github-actions`), weekly. Minor Python/Go and major PostgreSQL/Redis/Caddy jumps are ignored. Every update PR runs the full CI including Trivy |
| Trivy in CI | Step in `deploy-smoke`: all four images the stack runs (`gemmanet-app`, `gemmanet-web`, `gemmanet-postgres`, `redis`), vulnerabilities and secrets, fails on anything fixable at HIGH/CRITICAL. One skip: Debian's placeholder "snakeoil" TLS key in the upstream postgres layer (the same public key in every copy of that image; unused, `ssl = off`) | The upstream `caddy:2` and `postgres:16` fail the same gate, so it discriminates |
| `main` unprotected | Ruleset: PRs required, `test` and `deploy-smoke` must pass, no force pushes, no deletion | Set by the owner (see below); verified by a direct push being refused |

**Building on the VM** now compiles Caddy: it needs outbound access to
`proxy.golang.org`, a few minutes, and about 3 GB of build cache, which
`docker builder prune -f` reclaims after a successful deploy.

**Branch protection, by the owner** (the tools available to the assistant
cannot change repository rules): GitHub → the repository → **Settings → Rules
→ Rulesets → New ruleset → New branch ruleset**. Name `main`, enforcement
**Active**, target **Include default branch**, then enable:

- **Restrict deletions**
- **Require a pull request before merging** (required approvals: 0, since
  the owner is the only maintainer and cannot approve their own pull requests)
- **Require status checks to pass**, adding `test` and `deploy-smoke`
- **Block force pushes**

Leave the bypass list empty.

## 5. Decisions taken on the owner's behalf

1. `/openapi.json`, `/docs`, `/redoc` stay public (§2.5).
2. Request bodies are capped at 2 MB, not 1 MB (§2.2).
3. Account deletion is implemented rather than the promise removed (§3.2).
4. Official nodes are configured by account id; the request default stays `trust: "any"` (§3.1).
5. Dependabot rather than Renovate: it needs no app installation (§4).
