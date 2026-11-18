from contextlib import nullcontext
from datetime import date
from io import BytesIO
import gzip
import time
from urllib.error import HTTPError
import pytest
from automation.derivatives import collect, option_history, market_history, dated_latest


def test_option_gateway_retry_stays_on_same_official_request(monkeypatch):
    calls, sleeps = [], []
    def open_request(request, **kwargs):
        calls.append((request.full_url, request.data))
        if len(calls) == 1:
            raise HTTPError(request.full_url, 520, 'gateway', {}, None)
        return nullcontext(BytesIO(b'actual report'))
    monkeypatch.setattr(option_history.urllib.request, 'urlopen', open_request)
    monkeypatch.setattr(option_history.time, 'sleep', sleeps.append)
    assert option_history.download('2026-11-17','2026-11-17') == b'actual report'
    assert len(calls) == 2 and calls[0] == calls[1] and sleeps == [2]


@pytest.mark.parametrize('code,count', [(401,1),(403,1),(429,1),(520,3)])
def test_option_retry_is_bounded_and_respects_access_refusals(monkeypatch,code,count):
    calls=[]
    def fail(request,**kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url,code,'failure',{},None)
    monkeypatch.setattr(option_history.urllib.request,'urlopen',fail)
    monkeypatch.setattr(option_history.time,'sleep',lambda _:None)
    with pytest.raises(HTTPError): option_history.download('2026-11-17','2026-11-17')
    assert len(calls)==count


def volume_report(day='20261117'):
    return {'stat':'OK','date':day,'tables':[{'fields':['證券代號','成交股數','成交金額'],
        'data':[['12345B','1000','500']]}]}


def test_volume_recovery_requires_exact_verified_quote_day():
    calls=[]
    def fetch(url): calls.append(url); return volume_report()
    feeds={'twse-warrants':{'date':'2026-11-17','status':'ok'}}
    result,url=collect.dated_warrant_volume('twse-volume-extra',None,feeds,fetch,'now',date(2026,11,18),None)
    assert calls==[url] and url.endswith('&date=20261117')
    assert result['date']=='2026-11-17' and result['rows'][0]['volume']==1
    with pytest.raises(ValueError,match='does not match'):
        collect.dated_warrant_volume('twse-volume-extra',None,feeds,lambda _:volume_report('20261118'),'now',date(2026,11,18),None)
    with pytest.raises(ValueError,match='No verified'):
        collect.dated_warrant_volume('twse-volume-extra',None,{'twse-warrants':{'date':'2026-11-17','status':'retained'}},fetch,'now',date(2026,11,18),None)
    with pytest.raises(ValueError,match='columns changed'):
        collect.dated_warrant_volume('twse-volume-extra',None,feeds,lambda _:{'stat':'OK','date':'20261117','tables':[]},'now',date(2026,11,18),None)


def test_compressed_large_source_checks_decompressed_size():
    raw=b'official public report'*5000
    response=BytesIO(gzip.compress(raw));response.headers={'Content-Encoding':'gzip'}
    assert collect.read_response(response,len(raw),time.monotonic()+10)==raw
    response=BytesIO(gzip.compress(raw));response.headers={'Content-Encoding':'gzip'}
    with pytest.raises(ValueError,match='size limit'):
        collect.read_response(response,len(raw)-1,time.monotonic()+10)


def test_slow_trickle_source_cannot_exceed_total_download_budget(monkeypatch):
    ticks=iter([0,121]);monkeypatch.setattr(collect.time,'monotonic',lambda:next(ticks))
    response=BytesIO(b'x'*100000);response.headers={}
    with pytest.raises(TimeoutError,match='total download deadline'):
        collect.read_response(response,200000,120)


def test_history_gateway_retry_preserves_post_and_validates_cache(tmp_path,monkeypatch):
    calls=[]
    def open_request(request,**kwargs):
        calls.append((request.full_url,request.data))
        if len(calls)==1:raise HTTPError(request.full_url,520,'gateway',{},None)
        response=BytesIO(gzip.compress(b'official dated report'))
        response.headers={'Content-Encoding':'gzip','Content-Type':'text/csv'};response.status=200
        return response
    monkeypatch.setattr(market_history,'urlopen',open_request)
    monkeypatch.setattr(market_history.time,'sleep',lambda _:None)
    client=market_history.Client(tmp_path)
    raw,proof=client.get('https://www.taifex.com.tw/cht/3/futDataDown',{'queryStartDate':'2026/11/18'})
    assert raw==b'official dated report' and len(calls)==2 and calls[0]==calls[1]
    client.accept(proof)
    assert client.get(proof['url'],proof['parameters'])[0]==raw and len(calls)==2


@pytest.mark.parametrize('code,count',[(401,1),(403,1),(429,1),(520,3)])
def test_history_retry_stops_at_access_refusal_or_budget(tmp_path,monkeypatch,code,count):
    calls=[]
    def fail(request,**kwargs):
        calls.append(request.full_url);raise HTTPError(request.full_url,code,'failure',{},None)
    monkeypatch.setattr(market_history,'urlopen',fail)
    monkeypatch.setattr(market_history.time,'sleep',lambda _:None)
    with pytest.raises(HTTPError):market_history.Client(tmp_path).get('https://www.taifex.com.tw/cht/3/futDataDown',{})
    assert len(calls)==count and not list(tmp_path.rglob('*.json'))


def test_dated_otc_quotes_do_not_fabricate_terms_or_accept_wrong_day(tmp_path):
    import json
    fields=['代號','名稱','開市價','最高價','最低價','收市價','漲跌','成交量(股)','筆數','成交金額','標的代號','標的名稱','標的/指數收盤','標的/指數漲跌']
    doc={'stat':'ok','date':'20261118','tables':[{'fields':fields,'data':[['712345','權證','1','2','1','1.5','0.5','2000','2','3000','6488','環球晶','500','1']]}]}
    class Client:
        def get(self,url,**kwargs):return json.dumps(doc).encode(),{'url':url}
    fallback=object.__new__(dated_latest.DatedFallback)
    fallback.client=Client();fallback.day='2026-11-18';fallback.now='2026-11-18T09:00:00Z';fallback.blocked=False
    rows,proof=fallback.load('tpex-warrants',{})
    assert rows[0]['date']=='2026-11-18' and rows[0]['volume']==2 and rows[0]['price']==1.5
    assert 'strike' not in rows[0] and 'termsDate' not in rows[0] and '2026%2F11%2F18' in proof['url']
    doc['date']='20261117'
    with pytest.raises(ValueError,match='date/status'):fallback.load('tpex-warrants',{})


def test_afternoon_fcm_reloads_validated_morning_cache(tmp_path,monkeypatch):
    from automation.derivatives import fcm_history
    url=fcm_history.sources('options-fcm')[0][1]
    client=market_history.Client(tmp_path)
    import hashlib
    morning=b'morning-previous-session';sha=hashlib.sha256(morning).hexdigest()
    proof={'url':url,'parameters':None,'fetchedAt':'2026-11-18T02:00:00+00:00','sha256':sha,'path':f'market-history/sources/{sha}.raw.gz'}
    market_history.write_gzip(tmp_path/proof['path'],morning);client.accept(proof)
    calls=[]
    def download(request,**kwargs):
        calls.append(request.full_url);response=BytesIO(b'afternoon-current-session')
        response.headers={};response.status=200;return response
    monkeypatch.setattr(market_history,'urlopen',download)
    monkeypatch.setattr(market_history.time,'sleep',lambda _:None)
    def parse(raw):
        assert raw==b'afternoon-current-session'
        return [{'date':'2026-11-18','contract':'TXO','volume':30}]
    monkeypatch.setattr(fcm_history,'parse',parse)
    fallback=object.__new__(dated_latest.DatedFallback)
    fallback.client=client;fallback.day='2026-11-18';fallback.now='2026-11-18T17:00:00+08:00';fallback.blocked=False
    quotes={'options':{'date':'2026-11-18','rows':[{'contract':'TXO','month':'202611','session':'一般','volume':10},{'contract':'TXO','month':'202611','session':'盤後','volume':5}]}}
    rows,evidence=fallback.load('options-fcm',quotes)
    assert calls==[url] and len(rows)==1 and evidence['validation']['matched'] is True
