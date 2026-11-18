from contextlib import nullcontext
from datetime import date
from io import BytesIO
import gzip
import time
from urllib.error import HTTPError
import pytest
from automation.derivatives import collect, option_history


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
