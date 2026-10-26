"""Taiwan market-session dates from TWSE holidays and explicit closure notices."""
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path
import re
import urllib.request

HOLIDAYS = 'https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json&queryYear='
NEWS = 'https://www.twse.com.tw/rwd/zh/news/newsList?response=json'
SEED = Path(__file__).with_name('taiwan-calendar.json')


class CalendarUnavailable(ValueError):
    pass


def day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('INVALID_CALENDAR_DATE')
    return date.fromisoformat(value)


def parse_year(raw, year):
    if raw.get('stat') != 'ok' or raw.get('queryYear') != year or len(raw.get('data', [])) < 5:
        raise CalendarUnavailable('CALENDAR_YEAR_UNVERIFIED')
    closed, opened = {}, {}
    for value, name, note in raw['data']:
        if day(value).year != year or value in closed or value in opened:
            raise CalendarUnavailable('CALENDAR_ROW_INVALID')
        if re.search('開始交易|最後交易', name):
            opened[value] = name
        elif re.search('放假|補假|無交易|休市', name+' '+note):
            closed[value] = name
        else:
            raise CalendarUnavailable('CALENDAR_ROW_UNKNOWN')
    return {'closed': closed, 'opened': opened, 'source': HOLIDAYS+str(year-1911), 'exceptions': {}}


def closure_notices(raw, year):
    if raw.get('stat') != 'ok' or not isinstance(raw.get('data'), list) or not raw['data']:
        raise CalendarUnavailable('CALENDAR_NOTICES_UNVERIFIED')
    found = {}
    for row in raw['data']:
        if len(row) < 5 or not isinstance(row[1], str):
            raise CalendarUnavailable('CALENDAR_NOTICE_ROW_INVALID')
        title = row[1]
        if not title.startswith('臺灣證券交易所集中交易市場') or '休市' not in title:
            continue
        match = re.fullmatch(r'臺灣證券交易所集中交易市場(\d{3})年(\d{1,2})月(\d{1,2})日休市(?:一天|一日|1天|1日)', title)
        if not match:
            raise CalendarUnavailable('CALENDAR_CLOSURE_REQUIRES_REVIEW')
        y, m, d = map(int, match.groups())
        value = date(y+1911, m, d).isoformat()
        if y+1911 == year:
            found[value] = {'title': title, 'announced': row[2], 'newsId': row[3], 'source': NEWS}
    return found


class TradingCalendar:
    def __init__(self, data):
        if data.get('schemaVersion') != 1 or not isinstance(data.get('years'), dict):
            raise CalendarUnavailable('CALENDAR_SCHEMA_INVALID')
        self.data = data
        self.years = {}
        for year, entry in data['years'].items():
            closed = set(entry['closed']) | set(entry.get('exceptions', {}))
            opened = set(entry['opened'])
            if any(day(d).year != int(year) for d in closed | opened) or closed & opened:
                raise CalendarUnavailable('CALENDAR_DATE_INVALID')
            self.years[int(year)] = (closed, opened)

    def is_open(self, value):
        value = day(value)
        if value.year not in self.years:
            raise CalendarUnavailable('CALENDAR_YEAR_MISSING')
        closed, opened = self.years[value.year]
        key = value.isoformat()
        return key not in closed and (key in opened or value.weekday() < 5)

    @lru_cache(maxsize=2048)
    def age(self, source, today):
        source, today = day(source), day(today)
        if source > today:
            return -1
        self.is_open(source)
        self.is_open(today)
        count, cursor = 0, source
        while cursor < today:
            cursor += timedelta(days=1)
            count += self.is_open(cursor)
        return count

    def fresh(self, source, today, limit=4):
        try:
            return 0 <= self.age(source, today) <= limit
        except (ValueError, TypeError):
            return False


@lru_cache(maxsize=1)
def default_calendar():
    return TradingCalendar(json.loads(SEED.read_text(encoding='utf-8')))


def load_calendar(output=None):
    path = Path(output)/'trading-calendar.json' if output else None
    return TradingCalendar(json.loads(path.read_text(encoding='utf-8'))) if path and path.exists() else default_calendar()


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'FlowScope-calendar/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise CalendarUnavailable('CALENDAR_RESPONSE_TOO_LARGE')
    return json.loads(raw)


def calendar_for_run(output, year, save, fetcher=None):
    previous = load_calendar(output)
    current, status = previous, {'status': 'verified', 'basis': 'TWSE annual calendar and explicit market-wide closure notices'}
    if fetcher:
        try:
            entry = parse_year(fetcher(HOLIDAYS+str(year-1911)), year)
            # News lists can roll over; preserve previously verified closure notices.
            entry['exceptions'] = {**previous.data['years'].get(str(year), {}).get('exceptions', {}), **closure_notices(fetcher(NEWS), year)}
            current = TradingCalendar({'schemaVersion': 1, 'years': {**previous.data['years'], str(year): entry}})
        except Exception as exc:
            status = {'status': 'retained', 'reason': 'Calendar refresh failed; using the last verified dates', 'failureType': type(exc).__name__}
    if year not in current.years:
        status = {'status': 'unavailable', 'reason': 'Calendar year is not verified', 'year': year}
    save(Path(output)/'trading-calendar.json', current.data)
    return current, status
