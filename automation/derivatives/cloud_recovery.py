"""A separate hosted timer fills missed collection windows without a desktop or PAT."""
from datetime import datetime, timedelta, timezone
import os
import subprocess
try:
    from .schedule_health import TAIPEI, REPO, read_api, parse, WORKFLOWS
except ImportError:
    from schedule_health import TAIPEI, REPO, read_api, parse, WORKFLOWS


def window(now, branch):
    local = now.astimezone(TAIPEI)
    if local.hour >= 23 or local.hour < 8:
        day = local.date() - timedelta(days=local.hour < 8)
        if branch and day.weekday() >= 5:
            return None
        return datetime.combine(day, datetime.min.time(), TAIPEI) + timedelta(hours=21 if branch else 22)
    if not branch and 12 <= local.hour < 18:
        return local.replace(hour=8, minute=0, second=0, microsecond=0)
    return None


def decision(history, since):
    if not since:
        return 'outside_window'
    if any(r.get('status') != 'completed' for r in history):
        return 'already_running'
    # Any attempted run in this window suppresses a second dispatch, including
    # a failed broker response: never amplify errors or bypass source cooldowns.
    if any(r.get('created_at') and parse(r['created_at']) >= since for r in history):
        return 'already_attempted'
    return 'dispatch'


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Recovery is restricted to the public production default branch')
    now = datetime.now(timezone.utc)
    for name, branch in WORKFLOWS.items():
        since = window(now, branch)
        history = read_api(f'actions/workflows/{name}/runs?branch=main&per_page=30')['workflow_runs'] if since else []
        action = decision(history, since)
        print(name, since, action, flush=True)
        if action == 'dispatch':
            subprocess.run(['gh', 'workflow', 'run', name, '--repo', REPO, '--ref', 'main'], check=True, timeout=30)


if __name__ == '__main__':
    main()
