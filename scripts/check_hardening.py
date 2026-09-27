"""Check that every container of the running compose stack is hardened.

For each container: all capabilities dropped and none added, not privileged,
no-new-privileges, read-only root file system, size-capped json-file logs, no
process running as root, and no "permission denied" in its recent logs (the
usual sign that a read-only or non-root setup broke something). Run on the
Docker host after `docker compose up`:

    python3 scripts/check_hardening.py --expect 7   # with the seed nodes

Exits non-zero if a check fails. Standard library only.
"""
import argparse
import json
import subprocess
import sys


def docker(*args: str) -> str:
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True).stdout


def problems(info: dict, uids: list[int], logs: str = '') -> list[str]:
    host = info['HostConfig']
    found = []
    if 'ALL' not in [c.upper() for c in host.get('CapDrop') or []]:
        found.append(f'cap_drop is {host.get("CapDrop")}, not [ALL]')
    if host.get('CapAdd'):
        found.append(f'cap_add is {host["CapAdd"]}')
    if host.get('Privileged'):
        found.append('privileged')
    if not any(opt.startswith('no-new-privileges') and not opt.endswith(':false')
               for opt in host.get('SecurityOpt') or []):
        found.append('no-new-privileges is not set')
    if not host.get('ReadonlyRootfs'):
        found.append('root file system is writable')
    log = host.get('LogConfig') or {}
    if log.get('Type') != 'json-file' or 'max-size' not in (log.get('Config') or {}):
        found.append(f'logs are not size-capped json-file: {log}')
    if not uids:
        found.append('no processes found')
    if 0 in uids:
        found.append('a process runs as root (uid 0)')
    denied = [line for line in logs.splitlines() if 'permission denied' in line.lower()]
    if denied:
        found.append(f'"permission denied" in logs: {denied[0][:200]}')
    return found


def process_uids(container_id: str) -> list[int]:
    # docker top needs a pid column in the ps output
    rows = [line.split() for line in docker('top', container_id, '-o', 'uid,pid').splitlines()[1:]]
    return [int(row[0]) for row in rows if row and row[0].isdigit()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', default='gemmanet', help='compose project name')
    parser.add_argument('--expect', type=int, help='number of containers that must be running')
    args = parser.parse_args()

    ids = docker('ps', '-q', '--filter', f'label=com.docker.compose.project={args.project}').split()
    failed = False
    if args.expect is not None and len(ids) != args.expect:
        print(f'FAIL  expected {args.expect} running containers, found {len(ids)}')
        failed = True
    for container_id in ids:
        info = json.loads(docker('inspect', container_id))[0]
        name = info['Name'].lstrip('/')
        uids = process_uids(container_id)
        logs = subprocess.run(['docker', 'logs', '--tail', '500', container_id],
                              capture_output=True, text=True)
        found = problems(info, uids, logs.stdout + logs.stderr)
        uids = sorted(set(uids))
        print(f'{"FAIL" if found else "PASS"}  {name}  (uids {uids})  {"; ".join(found)}')
        failed = failed or bool(found)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
