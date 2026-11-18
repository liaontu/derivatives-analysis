"""Bounded official dated fallback when TAIFEX OpenAPI lags a closed session."""
from datetime import date, datetime, timedelta, timezone
import json
from urllib.error import HTTPError
from urllib.parse import urlencode

if __package__:
    from . import market_history as history, fcm_history, supplement_sources
else:
    import market_history as history, fcm_history, supplement_sources

NAMES = (*history.DOWNLOADS, 'futures-fcm', 'options-fcm', 'pcr', 'tpex-warrants')
PCR_URL = 'https://www.taifex.com.tw/cht/3/pcRatio'
PCR_FIELDS = ['日期','賣權成交量','買權成交量','買賣權成交量比率%',
              '賣權未平倉量','買權未平倉量','買賣權未平倉量比率%']


def closed_day(now, calendar):
    local = datetime.fromisoformat(now).astimezone(timezone(timedelta(hours=8)))
    # Allow the afternoon publication window; never request today's unfinished day.
    day = local.date() - timedelta(days=int(local.hour < 16))
    for _ in range(20):
        if calendar.is_open(day):
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError('No verified recently closed Taiwan session')


def parse_pcr(raw, day):
    parser = supplement_sources.Tables()
    parser.feed(supplement_sources.decode(raw))
    tables = [table for table in parser.tables if PCR_FIELDS in table]
    if len(tables) != 1:
        raise ValueError('P/C table header missing or ambiguous')
    matches = [row for row in tables[0] if row and history.iso_date(row[0]) == day]
    if len(matches) != 1 or len(matches[0]) != 7:
        raise ValueError('Requested P/C date missing or duplicated')
    values = [supplement_sources.numeric(value) for value in matches[0][1:]]
    pv, cv, ratio, poi, coi, oi_ratio = values
    if min(values) < 0 or cv <= 0 or coi <= 0:
        raise ValueError('Invalid P/C totals')
    if abs(pv / cv * 100 - ratio) > .0051 or abs(poi / coi * 100 - oi_ratio) > .0051:
        raise ValueError('P/C ratios do not reconcile')
    return [dict(zip(['date','putVolume','callVolume','volumeRatio','putOi','callOi','oiRatio'], [day,*values]))]


class DatedFallback:
    def __init__(self, output, now, calendar, client=None):
        self.client = client or history.Client(output)
        self.day = closed_day(now, calendar)
        self.now = now
        self.blocked = False

    def load(self, name, feeds):
        if name not in NAMES:
            raise ValueError('Unsupported dated source')
        if self.blocked:
            raise ValueError('Dated sources stopped after access/rate-limit response')
        day = self.day
        # Current-day downloads can be republished during the afternoon. A
        # midnight cache cutoff would keep a valid morning file with yesterday's
        # rows and prevent every later run from seeing the new session.
        fresh_after = datetime.fromisoformat(self.now).astimezone(timezone.utc).isoformat(timespec='seconds')
        try:
            if name == 'tpex-warrants':
                if __package__:
                    from .backfill_volume_reports import OTC_URL, otc_quotes
                else:
                    from backfill_volume_reports import OTC_URL, otc_quotes
                url = OTC_URL + urlencode({'date':day.replace('-','/'),'type':'Daily','response':'json'})
                raw, proof = self.client.get(url, fresh_after=fresh_after)
                document = json.loads(raw)
                for table in document.get('tables',[]):
                    if any(len(r)!=len(table.get('fields',[])) for r in table.get('data',[])):
                        raise ValueError('OTC daily price row width mismatch')
                rows = otc_quotes(document, date.fromisoformat(day))
                # Dated quotes do not provide contract terms. Keep their original
                # dates; downstream reports still require same-day terms.
            elif name in history.DOWNLOADS:
                route, extra = history.DOWNLOADS[name]
                url = 'https://www.taifex.com.tw/cht/3/' + route
                fields = {**extra,'queryStartDate':day.replace('-','/'),'queryEndDate':day.replace('-','/')}
                raw, proof = self.client.get(url, fields, fresh_after)
                rows = history.parse_csv(name, raw, day, day)
            elif name.endswith('-fcm'):
                url = fcm_history.sources(name)[0][1]
                raw, proof = self.client.get(url, fresh_after=fresh_after)
                source_rows = fcm_history.parse(raw)
                rows = [r for r in source_rows if r['date'] == day]
                if not rows:
                    raise ValueError('Requested FCM day not yet published; latest source date: '+max(r['date'] for r in source_rows))
                kind = name.removesuffix('-fcm')
                quotes = feeds.get(kind, {})
                if quotes.get('date') != day:
                    raise ValueError('No same-day quotes to reconcile FCM volume')
                quote_contract, fcm_contract = ('TX','TXF') if kind == 'futures' else ('TXO','TXO')
                selected = [r for r in quotes.get('rows',[]) if r['contract'] == quote_contract and '/' not in r['month']]
                if not all(any(r.get('session') == session for r in selected) for session in ('一般','盤後')):
                    raise ValueError('FCM reconciliation requires both quote sessions')
                expected = 2 * sum(r['volume'] for r in selected)
                actual = sum(r['volume'] for r in rows if r['contract'] == fcm_contract)
                if not rows or expected <= 0 or actual != expected:
                    raise ValueError('FCM volume does not match same-day official quote total')
                proof = {**proof,'validation':{'contract':fcm_contract,'twoSidedQuoteVolume':expected,'matched':True}}
            else:
                raw, proof = self.client.get(PCR_URL, fresh_after=fresh_after)
                rows = parse_pcr(raw, day)
            if not rows or any(r.get('date') != day for r in rows):
                raise ValueError('Dated source does not match requested session')
            return rows, proof
        except HTTPError as exc:
            if exc.code in (401,403,429):
                self.blocked = True
            raise
