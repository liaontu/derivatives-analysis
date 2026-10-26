"""Read-only workflow health and duplicate-run suppression. Never commits heartbeats."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import urllib.request

REPO = 'liaontu/derivatives-analysis'
TAIPEI = timezone(timedelta(hours=8))


def read_api(path):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'FlowScope-schedule-health'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/{path}', headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def runs(workflow):
    history = read_api(f'actions/workflows/{workflow}/runs?per_page=100')['workflow_runs']
    # A successful workflow can have skipped collection. Never let skipped runs
    # extend the suppression window or conceal a missed collection.
    checked = 0
    for run in history:
        run['collected'] = False
    for run in history:
        if run.get('status') != 'completed' or run.get('conclusion') != 'success':
            continue
        if checked >= 8:
            raise RuntimeError('No verified collection within bounded job lookup')
        checked += 1
        jobs = read_api(f"actions/runs/{run['id']}/jobs?per_page=100&filter=latest")['jobs']
        run['collected'] = any(step.get('name') in ('Fetch official derivatives data', 'Fetch bounded warrant rankings')
                               and step.get('conclusion') == 'success'
                               for job in jobs for step in job.get('steps', []))
        if run['collected']:
            break
    return history


def parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def should_run(history, now, event, head, minutes):
    if event != 'schedule':
        return True
    return not any(r.get('collected') and r.get('status') == 'completed' and r.get('conclusion') == 'success'
                   and r.get('head_sha') == head
                   and 0 <= (now-parse(r['run_started_at'])).total_seconds() < minutes*60
                   for r in history if r.get('run_started_at'))


def health(history, now, *, branch=False):
    local = now.astimezone(TAIPEI)
    windows = [(20, 23)] if branch else [(8, 13), (18, 21)]
    deadlines = []
    for offset in (0, 1):
        day = local.date()-timedelta(days=offset)
        for start, deadline in windows:
            end = datetime.combine(day, datetime.min.time(), TAIPEI)+timedelta(hours=deadline)
            if end <= local:
                deadlines.append((end, end.replace(hour=start)))
    deadline, since = max(deadlines)
    successful = [r for r in history if r.get('collected') and r.get('conclusion') == 'success' and r.get('status') == 'completed'
                  and r.get('run_started_at') and since <= parse(r['run_started_at']) <= now]
    return {'state': 'ok' if successful else 'overdue', 'windowStart': since.isoformat(),
            'deadline': deadline.isoformat(),
            'latestSuccess': max((r['run_started_at'] for r in successful), default=None),
            'meaning': 'Workflow completion only; source dates and quality must be checked separately.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate', choices=['derivatives-daily.yml', 'warrant-branches-daily.yml'])
    parser.add_argument('--minutes', type=int, default=30)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.gate:
        decision = True
        try:
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
            decision = should_run(runs(args.gate), now, os.environ.get('GITHUB_EVENT_NAME'), head, args.minutes)
        except Exception:
            print('::warning::Health API unavailable; collection will proceed.')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write(f'run={str(decision).lower()}\n')
        print(json.dumps({'run': decision, 'reason': 'due_or_manual' if decision else 'recent_success_same_revision'}))
        return 0
    result = {}
    for name, branch in [('derivatives-daily.yml', False), ('warrant-branches-daily.yml', True)]:
        try:
            result[name] = health(runs(name), now, branch=branch)
        except Exception as exc:
            result[name] = {'state': 'unknown', 'failureType': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(r['state'] == 'ok' for r in result.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
