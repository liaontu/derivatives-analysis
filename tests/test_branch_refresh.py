from datetime import date
import json

import pytest

from automation.derivatives import warrant_branches as branches


class Calendar:
    def is_open(self, day):
        return True

    def fresh(self, value, today):
        if not value:
            return False
        day = value if isinstance(value, date) else date.fromisoformat(value)
        return 0 <= (today - day).days <= 4


def page(day):
    return f'''<p>測試權證(030001) 券商分點 買超券商 賣超券商 單位：張
最後更新日：{day.replace('-', '/')}</p><table><tr>
<td><a href="zco0.djhtm?a=030001&b=1234">測試分點</a></td>
<td>10</td><td>2</td><td>8</td><td>80</td>
<td></td><td></td><td></td><td></td><td></td>
</tr></table>'''


def seed(tmp_path, monkeypatch, source_day, volume=500, failure=None):
    calendar = Calendar()
    monkeypatch.setattr(branches, 'calendar_for_run', lambda *args: (calendar, 'ok'))
    quote = {'code': '030001', 'market': 'TWSE', 'cp': 'C',
             'date': '2026-11-18', 'volume': volume, 'expiry': '2027-01-01'}
    old = branches.update_warrant(quote, None, page(source_day),
                                  '2026-11-18T09:00:00+00:00', date(2026, 11, 18), calendar)
    branches.save(tmp_path / 'warrants.json', {'rows': [quote]})
    branches.save(tmp_path / 'warrant-branches/030001.json', old)
    branches.save(tmp_path / 'warrant-branches.json', {
        'items': [], 'progress': {'completedFor': {'030001': {'date': '2026-11-18', 'volume': 500}},
                                 'failures': {'030001': failure} if failure else {}}})
    return old


@pytest.mark.parametrize('source_day,continuation,volume,expected_requests', [
    ('2026-11-17', False, 500, 1),
    ('2026-11-17', True, 500, 0),
    ('2026-11-18', False, 500, 0),
    ('2026-11-18', False, 501, 1),
])
def test_later_run_refreshes_lag_but_continuation_and_current_data_are_reused(
        tmp_path, monkeypatch, source_day, continuation, volume, expected_requests):
    seed(tmp_path, monkeypatch, source_day, volume)
    requests = []

    def fetch(url):
        if url.endswith('/robots.txt'):
            return 'User-agent: *\nAllow: /'
        requests.append(url)
        return page('2026-11-18')

    pending = branches.collect(tmp_path, {'topCount': 1, 'watchlist': []}, fetcher=fetch,
                               now='2026-11-18T10:30:00+00:00', sleeper=lambda _: None,
                               continuation=continuation)
    result = json.loads((tmp_path / 'warrant-branches/030001.json').read_text(encoding='utf-8'))
    assert not pending
    assert len(requests) == expected_requests
    assert result['date'] == ('2026-11-18' if expected_requests else source_day)
    assert result['missingBranchMeansZero'] is False
    if expected_requests and source_day == '2026-11-17':
        assert [row['date'] for row in result['history']] == ['2026-11-18', '2026-11-17']


def test_lag_refresh_keeps_failure_cooldown(tmp_path, monkeypatch):
    failure = {'count': 3, 'retryAfter': '2026-11-19T09:00:00+00:00'}
    seed(tmp_path, monkeypatch, '2026-11-17', failure=failure)
    requests = []

    def fetch(url):
        requests.append(url)
        assert url.endswith('/robots.txt')
        return 'User-agent: *\nAllow: /'

    pending = branches.collect(tmp_path, {'topCount': 1, 'watchlist': []}, fetcher=fetch,
                               now='2026-11-18T10:30:00+00:00', sleeper=lambda _: None)
    result = json.loads((tmp_path / 'warrant-branches.json').read_text(encoding='utf-8'))
    assert pending == ['030001'] and len(requests) == 1
    assert result['progress']['failures']['030001'] == failure
    assert result['items'][0]['date'] == '2026-11-17'
    assert result['items'][0]['status'] == 'retained'
