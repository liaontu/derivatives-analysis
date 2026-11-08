"""Fill missing underlying prices from verified same-day official cash closes."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
if __package__:
    from . import market_history as h,market_completion as mc,collect as c,cross_market as cross
else:
    import market_history as h,market_completion as mc,collect as c,cross_market as cross


def run(output):
    index=h.read(output/'market-completion.json');base=index['base']
    twse,proof=cross.cash_snapshot(output,base)
    supplement=h.read(output/'market-supplement.json');otc_entry=supplement['documents']['cash-TPEX-'+base]
    otc=mc.verified(output,otc_entry)['rows'];baselines={'TWSE':twse['quotes'],'TPEX':{r['code']:r for r in otc}}
    conflicts=[]
    old=h.read(output/'stock-history-repair.json',{'repairs':[]});known={(r['code'],r['date']):r for r in old['repairs']}
    for stock in index['stocks']:
        path=output/'warrant-history'/(stock['code']+'.json')
        if not path.exists():continue
        doc=h.read(path);points={d:p for d,p in doc.get('underlyingPrices',[])}
        source=mc.verified(output,stock);values={r[0]:r[1] for r in source['rows']}
        # Keep the referenced evidence immutable across later daily stock updates.
        source_path='market-completion/repair-sources/'+stock['sha256']+'.json'
        values[base]=baselines[stock['market']].get(stock['code'],{}).get('close')
        changed=False
        for day,price in values.items():
            if price is None or price<=0:continue
            previous=points.get(day)
            if previous is None or previous<=0:
                points[day]=price;changed=True
                if day!=base:h.save(output/source_path,source)
                known[(stock['code'],day)]={'code':stock['code'],'market':stock['market'],'date':day,'close':price,
                    'source':proof if day==base and stock['market']=='TWSE' else otc_entry if day==base else {'path':source_path,'sha256':stock['sha256']}}
            elif abs(previous-price)>.011:conflicts.append({'code':stock['code'],'date':day,'existing':previous,'official':price})
            elif (stock['code'],day) in known and day!=base and '/stocks/' in known[(stock['code'],day)]['source']['path']:
                h.save(output/source_path,source)
                known[(stock['code'],day)]['source']={'path':source_path,'sha256':stock['sha256']}
        if changed:
            doc['underlyingPrices']=[[d,points[d]] for d in sorted(points)][-60:];c.save(path,doc)
    result={'version':'cash-close-repair-v1','status':'ok' if not conflicts else 'partial','start':index['start'],'end':index['end'],
        'checkedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),'repairs':list(known.values()),'conflicts':conflicts,
        'note':'只補缺值；同日官方收盤與既有有效價格衝突時保留原值並列示。未填停牌價、未上市櫃價格或含息總報酬。'}
    h.save(output/'stock-history-repair.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));args=p.parse_args();r=run(args.data)
    print({'filled':len(r['repairs']),'conflicts':len(r['conflicts'])})
    if r['conflicts']:raise SystemExit(1)
