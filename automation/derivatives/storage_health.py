"""Measure published data independently of Git object storage; never rewrite history."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import argparse,json,subprocess


def measure(output):
    files=[p for p in output.rglob('*') if p.is_file() and p.name!='storage-health.json']
    sizes=sorted(((p.stat().st_size,p.relative_to(output).as_posix()) for p in files),reverse=True)
    total=sum(s for s,_ in sizes);objects={}
    try:
        result=subprocess.check_output(['git','-C',str(output),'count-objects','-v'],text=True,stderr=subprocess.DEVNULL)
        objects=dict(line.split(': ',1) for line in result.splitlines())
    except (OSError,subprocess.CalledProcessError):pass
    path=output/'storage-health.json';old=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    history={r['date']:r for r in old.get('daily',[])}
    history[day]={'date':day,'dataBytes':total,'files':len(files)}
    result={'schemaVersion':1,'status':'warning' if total>=700*1024**2 else 'ok','dataBytes':total,'dataFiles':len(files),
            'gitPackedBytes':int(objects.get('size-pack',0))*1024,'researchSnapshots':sum('/research-inputs/' in '/'+str(p).replace('\\','/') for p in files),
            'largest':[{'path':n,'bytes':s} for s,n in sizes[:12]],'daily':[history[d] for d in sorted(history)[-60:]],
            'policy':['目前完整權證由標的分片提供；warrants.json 僅為格式索引，Python 讀取器相容重建。',
                      '官方來源保留必要當期快照；相同內容不因檢查時間而重寫。',
                      '研究輸入依月份分組，以內容 SHA-256 命名、gzip 壓縮且不可覆寫；重放優先使用封存，不依賴 Git 留存。',
                      '既有研究樣本、帳戶及報告版本保留。此流程不進行 orphan、強制推送或刪除 Git 歷史。',
                      '網站資料與 Git 物件容量分開量測，700 MiB 提醒檢查；未啟用付費資料、付費儲存或大型 runner。']}
    path.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));a=p.parse_args()
    r=measure(a.data);print(json.dumps({k:r[k] for k in ('dataBytes','dataFiles','researchSnapshots','status')}))
