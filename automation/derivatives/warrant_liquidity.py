"""Daily official warrant quotation-quality CSV, through the public form.

One sequential query per issuer; no authentication, parallel scraping or captcha.
Thirty-day summaries are not backdated as daily observations or execution quotes.
"""
import argparse
from collections import Counter,defaultdict
import csv
from datetime import datetime,timezone
import gzip,hashlib,http.cookiejar,io,json,re,time
from html.parser import HTMLParser
from pathlib import Path
from statistics import median
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener,HTTPCookieProcessor

if __package__:
    from . import collect as c,market_history as h
else:
    import collect as c,market_history as h

URL='https://warrants.twse.com.tw/'
FIELDS={'權證收盤價*':'close','成交股數*':'volumeUnits','履約價':'strike','行使比例':'ratio','剩餘天數':'days',
    '買賣價差比':'spreadPct','委買隱波':'bidIv','委賣隱波':'askIv','收盤價隱波':'closeIv',
    '委託買量':'bidVolume','委託賣量':'askVolume','買賣價差檔數':'spreadTicks',
    '一段時間買1 IV':'periodBidIv','一段時間賣1 IV':'periodAskIv','一段時間買1 IV最大變動值':'periodMaxBidIvChange',
    '一段時間賣1 IV最大變動值':'periodMaxAskIvChange'}
LIMITS=['證交所盤中取樣／日終彙整，並非三邊同步報價，不能保證成交；委託量不是本人可成交量。',
    'Period=30 是官方近一個月彙總，未拆成過去每一天；逐日原始值從實際保存日開始累積。',
    '只以同日代號、名稱、履約價和行使比例配對既有名錄。缺買價、賣價、量或隱波保留空值；未推定為零。',
    '官方 CSV 未提供逐筆最佳買賣價格、同步時間戳及完整深度，無法以本表完成套利成交驗證。',
    '沿用官方搜尋預設剩餘天數 0～730，days 欄保持官方定義，不當成日曆天；完整覆蓋以本地名錄逐檔比對，未出現在來源的權證另列。']


class Form(HTMLParser):
    def __init__(self,text):
        super().__init__();self.values={};self.options=defaultdict(list);self.select=None;self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='input' and a.get('name') and a.get('type','text') not in ('submit','button','checkbox','radio'):self.values[a['name']]=a.get('value','')
        if tag=='select':self.select=a.get('name')
        if tag=='option' and self.select:
            self.options[self.select].append(a.get('value',''))
            if self.select not in self.values or 'selected' in a:self.values[self.select]=a.get('value','')
    def handle_endtag(self,tag):
        if tag=='select':self.select=None


def parse_csv(raw,day,issuer):
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not {'權證代號','權證名稱','發行人',*FIELDS}<=set(reader.fieldnames or []):raise ValueError('Quotation CSV schema changed')
    rows=[];seen=set()
    for r in reader:
        code=r['權證代號'].strip()
        if not re.fullmatch(r'[0-9A-Z]{6}',code) or code in seen or None in r:raise ValueError('Bad quotation CSV identity/width')
        seen.add(code);row={'date':day,'code':code,'name':r['權證名稱'].strip(),'issuer':r['發行人'].strip(),'issuerCode':issuer,
            'moneyness':r.get('價內外程度'),'periodDays':30}
        for source,target in FIELDS.items():
            value=r[source].strip();row[target]=c.number(value)
            if value not in ('','--','-','N/A') and row[target] is None:raise ValueError('Invalid quotation number: '+source)
        row['close']=row['close'] if row['volumeUnits'] and row['volumeUnits']>0 and row['close'] and row['close']>0 else None
        rows.append(row)
    if not rows:raise ValueError('Empty quotation export')
    return rows


class Client:
    def __init__(self):
        self.op=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()));self.op.addheaders=[('User-Agent','DerivativeInsight public daily research')]
        self.last=0
    def request(self,url,body=None):
        time.sleep(max(0,1.2-(time.monotonic()-self.last)));self.last=time.monotonic()
        with self.op.open(url,urlencode(body).encode() if body is not None else None,timeout=120) as res:
            raw=res.read(12_000_001);final=res.url;ctype=res.headers.get('Content-Type','')
        if len(raw)>12_000_000:raise ValueError('Quotation response exceeds bounded size')
        if '/Error' in final:raise ValueError('Official quotation site returned its error page')
        return raw,final,ctype
    def enter(self):
        raw,url,_=self.request(URL);form=Form(raw.decode('utf-8-sig'))
        if 'Query.aspx' not in url:raw,url,_=self.request(url,form.values|{'BTNConfirm':'繼續'})
        if not url.endswith('/Query.aspx'):raise ValueError('Unexpected public query route')
        self.page=raw.decode('utf-8-sig');self.form=Form(self.page)
        values=[v for v in self.form.options['COMPANY'] if v.strip()]
        if not values or len(set(v.strip() for v in values))!=len(values) or any(not re.fullmatch(r'[0-9A-Z]{4}',v.strip()) for v in values):raise ValueError('Invalid issuer selection values')
        return values
    def query(self,value,day):
        body=Form(self.page).values|{'BtnQuery':'搜尋','COMPANY':value,'Period':'30','PageSizeSelector':'50'}
        raw,url,_=self.request(URL+'Query.aspx',body);page=raw.decode('utf-8-sig')
        dates=re.findall(r'id="(?:LastUpdate1|StarredLastUpdate1)"[^>]*>(.*?)<',page)
        if dates!=[day,day]:raise ValueError('Quotation date differs from catalogue: '+str(dates))
        keys=list(dict.fromkeys(re.findall(r"key: '([A-Z_0-9]+)'",page)))
        raw,_,ctype=self.request(url,Form(page).values|{'BtnCsv':'下載CSV','ExportColumnKeys':','.join(keys)})
        if 'text/csv' not in ctype:raise ValueError('Expected public CSV export')
        return raw


def summary(rows):
    return {'rows':len(rows),'withSpread':sum(r['spreadPct'] is not None for r in rows),
        'twoSidedIv':sum(r['bidIv'] is not None and r['askIv'] is not None for r in rows),
        'twoSidedVolume':sum((r['bidVolume'] or 0)>0 and (r['askVolume'] or 0)>0 for r in rows),
        'spreadMedian':median(v) if (v:=[r['spreadPct'] for r in rows if r['spreadPct'] is not None]) else None}


def run(output,client=None):
    now=datetime.now(timezone.utc).isoformat(timespec='seconds');cat=c.read(output/'warrants.json')['rows'];days={r['date'] for r in cat}
    if len(days)!=1:raise ValueError('Wait for both warrant markets to have the same date')
    day=days.pop();old=h.read(output/'warrant-liquidity.json',{});client=client or Client()
    sources={};errors=[];all_rows=[];values=client.enter()
    for value in values:
        issuer=value.strip();key='warrant-liquidity/days/'+day+'/'+issuer+'.json';e={}
        try:
            saved=h.read(output/key)
            if saved:
                e=saved['source'];raw=gzip.decompress((output/e['path']).read_bytes())
                if hashlib.sha256(raw).hexdigest()!=e['sha256']:raise ValueError('Quotation source hash changed')
                if e.get('date')!=day or e.get('issuer')!=issuer:raise ValueError('Quotation cached identity mismatch')
            else:
                raw=client.query(value,day)
                parse_csv(raw,day,issuer)
                digest=hashlib.sha256(raw).hexdigest();path='warrant-liquidity/raw/'+digest+'.csv.gz'
                h.write_gzip(output/path,raw);e={'url':URL+'Query.aspx','path':path,'sha256':digest,'retrievedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),'issuer':issuer,'periodDays':30,'date':day}
            rows=parse_csv(raw,day,issuer);payload={'date':day,'source':e,'rowCount':len(rows)};h.save(output/key,payload)
            sources[issuer]={'path':key,'sha256':hashlib.sha256(h.encode(payload)).hexdigest(),'rows':len(rows),'status':'ok','source':e}
            all_rows.extend(rows);print(json.dumps({'issuer':issuer,'rows':len(rows),'date':day}),flush=True)
        except Exception as exc:
            errors.append({'issuer':issuer,'checkedAt':now,'error':str(exc)[:400]});print(json.dumps(errors[-1]),flush=True)
            h.save(output/'warrant-liquidity/failures'/(day+'-'+issuer+'.json'),errors[-1])
            if isinstance(exc,HTTPError) and exc.code in (401,403,429):break
    bycode={r['code']:r for r in cat};groups=defaultdict(list);unmatched=[];seen=set()
    for row in all_rows:
        if row['code'] in seen:raise ValueError('Quotation duplicated across issuers')
        seen.add(row['code']);q=bycode.get(row['code'])
        if not q or row['name']!=q['name'] or row['strike']!=q.get('strike') or row['ratio']!=q.get('ratio'):
            unmatched.append(row['code']);continue
        groups[q['underlying']].append({**row,'underlying':q['underlying'],'underlyingName':q['underlyingName'],'market':q['market'],'cp':q.get('cp'),'expiry':q.get('expiry')})
    items=[]
    for code,rows in sorted(groups.items()):
        path='warrant-liquidity/stocks/'+code+'.json';doc={'date':day,'rows':rows,'limitations':LIMITS};h.save(output/path,doc)
        items.append({'code':code,'name':rows[0]['underlyingName'],'path':path,'sha256':hashlib.sha256(h.encode(doc)).hexdigest(),**summary(rows)})
    matched_codes={r['code'] for rows in groups.values() for r in rows}
    missing=[{'code':r['code'],'name':r['name'],'market':r['market'],'underlying':r['underlying'],
        'reason':'terms-or-name-mismatch' if r['code'] in seen else 'not-in-source'} for r in cat if r['code'] not in matched_codes]
    directory=c.read(output/'stock-warrant-directory.json') or {};stock_codes={r['code'] for r in directory.get('items',[])}
    ordinary=[r for r in cat if r['underlying'] in stock_codes]
    ordinary_missing=[r for r in missing if r['underlying'] in stock_codes]
    completion=h.read(output/'market-completion.json',{})
    suspended={r['code'] for r in completion.get('warrantVolumeGaps',[]) if r['date']==day and r['reason']=='underlying-suspended'}
    stock_coverage={'catalogue':len(ordinary),'matched':sum(r['code'] in matched_codes for r in ordinary),
        'missing':len(ordinary_missing),'specialStructureMissing':sum(bool(re.fullmatch(r'\d{5}[BCXY]',r['code'])) for r in ordinary_missing),
        'underlyingSuspendedMissing':sum(r['code'] in suspended for r in ordinary_missing),
        'basis':'目前官方個股名錄交集；牛熊證及停牌另列，不稱為全部欄位完整'}
    result={'version':'official-warrant-quality-v1','date':day,'checkedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),'status':'partial' if errors else 'ok','error':'; '.join(r['error'] for r in errors) or None,
        'summary':summary(all_rows),'matched':sum(r['rows'] for r in items),'catalogue':len(cat),'issuerCount':len(values),'sources':sources,'errors':errors,
        'unmatchedCodes':unmatched,'missingCatalogueRows':missing,'individualStocks':stock_coverage,
        'queryDefaults':{'periodDays':30,'minDays':0,'maxDays':730},
        'items':items,'limitations':LIMITS,'historyType':'saved-daily-snapshots-with-30-day-aggregates'}
    h.save(output/'warrant-liquidity.json',result)
    # Historical evidence is the compressed, hash-verified official CSV. Do not
    # duplicate all normalized rows daily or point historical hashes at mutable shards.
    archived={**result,'items':[{k:v for k,v in item.items() if k not in ('path','sha256')} for item in items],
              'dataFormat':'immutable-raw-csv-per-issuer'}
    prior=h.read(output/'warrant-liquidity/days'/day/'index.json',{})
    if prior and {k:v for k,v in prior.items() if k!='checkedAt'}=={k:v for k,v in archived.items() if k!='checkedAt'}:
        archived['checkedAt']=prior['checkedAt']
    h.save(output/'warrant-liquidity/days'/day/'index.json',archived)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));args=p.parse_args()
    try:
        result=run(args.data);print(json.dumps(result['summary']));raise SystemExit(0 if result['status']=='ok' else 1)
    except Exception as exc:
        old=h.read(args.data/'warrant-liquidity.json',{})
        h.save(args.data/'warrant-liquidity.json',{**old,'status':'retained' if old else 'unavailable','error':str(exc),'checkedAt':datetime.now(timezone.utc).isoformat()})
        raise
