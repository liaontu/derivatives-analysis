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
    if latest and latest.get('commit') == head and latest.get('status') == 'errored' and successful_deployment(repository, head, api):
        return f'Pages deployment already succeeded for {head}; legacy build status differs, no duplicate build requested.'
    if needs_build(latest, head, now):
        api(f'repos/{repository}/pages/builds', 'POST')
        return f'Pages build requested for {head}; includes recovery after a prior failed or interrupted deployment.'
    return f'Pages already built or building {head}; no duplicate build requested.'


def successful_deployment(repository, head, api):
    """Pages' legacy build endpoint can report a cancelled duplicate as errored."""
    deployments = api(f'repos/{repository}/deployments?environment=github-pages&sha={head}&per_page=5', 'GET')
    if not isinstance(deployments, list):
        return None
    for deployment in deployments:
        if deployment.get('sha') != head or deployment.get('environment') != 'github-pages':
            continue
        statuses = api(f"repos/{repository}/deployments/{deployment['id']}/statuses", 'GET')
        if statuses and statuses[0].get('state') == 'success':
            return {'id': deployment['id'], 'commit': head, 'status': 'built', 'basis': 'github-pages deployment API'}
    return None


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
    derivatives = root / 'data/derivatives/derivative-reports.json'
    if derivatives.exists():
        paths.append('data/derivatives/derivative-reports.json')
        for item in json.loads(derivatives.read_text(encoding='utf8'))['items']:
            report_id = item['id']
            if not report_id or not all(c.isalnum() or c in '-_' for c in report_id):
                raise ValueError('Unsafe derivative report identifier')
            paths.extend(f'data/derivatives/derivative-reports/{report_id}.{ext}' for ext in ('json', 'html'))
    history = root / 'data/derivatives/market-history.json'
    if history.exists():
        paths.append('data/derivatives/market-history.json')
        for feed, entries in json.loads(history.read_bytes()).get('feeds', {}).items():
            if not entries: continue
            for day in sorted(entries if feed.endswith('-fcm') else {min(entries), max(entries)}):
                entry = entries[day]
                relative = Path(entry['path'])
                if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'market-history':
                    raise ValueError('Unsafe history archive path')
                paths.append('data/derivatives/'+entry['path'])
    monthly = root/'data/derivatives/fcm-monthly.json'
    if (root/'data/derivatives/fcm-history-check.json').exists():
        paths.append('data/derivatives/fcm-history-check.json')
    if monthly.exists():
        paths.append('data/derivatives/fcm-monthly.json')
        for entries in json.loads(monthly.read_bytes()).get('months',{}).values():
            for entry in entries.values():
                relative = Path(entry['path'])
                if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] != ('market-history','fcm-monthly'):
                    raise ValueError('Unsafe monthly archive path')
                paths.append('data/derivatives/'+entry['path'])
    completion = root/'data/derivatives/market-completion.json'
    if completion.exists():
        data=json.loads(completion.read_bytes())
        paths.extend(['data/derivatives/market-completion.json','data/derivatives/market-supplement.json'])
        entries=[data['references'],data['research']]+[r for r in data['stocks'] if r['code'] in ('2330','3008','6488','8069')]
        for entry in entries:
            relative=Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('market-completion','option-settlement-study'):
                raise ValueError('Unsafe completion artifact path')
            paths.append('data/derivatives/'+entry['path'])
    liquidity=root/'data/derivatives/warrant-liquidity.json'
    if liquidity.exists():
        data=json.loads(liquidity.read_bytes());paths.append('data/derivatives/warrant-liquidity.json')
        entries=list(data.get('sources',{}).values())+[r for r in data.get('items',[]) if r['code'] in ('2330','6488')]
        for entry in entries:
            relative=Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0]!='warrant-liquidity':raise ValueError('Unsafe quotation artifact path')
            paths.append('data/derivatives/'+entry['path'])
    if (root/'data/derivatives/warrant-extra-history.json').exists():paths.append('data/derivatives/warrant-extra-history.json')
    if (root/'data/derivatives/stock-history-repair.json').exists():paths.append('data/derivatives/stock-history-repair.json')
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
            if build.get('status') == 'errored' and build.get('commit') == head:
                build = successful_deployment(repository, head, api) or build
            if build.get('status') == 'built' and build.get('commit') == head:
                verified = []
                try:
                    for file, digest in expected.items():
                        actual = hashlib.sha256(fetch(base+file+'?revision='+head)).hexdigest()
                        if actual != digest:
                            raise ValueError('Published SHA-256 mismatch: '+file)
                        verified.append(file)
                    proof.update(status='verified', verifiedFiles=verified, buildCommit=build['commit'],
                                 verificationBasis=build.get('basis','Pages build API'), deploymentId=build.get('id'))
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
