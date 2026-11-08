"""Source freshness receipts, distinct from scheduler and publication success."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
try:
    from .trading_calendar import load_calendar
except ImportError:
    from trading_calendar import load_calendar

TAIPEI = timezone(timedelta(hours=8))


def read(path):
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}


def expected_day(calendar, now):
    local = now.astimezone(TAIPEI)
    day = local.date() - timedelta(days=local.hour * 60 + local.minute < 16 * 60 + 30)
    for _ in range(15):
        if calendar.is_open(day):
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError('No verified recent trading session')


def build(output, now):
    manifest = read(output / 'manifest.json')
    sources = {s['id']: s for s in manifest.get('sources', [])}
    try:
        expected, calendar_error = expected_day(load_calendar(output), now), None
    except ValueError as exc:
        expected, calendar_error = None, str(exc)
    groups = []
    for key, label, ids in [
        ('tw-derivatives', '台灣期貨／選擇權', ['futures', 'options', 'futures-institutional', 'options-institutional']),
        ('tw-warrants', '台灣權證行情／條款', ['twse-warrants','twse-volume','twse-volume-put','tpex-warrants','tpex-terms']),
        ('us-daily', '美國每日統計', ['us-futures','us-options']),
        ('us-weekly', 'CFTC 每週持倉', [k for k in sources if k.startswith('us-cot-')])]:
        records = [sources.get(k, {'id':k,'status':'unavailable'}) for k in ids]
        dates = [s.get('date') for s in records if s.get('date')]
        failed = [s['id'] for s in records if s.get('status') != 'ok']
        missing = [s['id'] for s in records if not s.get('date')]
        due = expected if key.startswith('tw-') else None
        stale = [s['id'] for s in records if due and s.get('date','') < due]
        if not due and key.startswith('us-'):
            limit = 14 if key == 'us-weekly' else 4
            stale = [s['id'] for s in records if s.get('date') and (now.date()-datetime.fromisoformat(s['date']).date()).days > limit]
        state = 'unknown' if not records or (key.startswith('tw-') and not expected) else 'failed' if failed or missing else 'awaiting' if stale else 'current'
        groups.append({'id':key,'label':label,'status':state,'expectedDate':due,'dataFrom':min(dates) if dates else None,
                       'dataThrough':max(dates) if dates else None,'failedSources':failed,'laggingSources':stale,
                       'reason':calendar_error if key.startswith('tw-') and calendar_error else
                       '來源尚未提供預期日期，或該次抓取未確認成功；保留原資料日。' if state!='current' else
                       '各來源依實際日期核對；不代表即時或全市場涵蓋。'})
    for key,label,file in [('branches','權證分點','check-branches.json'),('stock-institutional','現股法人','stock-institutional.json')]:
        data = read(output/file)
        if key=='branches':
            ranks=read(output/'warrant-branches.json');items=ranks.get('items',[])
            dates=sorted(r['date'] for r in items if r.get('date'))
            current=sum(bool(expected and (r.get('date') or '')>=expected and r.get('status')=='ok') for r in items)
            groups.append({'id':key,'label':label,'dataFrom':dates[0] if dates else None,'dataThrough':dates[-1] if dates else None,
                           'expectedDate':expected,'status':'current' if items and current==len(items) and data.get('outcome')=='success' and ranks.get('status')=='ok' else 'awaiting',
                           'reason':f'本次收錄 {len(items)} 檔中 {current} 檔排行日期已核對；僅買賣超排行，非全部權證或全部分點。'})
            continue
        day = data.get('date') or ((data.get('modules') or {}).get('branches') or {}).get('dataDate')
        groups.append({'id':key,'label':label,'dataFrom':day,'dataThrough':day,'expectedDate':expected,
                       'status':'current' if day and expected and day>=expected and (data.get('outcome')=='success' or data.get('status')=='ok') else 'awaiting',
                       'reason':'分點僅部分排行；法人僅來源涵蓋市場。各自核對，無資料不當作零。'})
    return {'schemaVersion':1,'checkedAt':now.isoformat(timespec='seconds'),'expectedTaiwanDate':expected,
            'contentUpdatedAt':manifest.get('updatedAt'),'groups':groups,
            'schedule':'台灣 16:30 首次核對、18:30／20:30 補查；美國早晨獨立核對；分點晚間更新。這是檢查目標，來源未發布或排程延遲會明列。'}


def record(output, now=None):
    now = now or datetime.now(timezone.utc)
    result = build(output, now)
    old = read(output/'update-health.json')
    day = now.astimezone(TAIPEI).date().isoformat()
    observations = {r['day']:r for r in old.get('dailyObservations', [])}
    row = observations.get(day, {'day':day,'checks':0,'firstCurrentAt':{}})
    row['checks'] += 1
    for g in result['groups']:
        if g['status']=='current' and (not g.get('expectedDate') or g['expectedDate']==day):
            row['firstCurrentAt'].setdefault(g['id'], {'at':result['checkedAt'],'dataDate':g['dataThrough']})
    row['latestCheckAt'] = result['checkedAt']
    observations[day] = row
    result['dailyObservations'] = [observations[d] for d in sorted(observations)[-60:]]
    (output/'update-health.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    return result
