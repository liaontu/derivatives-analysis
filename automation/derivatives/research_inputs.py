"""Content-addressed paper-research inputs survive any later Git maintenance."""
from pathlib import Path
import gzip
import hashlib
import json
import re


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def save_input(output, day, warrants, focus, sources, expected):
    value = {'warrants':warrants,'focus':focus,'sources':sources}
    raw = encoded(value)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
        raise ValueError('Research input hash mismatch')
    path = output/'research-inputs'/day[:7]/(day+'-'+digest+'.json.gz')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if gzip.decompress(path.read_bytes()) != raw:
            raise ValueError('Immutable research input was modified')
    else:
        path.write_bytes(gzip.compress(raw,mtime=0))
    return {'date':day,'inputSha256':digest,'path':path.relative_to(output).as_posix(),
            'bytes':path.stat().st_size,'uncompressedBytes':len(raw)}


def load_input(output, day, expected):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or not re.fullmatch(r'[a-f0-9]{64}',expected):
        raise ValueError('Unsafe research input identity')
    path=output/'research-inputs'/day[:7]/(day+'-'+expected+'.json.gz')
    if not path.exists():return None
    raw=gzip.decompress(path.read_bytes())
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Research input integrity failure')
    return json.loads(raw)


def record_input(output, entry, warrants, focus, sources):
    row=save_input(output,entry['marketDate'],warrants,focus,sources,entry['inputSha256'])
    path=output/'research-inputs.json'
    index=json.loads(path.read_text(encoding='utf8')) if path.exists() else {'schemaVersion':1,'items':[]}
    entries={i['inputSha256']:i for i in index['items']};entries[row['inputSha256']]=row
    index['items']=sorted(entries.values(),key=lambda i:(i['date'],i['inputSha256']))
    path.write_bytes(encoded(index))
    return row
