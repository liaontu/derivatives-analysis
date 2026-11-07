"""Build same-session Taiwan standard single-stock call/put observations."""
from datetime import date, datetime, timezone
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import urllib.request
import urllib.error

if __package__:
    from .collect import fetch, number, request_bytes
    from .publication import save_text
else:
    from collect import fetch, number, request_bytes
    from publication import save_text


LIST_URL = 'https://openapi.taifex.com.tw/v1/SSOLists'
LIST_CSV = 'https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=SSOLists'
ROSTER_ALIASES = {'股票選擇權商品契約代碼': 'Contract', '標的證券': 'UnderlyingStock',
                  '證券代號': 'StockCode', '標的證券簡稱': 'StockName', '標的證券種類': 'Type'}
ROSTER_TYPES = {'上市普通股標的證券', '上櫃普通股標的證券', '上市ETF標的證券', '上櫃ETF標的證券'}
INDEX = 'stock-option-watch'
TWSE_DAILY = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999&response=json&date='


def serialize(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def stock_contracts(raw):
    if not isinstance(raw, list) or not raw:
        raise ValueError('股票選擇權名單必須是非空表格')
    contracts = {}
    for original in raw:
        if not isinstance(original, dict):
            raise ValueError('股票選擇權名單資料列格式不符')
        row = {}
        for key, value in original.items():
            key = ROSTER_ALIASES.get(str(key).strip().lstrip('\ufeff'), str(key).strip().lstrip('\ufeff'))
            value = str(value).strip() if value is not None else ''
            if key in row and row[key] != value:
                raise ValueError('股票選擇權名單中英文欄位內容衝突')
            row[key] = value
        if not {'Type', 'Contract', 'StockCode', 'StockName'} <= set(row):
            raise ValueError('股票選擇權名單欄位未識別：' + ', '.join(sorted(row)))
        if row['Type'] not in ROSTER_TYPES:
            raise ValueError('股票選擇權標的類別未識別：' + row['Type'])
        if row['Type'] not in ('上市普通股標的證券', '上櫃普通股標的證券'):
            continue
        symbol, code = row['Contract'], row['StockCode']
        if not (re.fullmatch(r'[A-Z0-9]{2}O', symbol) and re.fullmatch(r'[0-9]{4}', code)):
            raise ValueError('Unrecognized standard stock option')
        if symbol in contracts or not row['StockName']:
            raise ValueError('Ambiguous stock option identity')
        contracts[symbol] = (code, row['StockName'])
    if not contracts:
        raise ValueError('Empty stock option list')
    return contracts


def decode_roster(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp950')
    if text.lstrip().startswith(('[', '{')):
        return json.loads(text), 'JSON'
    return list(csv.DictReader(io.StringIO(text, newline=None))), 'CSV'


def request_roster(url):
    request = urllib.request.Request(url, headers={'User-Agent':'derivatives-research/1','Accept':'application/json,text/csv,text/plain'})
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(2_000_001)
        metadata = {'httpStatus':response.status, 'contentType':response.headers.get('Content-Type','')}
    return raw, metadata


def fetch_contracts(output=None, requester=None, sleep=time.sleep, attempts=2):
    """Recognize both official schemas, with a validated free official CSV fallback."""
    requester = requester or request_roster
    failures, evidence = [], None
    for attempt in range(attempts):
        for url in (LIST_URL, LIST_CSV):
            raw, metadata, rows = b'', {}, None
            try:
                raw, metadata = requester(url)
                if metadata.get('httpStatus') != 200:
                    raise ValueError('Unexpected HTTP status')
                content_type = metadata.get('contentType','').lower().split(';')[0].strip()
                if content_type not in ('application/json','text/json','text/csv','application/csv','text/plain','application/octet-stream','application/vnd.ms-excel'):
                    raise ValueError('Unexpected roster content-type')
                if len(raw)>2_000_000:
                    raise ValueError('Roster exceeds size limit')
                rows, format_name = decode_roster(raw)
                contracts = stock_contracts(rows)
                return contracts, {'url': url, 'format': format_name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                   'rows': len(rows), 'stockContracts': len(contracts), **metadata,
                                   'fields': sorted(rows[0]), 'fallback': url != LIST_URL,
                                   'attempt':attempt+1, 'failureEvidence':evidence}
            except Exception as exc:
                failures.append({'url':url, 'attempt':attempt+1, **metadata, 'error':type(exc).__name__+': '+str(exc)[:180],
                                 'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw),
                                 'sample':raw[:2048].decode('utf8',errors='replace'),
                                 'fields':sorted(rows[0]) if isinstance(rows,list) and rows and isinstance(rows[0],dict) else None})
                if output is not None:
                    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
                    evidence = INDEX+'/failures/'+stamp.replace(':','').replace('+','-')+'.json'
                    save_text(output/evidence, serialize({'checkedAt':stamp,'attempts':failures}))
        if attempt+1 < attempts:
            sleep(120)
    error = ValueError('兩個官方名單來源均未通過核對；'+ '；'.join(f['error'] for f in failures))
    error.evidence = evidence
    raise error


def positive(v):
    return v if type(v) in (int, float) and math.isfinite(v) and v > 0 else None


def stock_market_quotes(raw, day, codes):
    if raw.get('date') != day.replace('-', '') or raw.get('stat') != 'OK':
        raise ValueError('TWSE spot date mismatch')
    columns = {'證券代號', '成交股數', '收盤價', '最後揭示買價', '最後揭示賣價'}
    tables = [t for t in raw['tables'] if columns <= set(t.get('fields', []))]
    if len(tables) != 1:
        raise ValueError('Unknown TWSE quote columns')
    table = tables[0]
    found = {}
    for values in table['data']:
        row = dict(zip(table['fields'], values))
        code = row['證券代號']
        if code not in codes:
            continue
        if code in found:
            raise ValueError('Ambiguous TWSE spot quote')
        spot = positive(number(row['收盤價'])) if positive(number(row['成交股數'])) else None
        bid, ask = positive(number(row['最後揭示買價'])), positive(number(row['最後揭示賣價']))
        if bid and ask and bid > ask:
            bid = ask = None
        found[code] = {'spot': spot, 'bid': bid, 'ask': ask, 'spotSource': 'TWSE'}
    if not found:
        raise ValueError('No matched TWSE stock quotes')
    return found


def spot_at(output, code, day):
    path = output / 'warrant-history' / (code + '.json')
    if not path.exists():
        return None
    source = read(path)
    if source.get('underlying') != code:
        return None
    values = {positive(p) for d, p in source['underlyingPrices'] if d == day}
    return values.pop() if len(values) == 1 else None


def leg(row):
    volume = row.get('volume')
    last = positive(row.get('last')) if positive(volume) else None
    bid, ask = positive(row.get('bid')), positive(row.get('ask'))
    if bid and ask and bid > ask:
        bid = ask = None
    return {'last': last, 'volume': volume, 'oi': row.get('oi'), 'bid': bid, 'ask': ask}


def build(output, options, contracts, stock_quotes=None):
    day = date.fromisoformat(options['date']).isoformat()
    stocks = {}
    for symbol, (code, name) in sorted(contracts.items()):
        quote = (stock_quotes or {}).get(code)
        if quote is None:
            quote = {'spot': spot_at(output, code, day), 'bid': None, 'ask': None, 'spotSource': 'underlyingHistory'}
        stocks[symbol] = {'code': code, 'name': name, **quote, 'pairs': []}
    groups = {}
    for row in options['rows']:
        symbol, month, strike = row['contract'], row['month'], positive(row['strike'])
        if symbol not in stocks or row['date'] != day or row['session'] != '一般':
            continue
        if not (strike and re.fullmatch(r'20\d{2}(?:0[1-9]|1[0-2])(?:W[1-5])?', month or '') and row['cp'] in ('C', 'P')):
            continue
        sides = groups.setdefault((symbol, month, strike), {})
        if row['cp'] in sides:
            raise ValueError('Duplicate option series in official report')
        sides[row['cp']] = leg(row)
    if not groups:
        raise ValueError('No matching regular-session stock option series')
    for (symbol, month, strike), sides in sorted(groups.items()):
        stocks[symbol]['pairs'].append({'month': month, 'strike': strike, **sides})
    return {'date': day, 'optionsHash': options.get('provenance', {}).get('sha256'),
            'rosterHash': hashlib.sha256(serialize(sorted(contracts.items())).encode()).hexdigest(),
            'spotUrl': TWSE_DAILY + day.replace('-', ''), 'stocks': stocks}


def publish(output, options, contracts, roster=None):
    day = date.fromisoformat(options['date']).isoformat()
    stock_quotes = stock_market_quotes(fetch(TWSE_DAILY + day.replace('-', '')), day,
                                       {code for code, _ in contracts.values()})
    snapshot = build(output, options, contracts, stock_quotes)
    if roster is not None:
        snapshot['roster'] = roster
    day = snapshot['date']
    save_text(output / INDEX / (day + '.json'), serialize(snapshot))
    path = output / (INDEX + '.json')
    previous = read(path) if path.exists() else {}
    dates = sorted(set(previous.get('dates', [])) | {day})[-60:]
    save_text(path, serialize({'dates': dates, 'latest': dates[-1], 'status': 'ok',
                              'rosterUrl': roster['url'] if roster else LIST_URL, 'roster': roster}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    args = parser.parse_args()
    try:
        contracts, roster = fetch_contracts(args.data)
        options = read(args.data / 'options.json')
        publish(args.data, options, contracts, roster)
        print(serialize({'date': options['date'], 'status': 'ok', 'roster': roster}))
    except Exception as exc:
        path = args.data / (INDEX + '.json')
        previous = read(path) if path.exists() else {'dates': [], 'latest': None, 'rosterUrl': LIST_URL}
        previous['status'] = 'retained'
        previous['error'] = str(exc)[:400]
        previous['failureEvidence'] = getattr(exc, 'evidence', None)
        previous['lastAttemptAt'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        save_text(path, serialize(previous))
        raise


if __name__ == '__main__':
    main()
