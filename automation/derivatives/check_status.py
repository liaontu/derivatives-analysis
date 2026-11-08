"""Per-step receipts: a successful process may still have retained old data."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os

ARTIFACTS = {
    'supplement_sources': ['market-supplement.json'], 'market_completion': ['market-completion.json'],
    'market_history': ['market-history.json'],
    'derivative_daily': ['derivative-reports.json'], 'derivative_series': ['derivative-reports.json'],
    'collect': ['manifest.json'], 'directory': ['stock-warrant-directory.json'],
    'option_history': ['option-history.json'], 'stock_options': ['stock-option-watch.json'],
    'reports': ['daily-reports/warrant-volume/index.json', 'daily-reports/positioning/index.json'],
    'journal': ['daily-reports/warrant-journal/index.json'],
    'tpex_history': ['tpex-history-backfill.json'],
    'stock_institutional': ['stock-institutional.json'],
    'institutional_history': ['stock-institutional-backfill.json'],
    'institutional_research': ['stock-institutional-research.json'],
    'relationship': ['warrant-relationship/index.json'], 'signal_backtest': ['warrant-signal-backtest/outcomes.json'],
    'branches': ['warrant-branches.json'], 'coverage': ['stock-warrant-coverage.json'],
    'series': ['warrant-series-reports.json'], 'lab': ['warrant-lab.json'],
    'lab_reports': ['warrant-lab-reports.json'], 'focus': ['warrant-focus.json'], 'spreads': ['warrant-spreads.json'],
}
BAD = {'retained', 'unavailable', 'failure', 'error', 'failed'}


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def record(output, kind, outcomes, run_id, now=None):
    if kind not in ('market', 'branches', 'series-reports') or not str(run_id).isdecimal():
        raise ValueError('Invalid collector receipt')
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    path = output / f'check-{kind}.json'
    old = read(path)
    if isinstance(outcomes, str):
        outcomes = {'legacy': outcomes}
    outcomes = dict(outcomes)
    if any(v not in ('success', 'failure', 'skipped', 'cancelled') for v in outcomes.values()):
        raise ValueError('Invalid step outcome')
    if 'collect' in outcomes:
        children = read(output / 'collection-steps.json')
        for key in ('lab', 'lab_reports', 'focus', 'spreads'):
            state = children.get('modules', {}).get(key, 'failure') if children.get('runId') == str(run_id) else 'failure'
            outcomes.setdefault(key, state if outcomes['collect'] in ('success', 'failure') else outcomes['collect'])
    modules = {}
    for key, outcome in outcomes.items():
        artifacts = ARTIFACTS.get(key, [])
        records = [read(output / a) for a in artifacts]
        data = next((d for d in records if d.get('status') in BAD), records[0] if records else {})
        status = data.get('status', 'ok')
        reason = data.get('error') or data.get('message')
        failures = [s for d in records for s in d.get('sources', []) if s.get('status') in BAD]
        if failures:
            status, reason = 'retained', '; '.join(s['id']+': '+str(s.get('error') or s['status']) for s in failures)
        if any(not d for d in records):
            status, reason = 'unavailable', 'Expected output is missing or invalid'
        if outcome != 'success':
            status, reason = outcome, reason or 'Workflow step '+outcome+'; data is not confirmed refreshed'
        previous = old.get('modules', {}).get(key, {})
        good = outcome == 'success' and status not in BAD
        dates = data.get('dates', [])
        data_date = data.get('asOf') or data.get('latest') or data.get('end') or data.get('lastMarketDate') or data.get('date') or (dates[-1] if dates else None)
        if data_date is not None and not isinstance(data_date, str):
            data_date = max((r.get('dataThrough') or '' for r in data.get('items', [])), default='') or None
        if key=='branches':
            rank_dates=sorted(r['date'] for r in data.get('items',[]) if r.get('date'))
            data_date=(rank_dates[0] if rank_dates[0]==rank_dates[-1] else rank_dates[0]+'～'+rank_dates[-1]) if rank_dates else None
        if key=='coverage':
            data_date=data.get('directoryDay')
        if key == 'lab_reports' and data.get('latest'):
            item = next((r for r in data.get('items', []) if r.get('id') == data['latest']), {})
            data_date = item.get('date')+'（報告日）' if item.get('date') else None
        modules[key] = {'outcome': outcome, 'status': status, 'checkedAt': now,
                        'lastSuccessAt': now if good else previous.get('lastSuccessAt'),
                        'dataDate': data_date,
                        'artifacts': artifacts, 'reason': str(reason)[:500] if reason else None,
                        'evidence': data.get('failureEvidence') or data.get('evidencePath') or (data.get('roster') or {}).get('failureEvidence')}
    failed = any(r['outcome'] != 'success' or r['status'] in BAD for r in modules.values())
    payload = {'schemaVersion': 2, 'checkedAt': now, 'outcome': 'failure' if failed else 'success',
               'runId': str(run_id), 'modules': modules}
    if kind == 'series-reports' and old.get('publication'):
        payload['publication'] = old['publication']
    output.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    if kind in ('market', 'branches'):
        try:
            from .update_health import record as record_freshness
        except ImportError:
            from update_health import record as record_freshness
        record_freshness(output, datetime.fromisoformat(now))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['market', 'branches', 'series-reports'])
    parser.add_argument('outcomes', nargs='+')
    args = parser.parse_args()
    outcomes = {}
    for i, value in enumerate(args.outcomes):
        key, outcome = value.split('=', 1) if '=' in value else ('step'+str(i+1), value)
        outcomes[key] = outcome
    record(Path('data/derivatives'), args.kind, outcomes, os.environ['GITHUB_RUN_ID'])
