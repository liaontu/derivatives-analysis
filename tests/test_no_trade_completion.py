from datetime import date
import pytest
from automation.derivatives import warrant_branches as w


def fixture(code='081105', day='2026/10/22', buy='10', sell='3', net='7', branch='0039003800310042'):
    return f'''<title>主力買賣超-{code}</title><h1>測試權證({code}) 券商分點</h1>
    單位：張 最後更新日：{day}<table><tr><td>買超券商</td><td>賣超券商</td></tr>
    <tr><td><a href="/z/zc/zco/zco0/zco0.djhtm?a={code}&amp;b={branch}">測試-竹北</a></td>
    <td>{buy}</td><td>{sell}</td><td>{net}</td><td>70%</td>
    <td><a href="/z/zc/zco/zco0/zco0.djhtm?a={code}&amp;b=9824">測試-向上</a></td>
    <td>0</td><td>7</td><td>7</td><td>70%</td></tr></table>'''

def quote(code='081105', **kwargs):
    return dict(code=code, name='測試權證', market='TWSE', cp='C', date='2026-10-22', volume=12,
                expiry='2027-01-01', **kwargs)

def empty_fixture(code='081105', day='2026/10/22'):
    return (f'<h1>測試權證({code}) 券商分點</h1>單位：張 最後更新日：{day}'
            f'買超券商 賣超券商 查無(測試權證{code})券商分點-進出明細')

def test_explicit_empty_rankings_require_same_day_official_zero_and_keep_history():
    now, today = '2026-10-24T10:00:00+00:00', date(2026, 10, 24)
    old = w.update_warrant(quote(), None, fixture(), now, today)
    q = {**quote(), 'date': '2026-10-23', 'volume': 0}
    result = w.update_warrant(q, old, empty_fixture(day='2026/10/23'), now, today)
    day = result['history'][0]
    assert result['status'] == 'ok' and result['rankingState'] == 'no_trade'
    assert day['rows'] == [] and day['quoteVolume'] == 0
    assert all(day[k] is None for k in ['buyTotal', 'sellTotal', 'buyGap', 'sellGap'])
    assert day['noTradeEvidence']['date'] == '2026-10-23'
    assert result['history'][1] == old['history'][0]
    assert result['completeBranchCoverage'] is result['missingBranchMeansZero'] is False
    traded = w.update_warrant({**q, 'volume': 12}, result, fixture(day='2026/10/23'), now, today)
    assert 'rankingState' not in traded and len(traded['history'][0]['rows']) == 2

@pytest.mark.parametrize('change', [dict(volume=None), dict(volume=1), dict(volume=False),
                                   dict(volume='0'), dict(date='2026-10-21'), dict(market='UNKNOWN')])
def test_empty_source_is_not_proof_of_zero_without_matching_official_quote(change):
    with pytest.raises(ValueError, match='same-day official zero'):
        w.update_warrant({**quote(), 'volume': 0, **change}, None, empty_fixture(),
                         '2026-10-24T10:00:00+00:00', date(2026, 10, 24))

@pytest.mark.parametrize('html', [empty_fixture().replace('查無', '尚無'),
                                 empty_fixture().replace('測試權證081105', '測試權證030001')])
def test_blank_or_other_security_empty_page_never_becomes_zero(html):
    with pytest.raises(ValueError):
        w.parse_page(html, '081105')

def test_verified_no_trade_clears_pending_failure_and_is_labeled_in_index(tmp_path):
    w.save(tmp_path/'warrants.json', {'rows': [{**quote(), 'volume': 0}, quote('030001')]})
    w.save(tmp_path/'warrant-branches.json', {'items': [], 'progress': {
        'remaining': ['081105'], 'failures': {'081105': {'count': 3, 'retryAfter': '2026-10-23T10:00:00+00:00'}}}})
    fetch = lambda url: 'User-agent: *\nAllow: /' if url.endswith('/robots.txt') else empty_fixture()
    assert w.collect(tmp_path, {'topCount': 0, 'watchlist': ['081105']}, fetch,
                     '2026-10-24T10:00:00+00:00', 0, lambda _: None) == []
    ix = w.read(tmp_path/'warrant-branches.json')
    assert ix['status'] == 'ok' and 'progress' not in ix
    assert ix['items'][0]['rankingState'] == 'no_trade' and ix['items'][0]['count'] == 0

def test_large_directory_stream_can_finish_past_default_deadline_but_stays_bounded(monkeypatch):
    import io
    from automation.derivatives import collect as c
    tick = [0]
    class SlowResponse(io.BytesIO):
        headers = {}
        def read1(self, size):
            tick[0] += 150
            return super().read1(size)
    monkeypatch.setattr(c.time, 'monotonic', lambda: tick[0])
    monkeypatch.setattr(c.time, 'sleep', lambda _: None)
    monkeypatch.setattr(c.urllib.request, 'urlopen', lambda *a, **k: SlowResponse(b'[]'))
    with pytest.raises(TimeoutError, match='deadline'):
        c.request_bytes('https://openapi.twse.com.tw/v1/opendata/t187ap37_L', attempts=1)
    assert c.request_bytes('https://openapi.twse.com.tw/v1/opendata/t187ap37_L',
                           total_seconds=300, attempts=1) == b'[]'

def test_large_download_never_retries_source_rejection(monkeypatch):
    from urllib.error import HTTPError
    from automation.derivatives import collect as c
    calls = []
    def rejected(*args, **kwargs):
        calls.append(1)
        raise HTTPError('https://openapi.twse.com.tw/', 429, 'limit', {}, None)
    monkeypatch.setattr(c.urllib.request, 'urlopen', rejected)
    with pytest.raises(HTTPError):
        c.request_bytes('https://openapi.twse.com.tw/', total_seconds=300, attempts=2)
    assert len(calls) == 1

def test_coverage_is_date_aligned_partition_and_preserves_history(tmp_path):
    from automation.derivatives import collect as c, warrant_directory as wd
    from automation.derivatives.governance import digest
    def seed(day):
        rows=[{'code':str(i),'underlying':'2409','date':day,'market':'TWSE','name':'test'+str(i),'volume':v}
              for i,v in enumerate([10,20,0,None,5,12])]
        h=digest(rows)
        c.save(tmp_path/'stock-warrant-directory.json',{'status':'ok','checkedDay':day,'items':[{'code':'2409','hash':h}]})
        c.save(tmp_path/'stock-warrant-directory/2409.json',{'rows':rows,'hash':h})
        picks=[dict(rows[0],status='ok'),dict(rows[1],date='2026-11-04',status='ok'),dict(rows[5],status='ok',name='prior issue'),
               dict(rows[2],status='ok',rankingState='no_trade')]
        c.save(tmp_path/'warrant-branches.json',{'status':'ok','items':picks,'updatedAt':day})
    for d in ['2026-11-05','2026-11-06']:
        seed(d);assert wd.record_coverage(tmp_path)
    data=c.read(tmp_path/'stock-warrant-coverage.json');v=data['current'][0]
    assert len(data['history'])==2 and v['total']==6 and v['ranked']==1 and v['otherDateOrFailed']==1
    assert v['zeroWithoutRank']==1 and v['unknownVolume']==1 and v['tradedWithoutRank']==2
    c.save(tmp_path/'warrant-branches.json',{'status':'unavailable','items':[]})
    assert not wd.record_coverage(tmp_path)
    held=c.read(tmp_path/'stock-warrant-coverage.json')
    assert held['status']=='retained' and held['history']==data['history']


def test_valid_empty_page_waits_one_hour_and_resumes_without_day_long_lock(tmp_path):
    w.save(tmp_path/'warrants.json', {'rows': [quote()]})
    calls = []
    def fetch(url):
        if url.endswith('/robots.txt'):
            return 'User-agent: *\nAllow: /'
        calls.append(url)
        return empty_fixture() if len(calls) <= 3 else fixture()
    config = {'topCount': 1, 'watchlist': []}
    for clock in ('10:00', '10:30', '11:00', '12:00'):
        assert w.collect(tmp_path, config, fetch, f'2026-10-24T{clock}:00+00:00', 0, lambda _: None) == ['081105']
    failure = w.read(tmp_path/'warrant-branches.json')['progress']['failures']['081105']
    assert len(calls) == 3 and failure['failureType'] == 'AwaitingRankings'
    assert failure['retryAfter'] == '2026-10-24T13:00:00+00:00'
    assert w.collect(tmp_path, config, fetch, '2026-10-24T13:00:00+00:00', 0, lambda _: None) == []
    assert len(calls) == 4
