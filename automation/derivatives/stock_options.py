"""Build same-session Taiwan standard single-stock call/put observations."""
from datetime import date
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

if __package__:
    from .collect import fetch, number
    from .publication import save_text
else:
    from collect import fetch, number
    from publication import save_text


LIST_URL = 'https://openapi.taifex.com.tw/v1/SSOLists'
INDEX = 'stock-option-watch'
TWSE_DAILY = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999&response=json&date='


def serialize(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def stock_contracts(raw):
    contracts = {}
    for row in raw:
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
    return {'last': last, 'volume': volume, 'bid': bid, 'ask': ask}


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


def publish(output, options, contracts):
    day = date.fromisoformat(options['date']).isoformat()
    stock_quotes = stock_market_quotes(fetch(TWSE_DAILY + day.replace('-', '')), day,
                                       {code for code, _ in contracts.values()})
    snapshot = build(output, options, contracts, stock_quotes)
    day = snapshot['date']
    save_text(output / INDEX / (day + '.json'), serialize(snapshot))
    path = output / (INDEX + '.json')
    previous = read(path) if path.exists() else {}
    dates = sorted(set(previous.get('dates', [])) | {day})[-60:]
    save_text(path, serialize({'dates': dates, 'latest': dates[-1], 'status': 'ok', 'rosterUrl': LIST_URL}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    args = parser.parse_args()
    try:
        contracts = stock_contracts(fetch(LIST_URL))
        publish(args.data, read(args.data / 'options.json'), contracts)
    except Exception:
        path = args.data / (INDEX + '.json')
        previous = read(path) if path.exists() else {'dates': [], 'latest': None, 'rosterUrl': LIST_URL}
        previous['status'] = 'retained'
        save_text(path, serialize(previous))
        raise


if __name__ == '__main__':
    main()
