"""Replay v3 from the exact public Git snapshots whose inputs match the v2 ledger."""
from datetime import date, datetime, timezone
from pathlib import Path
import argparse
import json
import subprocess

try:
    from . import warrant_lab as lab
    from .daily_reports import read, save
    from .trading_calendar import load_calendar
except ImportError:
    import warrant_lab as lab
    from daily_reports import read, save
    from trading_calendar import load_calendar


def git_read(repo, commit, name):
    raw = subprocess.check_output(['git','-C',str(repo),'show',commit+':data/derivatives/'+name], timeout=45)
    return json.loads(raw)


def matched_inputs(wanted, warrants, focus, manifest):
    rows = [r for r in warrants['rows'] if r.get('market') == 'TWSE']
    items = [r for r in focus.get('items',[]) if r.get('market') == 'TWSE' and r.get('date') == wanted['marketDate']]
    feeds = {s['id']:s for s in manifest['sources']}
    sources = [{k:feeds.get(s,{}).get(k) for k in ('date','status')} | {'id':s} for s in lab.SOURCES]
    return lab.digest({'warrants':rows,'focus':items,'sources':sources}) == wanted['inputSha256']


def replay(repo, output, legacy, now=None):
    lab.validate(legacy)
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    targets = {e['marketDate']:e for e in legacy['journal'] if e.get('marketDate')}
    commits = subprocess.check_output(['git','-C',str(repo),'log','--format=%H','--','data/derivatives/warrant-lab.json'],text=True).splitlines()
    found = {}
    for commit in commits:
        if len(found) == len(targets): break
        try:
            snapshot = git_read(repo,commit,'warrant-lab.json')
            day = snapshot.get('lastMarketDate')
            if day not in targets or day in found: continue
            warrants = git_read(repo,commit,'warrants.json')
            focus = git_read(repo,commit,'warrant-focus.json')
            manifest = git_read(repo,commit,'manifest.json')
            if matched_inputs(targets[day],warrants,focus,manifest):
                found[day] = (commit,warrants,focus,manifest)
        except (subprocess.CalledProcessError, KeyError, ValueError):
            continue
    dates = sorted(targets)
    evidence = {'legacyVersion':legacy['rules']['version'],'legacyStateSha256':legacy['stateSha256'],
                'requestedDates':dates,'matchedDates':sorted(found),'missingDates':sorted(set(dates)-set(found)),
                'basis':'Original public Git snapshots; every full input hash must match the archived ledger.',
                'snapshots':{d:{'commit':v[0],'inputSha256':targets[d]['inputSha256']} for d,v in found.items()}}
    report = lab.new_report(now, dates[0] if dates else now[:10], 'research', evidence)
    calendar = load_calendar(output)
    for day in dates:
        # Do not bridge missing historical inputs with a fictional next-day fill.
        if day not in found:
            report.update(status='retained',error='歷史輸入未核對，停止連續重算：'+day)
            break
        commit,warrants,focus,manifest = found[day]
        report = lab.build_lab(report,warrants['rows'],{s['id']:s for s in manifest['sources']},
                               focus,now,date.fromisoformat(day),calendar)
    report['message'] = '新版歷史重算研究；採原保存行情與訊號，不是當時已執行的新規則交易。'
    save(output/'warrant-lab-research.json', report)
    versions = read(output/'warrant-lab-versions.json') if (output/'warrant-lab-versions.json').exists() else {}
    versions.update(active=lab.RULES['version'],research='warrant-lab-research',replayCoverage=evidence)
    save(output/'warrant-lab-versions.json', versions)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repository',type=Path,required=True)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--legacy',type=Path,required=True)
    args=parser.parse_args()
    result=replay(args.repository,args.data,read(args.legacy))
    print(json.dumps({'days':result['dayCount'],'status':result['status'],'coverage':result['replayEvidence']},ensure_ascii=False))
