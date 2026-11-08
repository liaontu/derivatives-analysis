"""Period-scoped completeness review. Later observations do not fill older days."""
from collections import Counter
from pathlib import Path
if __package__:
    from . import market_history as h
else:
    import market_history as h

LABELS={'no-trade':'官方零成交','suspended':'官方停牌','not-yet-listed':'尚未上市櫃','source-row-missing':'來源未列行情','traded-close-missing':'有量但缺收盤'}


def build(output,start,end):
    completion=h.read(output/'market-completion.json',{})
    coverage=[r for r in completion.get('coverage',[]) if start<=r['date']<=end]
    if not coverage:return None
    final=max(r['date'] for r in coverage);latest=[r for r in coverage if r['date']==final];counts=Counter()
    for r in latest:
        for k,v in r.get('priceReasons',{}).items():counts[k]+=len(v)
    from hashlib import sha256
    reference=completion['references'];raw=(output/reference['path']).read_bytes()
    if sha256(raw).hexdigest()!=reference['sha256']:raise ValueError('Completion reference hash mismatch')
    import json
    refs=json.loads(raw)
    if __package__:
        from .supplement_actions import state_for
    else:
        from supplement_actions import state_for
    stock_markets={r['code']:r['market'] for r in completion.get('stocks',[])}
    history_counts=Counter()
    for path in (output/'warrant-history').glob('*.json'):
        if path.stem not in stock_markets:continue
        doc=h.read(path)
        for q in doc['quotes'].values():
            for day,price,volume in q['rows']:
                if not start<=day<=end:continue
                if volume is None:
                    known=state_for(refs.get('suspensions',[]),stock_markets[path.stem],path.stem,day)
                    history_counts['underlying-suspended' if known else 'unexplained-volume']+=1
                elif volume>0 and price is None:history_counts['no-official-close']+=1
    actions={(r['market'],r['code'],r['date']):r for r in refs.get('capitalActions',[]) if start<=r['date']<=end}
    extra=h.read(output/'warrant-extra-history.json',{})
    fixed=sum(r.get('fixed',0) for d,r in extra.get('days',{}).items() if start<=d<=end)
    paths=sorted((output/'warrant-liquidity/days').glob('*/index.json')) if (output/'warrant-liquidity/days').exists() else []
    indices=[h.read(p) for p in paths if start<=p.parent.name<=end]
    quality=max(indices,key=lambda r:r['date']) if indices else None
    rows=[['現股行情／收盤',f'{sum(r["quoteRows"] for r in latest):,} 列／{sum(r["priced"] for r in latest):,} 有成交價',final],
          ['缺收盤原因','、'.join(LABELS.get(k,k)+' '+str(v) for k,v in counts.items()) or '無','零成交／停牌均不補造價格'],
          ['法人未列個股',str(sum(len(r['missingInstitutionalCodes']) for r in latest))+' 檔','未列不等於零，不能推定沒有交易'],
          ['上市權證價量回補',str(fixed)+' 筆權證日（含非個股標的）','官方同日同名配對；特殊結構不加入一般認購售模型'],
          ['減資／面額變更',str(len(actions))+' 件生效事件','舊版價格報酬未還原，受影響股票另查官方參考價'],
          ['權證流動性每日保存',str(len(indices))+'／'+str(len({r['date'] for r in coverage}))+' 個交易日','近月彙總不能補為逐日報價']]
    rows.append(['權證歷史尚無價量',f'標的停牌 {history_counts["underlying-suspended"]:,}；原因未明缺量 {history_counts["unexplained-volume"]:,}；有量缺收盤 {history_counts["no-official-close"]:,} 筆權證日','逐日對照官方停牌區間；保留空值，不假造成交'])
    cash_repair=h.read(output/'stock-history-repair.json',{})
    cash_fills=[r for r in cash_repair.get('repairs',[]) if start<=r['date']<=end]
    rows.append(['現股歷史補值',str(len(cash_fills))+' 筆股票日','同日官方收盤補進權證歷史；不補停牌或未上市櫃價格'])
    if quality:
        s=quality['summary'];rows.append(['最新官方權證報價品質',f'{s["rows"]:,} 檔；有價差比 {s["withSpread"]:,}；有雙邊委託量 {s["twoSidedVolume"]:,}',quality['date']+'；取樣值非保證成交'])
    warrant_gaps=[r for r in completion.get('warrantVolumeGaps',[]) if r['date']==final]
    if warrant_gaps:rows.append(['最新日權證成交量缺值',str(len(warrant_gaps))+' 檔，其中 '+str(sum(r['reason']=='underlying-suspended' for r in warrant_gaps))+' 檔標的停牌','無官方成交列仍留白，不填成零'])
    return {'version':'report-data-review-v1','asOf':final,'rows':rows,'warrantHistoryGapCounts':dict(history_counts),
        'capitalActions':[{k:r.get(k) for k in ('market','code','name','date','stopDate','action','before','reference','newShareRatio','cashReturned')} for r in actions.values()],
        'liquidity':{'date':quality['date'],'summary':quality['summary'],'status':quality['status']} if quality else None,
        'notes':['全部來源收集成功不表示所有欄位存在。舊研究規則與樣本不因補資料而混版。',
            '公司行動與停牌會影響原始價格報酬、股證方向比較及訊號回測；未還原的跨事件報酬不能用來推論策略優劣。',
            '完整券商分點、投資人部位、同步深度與帳戶實際費用仍非本網站免費日報能完整還原。'],
        'sourceHashes':{'completion':sha256(h.encode({'coverage':coverage,'references':reference})).hexdigest(),
            'reference':reference['sha256'],'quality':sha256(h.encode(quality)).hexdigest() if quality else None},
        'historyType':'reconstructed-audit'}


def html(review):
    if not review:return ''
    from html import escape
    e=lambda v:escape(str(v if v is not None else '—'))
    rows=''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in r)+'</tr>' for r in review['rows'])
    events=''.join('<tr>'+''.join('<td>'+e(r.get(k))+'</td>' for k in ('date','market','code','name','action','reference'))+'</tr>' for r in review['capitalActions'])
    return '<h3>本期補資料與可判讀範圍</h3><div class="scroll"><table><tr><th>資料</th><th>實際覆蓋</th><th>限制／日期</th></tr>'+rows+'</table></div><details><summary>影響價格比較的公司行動與限制</summary><div class="scroll"><table><tr><th>生效日</th><th>市場</th><th>代號</th><th>名稱</th><th>事件</th><th>官方參考價</th></tr>'+events+'</table></div><ul>'+''.join('<li>'+e(t)+'</li>' for t in review['notes'])+'</ul></details>'
