import gzip
import hashlib
import json

import pytest

from automation.derivatives import stock_institutional as inst, supplement_sources as supplement
from automation.derivatives.daily_reports import read, save

NOW = '2026-11-19T09:00:00+00:00'


def setup_rosters(path, *, fetched='2026-11-19T05:00:00+00:00', reported='1151119'):
    documents = {}
    for market, code in [('TWSE', '2330'), ('TPEX', '6488')]:
        raw = ([{'公司代號': code, '公司名稱': '測試', '出表日期': reported}]
               if market == 'TWSE' else
               [{'SecuritiesCompanyCode': code, 'CompanyName': '測試', 'Date': reported}])
        encoded = json.dumps(raw, ensure_ascii=False).encode()
        rawpath = path / (market + '.raw.gz')
        rawpath.write_bytes(gzip.compress(encoded))
        proof = {'path': rawpath.name, 'sha256': hashlib.sha256(encoded).hexdigest(),
                 'url': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L' if market == 'TWSE'
                 else 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
                 'httpStatus': 200, 'validated': True, 'fetchedAt': fetched}
        key = 'companies-' + market
        target = path / (key + '.json')
        save(target, {'id': key, 'rows': supplement.companies(encoded, market), 'evidence': proof})
        documents[key] = {'status': 'ok', 'path': target.name,
                          'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'source': proof}
    save(path / 'market-supplement.json', {'documents': documents})
    save(path / 'manifest.json', {'sources': [
        {'id': market + '-warrants', 'date': '2026-11-19', 'status': 'ok'} for market in ('twse', 'tpex')]})
    save(path / 'warrants.json', {'rows': [
        {'code': code, 'underlying': underlying, 'market': market, 'cp': 'C', 'date': '2026-11-19'}
        for code, underlying, market in [('012345', '2330', 'TWSE'), ('712345', '6488', 'TPEX'),
                                         ('012346', '0050', 'TWSE'), ('012347', 'IX0001', 'TWSE')]]})


def test_verified_rosters_allow_institutional_membership_without_issue_directory(tmp_path):
    setup_rosters(tmp_path)
    result = inst.verified_membership(tmp_path, NOW)
    assert [r['code'] for r in result['items']] == ['2330', '6488']
    assert result['membershipQuoteDate'] == '2026-11-19'
    assert len(result['membershipSources']) == 2
    assert not (tmp_path / 'stock-warrant-directory.json').exists()


@pytest.mark.parametrize('fetched,reported', [
    ('2026-11-18T08:59:59+00:00', '1151119'),
    ('2026-11-19T09:00:01+00:00', '1151119'),
    ('2026-11-19T05:00:00+00:00', '1151120'),
    ('2026-11-19T05:00:00+00:00', '1151101')])
def test_unverified_roster_dates_are_rejected(tmp_path, fetched, reported):
    setup_rosters(tmp_path, fetched=fetched, reported=reported)
    with pytest.raises(ValueError):
        inst.verified_membership(tmp_path, NOW)


@pytest.mark.parametrize('corruption', ['raw', 'normalized', 'path', 'source', 'rows'])
def test_roster_proof_is_checked_before_use(tmp_path, corruption):
    setup_rosters(tmp_path)
    index = read(tmp_path / 'market-supplement.json')
    entry = index['documents']['companies-TWSE']
    doc = read(tmp_path / entry['path'])
    if corruption == 'raw':
        (tmp_path / entry['source']['path']).write_bytes(gzip.compress(b'[]'))
    elif corruption == 'normalized':
        (tmp_path / entry['path']).write_bytes(b'{}')
    elif corruption == 'path':
        entry['path'] = '../outside.json'
    else:
        if corruption == 'source':
            entry['source']['url'] = 'https://example.invalid/roster'
            doc['evidence'] = entry['source']
        else:
            doc['rows'][0]['code'] = '0050'
        save(tmp_path / entry['path'], doc)
        entry['sha256'] = hashlib.sha256((tmp_path / entry['path']).read_bytes()).hexdigest()
    save(tmp_path / 'market-supplement.json', index)
    with pytest.raises(ValueError):
        inst.verified_membership(tmp_path, NOW)


@pytest.mark.parametrize('change', ['misaligned', 'failed', 'stale'])
def test_quote_membership_must_be_verified(tmp_path, change):
    setup_rosters(tmp_path)
    manifest = read(tmp_path / 'manifest.json')
    if change == 'misaligned':
        manifest['sources'][0]['date'] = '2026-11-18'
    elif change == 'failed':
        manifest['sources'][0]['status'] = 'retained'
    else:
        for row in manifest['sources']:
            row['date'] = '2026-11-01'
    save(tmp_path / 'manifest.json', manifest)
    with pytest.raises(ValueError):
        inst.verified_membership(tmp_path, NOW)


def test_membership_failure_preserves_previous_snapshot_and_skips_network(tmp_path):
    setup_rosters(tmp_path, reported='1151101')
    save(tmp_path / 'stock-warrant-directory.json', {'status': 'retained'})
    save(tmp_path / 'stock-institutional.json', {'dates': ['2026-11-18'], 'latest': '2026-11-18'})
    def no_fetch(url):
        pytest.fail('Invalid membership must not start requests')
    result = inst.collect(tmp_path, fetcher=no_fetch, now=NOW)
    assert result['status'] == 'retained'
    assert result['latest'] == '2026-11-18' and result['dates'] == ['2026-11-18']
