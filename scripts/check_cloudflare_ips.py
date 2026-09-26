"""Check that the Caddyfile trusts exactly Cloudflare's current edge ranges.

    python scripts/check_cloudflare_ips.py deploy/Caddyfile.docker

Cloudflare publishes its ranges at https://www.cloudflare.com/ips/. If they
change, visitors behind new edges would all share one rate-limit bucket until
the Caddyfile is updated, so CI runs this check.
"""
import re
import sys

import httpx

SOURCES = ('https://www.cloudflare.com/ips-v4', 'https://www.cloudflare.com/ips-v6')


def trusted_ranges(caddyfile: str) -> set[str]:
    match = re.search(r'trusted_proxies\s+static\s+([^\n]+)', caddyfile)
    if not match:
        raise SystemExit('no "trusted_proxies static" line found')
    return set(match.group(1).split())


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else 'deploy/Caddyfile.docker'
    with open(path) as f:
        configured = trusted_ranges(f.read())
    published = set()
    for url in SOURCES:
        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        published.update(line.strip() for line in resp.text.splitlines() if line.strip())

    missing, extra = published - configured, configured - published
    if not missing and not extra:
        print(f'{path}: trusted_proxies matches Cloudflare ({len(published)} ranges)')
        return 0
    if missing:
        print(f'missing from {path}: {" ".join(sorted(missing))}')
    if extra:
        print(f'no longer Cloudflare, remove from {path}: {" ".join(sorted(extra))}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
