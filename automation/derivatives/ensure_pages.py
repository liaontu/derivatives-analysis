"""Repair an unpublished HEAD even when the collector produced no new commit."""
import json
import os
import subprocess
from datetime import datetime, timezone, timedelta


def needs_build(build, head, now):
    if not build or build.get('commit') != head:
        return True
    if build.get('status') == 'built':
        return False
    if build.get('status') in ('queued', 'building'):
        try:
            started = datetime.fromisoformat(build['updated_at'].replace('Z', '+00:00'))
            if now - started < timedelta(minutes=30):
                return False
        except (KeyError, TypeError, ValueError):
            pass
    return True


def ensure(repository, head, api, now):
    latest = api(f'repos/{repository}/pages/builds/latest', 'GET')
    if needs_build(latest, head, now):
        api(f'repos/{repository}/pages/builds', 'POST')
        return f'Pages build requested for {head}; includes recovery after a prior failed or interrupted deployment.'
    return f'Pages already built or building {head}; no duplicate build requested.'


def gh_api(endpoint, method):
    result = subprocess.run(['gh', 'api', '--method', method, endpoint], capture_output=True, text=True)
    if result.returncode:
        if method == 'GET' and '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError(f'GitHub Pages {method} failed: {result.stderr.strip()}')
    return json.loads(result.stdout) if result.stdout.strip() else None


if __name__ == '__main__':
    repository = os.environ['GITHUB_REPOSITORY']
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    message = ensure(repository, head, gh_api, datetime.now(timezone.utc))
    print(message)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(message + '\n')
