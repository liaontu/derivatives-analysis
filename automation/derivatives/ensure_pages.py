"""Repair an unpublished HEAD even when the collector produced no new commit."""
import json
import os
import subprocess
import argparse
import hashlib
import time
import urllib.request
from pathlib import Path
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


def expected_series(root):
    index_path = 'data/derivatives/warrant-series-reports.json'
    index = json.loads((root / index_path).read_text(encoding='utf8'))
    paths = [index_path]
    for item in index['items']:
        report_id = item['id']
        if not all(c.isalnum() or c in '-_' for c in report_id):
            raise ValueError('Unsafe report identifier')
        paths.extend(f'data/derivatives/warrant-series-reports/{report_id}.{ext}' for ext in ('json', 'html'))
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}


def verify_series(root, repository, head, api=gh_api, fetch=None, attempts=20, sleep=time.sleep):
    path = root / 'data/derivatives/check-series-reports.json'
    receipt = json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    proof = {'commit': head, 'status': 'failure', 'verifiedFiles': [], 'expected': {}}
    try:
        expected = expected_series(root)
        proof['expected'] = expected
        owner, name = repository.split('/')
        base = f'https://{owner}.github.io/{name}/'
        def download(url):
            req = urllib.request.Request(url, headers={'Cache-Control':'no-cache','User-Agent':'derivatives-publication-check/1'})
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.read()
        fetch = fetch or download
        last_error = 'Pages build has not completed for the target commit'
        for attempt in range(attempts):
            build = api(f'repos/{repository}/pages/builds/latest', 'GET') or {}
            if build.get('status') == 'built' and build.get('commit') == head:
                verified = []
                try:
                    for file, digest in expected.items():
                        actual = hashlib.sha256(fetch(base+file+'?revision='+head)).hexdigest()
                        if actual != digest:
                            raise ValueError('Published SHA-256 mismatch: '+file)
                        verified.append(file)
                    proof.update(status='verified', verifiedFiles=verified, buildCommit=build['commit'])
                    break
                except Exception as exc:
                    last_error = str(exc)
            if attempt+1 < attempts:
                sleep(15)
        else:
            raise RuntimeError(last_error)
    except Exception as exc:
        proof['error'] = str(exc)[:500]
    proof['checkedAt'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    proof['lastSuccessAt'] = proof['checkedAt'] if proof['status']=='verified' else receipt.get('publication', {}).get('lastSuccessAt')
    receipt['publication'] = proof
    if proof['status'] != 'verified':
        receipt['outcome'] = 'failure'
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    return proof['status'] == 'verified'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-series', action='store_true')
    args = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    message = ensure(repository, head, gh_api, datetime.now(timezone.utc))
    print(message)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(message + '\n')
    if args.verify_series:
        raise SystemExit(0 if verify_series(Path('.'), repository, head) else 1)
