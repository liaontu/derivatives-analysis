"""Workflow health, duplicate-run suppression and opt-in bounded recovery."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

REPO = 'liaontu/derivatives-analysis'
TAIPEI = timezone(timedelta(hours=8))
WORKFLOWS = {'derivatives-daily.yml': False, 'warrant-branches-daily.yml': True}


class QueryError(RuntimeError):
    def __init__(self, code, status=None):
        super().__init__(code)
        self.code, self.status = code, status


def read_api(path):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'FlowScope-schedule-health'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    # Reuse gh's existing login on desktops without exposing its credential.
    cli = shutil.which('gh') if not token else None
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/{path}', headers=headers)
    for attempt in range(3):
        try:
            if cli:
                result = subprocess.run([cli, 'api', '--hostname', 'github.com', f'repos/{REPO}/{path}'],
                                        capture_output=True, text=True, encoding='utf-8', timeout=25)
                if result.returncode:
                    match = re.search(r'HTTP (\d{3})', result.stderr)
                    status = int(match[1]) if match else None
                    code = 'rate_limited' if status == 429 or 'rate limit' in result.stderr.lower() else 'authentication_required' if status in (401, 403) or 'gh auth login' in result.stderr else 'query_failed'
                    raise QueryError(code, status)
                return json.loads(result.stdout)
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            reason = 'rate_limited' if error.code == 429 or error.headers.get('X-RateLimit-Remaining') == '0' else 'authentication_required' if error.code in (401, 403) else 'query_failed'
            failure = QueryError(reason, error.code)
            retry = error.code in (408, 500, 502, 503, 504)
        except QueryError as error:
            failure = error
            retry = error.status in (408, 500, 502, 503, 504)
        except (urllib.error.URLError, TimeoutError, subprocess.TimeoutExpired, json.JSONDecodeError):
            failure, retry = QueryError('network_or_response_error'), True
        if not retry or attempt == 2:
            raise failure
        time.sleep(2 * (attempt + 1))


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
    attempts = [r for r in history if r.get('run_started_at') and since <= parse(r['run_started_at']) <= now]
    latest = max(attempts, key=lambda r:r['run_started_at'], default=None)
    if latest and latest.get('status') != 'completed':
        state = 'running'
    elif latest and latest.get('conclusion') in ('failure', 'timed_out', 'action_required'):
        state = 'degraded' if successful else 'failed'
    else:
        state = 'ok' if successful else 'overdue'
    return {'state': state, 'windowStart': since.isoformat(),
            'deadline': deadline.isoformat(),
            'latestSuccess': max((r['run_started_at'] for r in successful), default=None),
            'latestAttempt': {k:latest.get(k) for k in ('run_started_at','status','conclusion','html_url')} if latest else None,
            'meaning': 'Workflow completion only; source dates and quality must be checked separately.'}


def recover_overdue(workflow, status, state_dir, *, now, busy=False):
    """At most one dispatch per missed window, including uncertain outcomes."""
    if workflow not in WORKFLOWS:
        raise ValueError('Unsupported workflow')
    if os.environ.get('GITHUB_ACTIONS'):
        return {'state': 'disabled_in_actions'}
    if status.get('state') != 'overdue':
        return {'state': 'not_overdue'}
    if busy:
        return {'state': 'collector_busy_or_unknown'}
    cli = shutil.which('gh')
    if not cli:
        return {'state': 'gh_unavailable'}
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    window = parse(status['windowStart']).astimezone(TAIPEI).strftime('%Y%m%d-%H%M')
    marker = state_dir / f'{workflow}-{window}.json'
    attempt = {'workflow': workflow, 'windowStart': status['windowStart'],
               'requestedAt': now.isoformat(), 'state': 'requesting'}
    try:
        # Exclusive creation is also the process lock. A crash or uncertain
        # response never authorizes a duplicate request in the same window.
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(attempt, stream)
    except FileExistsError:
        return {'state': 'already_requested', 'record': str(marker)}
    try:
        result = subprocess.run([cli, 'workflow', 'run', workflow, '--repo', REPO, '--ref', 'main'],
                                capture_output=True, text=True, encoding='utf-8', timeout=25)
        attempt['state'] = 'dispatched' if result.returncode == 0 else 'dispatch_unconfirmed'
    except (OSError, subprocess.TimeoutExpired):
        attempt['state'] = 'dispatch_unconfirmed'
    # No stdout/stderr or credentials persisted. A successful dispatch is not
    # a successful collection; the next health check must verify its jobs.
    marker.write_text(json.dumps(attempt, indent=2), encoding='utf-8')
    return {**attempt, 'record': str(marker)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate', choices=['derivatives-daily.yml', 'warrant-branches-daily.yml'])
    parser.add_argument('--minutes', type=int, default=30)
    parser.add_argument('--recover-overdue', action='store_true', help='Desktop only: dispatch once per missed window')
    parser.add_argument('--recovery-dir', type=Path, default=Path(__file__).resolve().parents[2]/'.analysis-cache'/'collector-recovery')
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
    result, histories = {}, {}
    for name, branch in WORKFLOWS.items():
        try:
            histories[name] = runs(name)
            result[name] = health(histories[name], now, branch=branch)
        except Exception as exc:
            result[name] = {'state': 'unknown', 'failureType': type(exc).__name__}
            if isinstance(exc, QueryError):
                result[name].update({'reason': exc.code, 'httpStatus': exc.status})
    if args.recover_overdue:
        # Collectors share the write queue. Avoid adding work while either
        # workflow is active, or its state cannot be verified.
        busy = any(value['state'] == 'unknown' for value in result.values()) or any(
            r.get('status') != 'completed' for history in histories.values() for r in history)
        for name, status in result.items():
            status['recovery'] = recover_overdue(name, status, args.recovery_dir, now=now, busy=busy)
            if status['recovery']['state'] in ('dispatched', 'dispatch_unconfirmed'):
                busy = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(r['state'] == 'ok' for r in result.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
