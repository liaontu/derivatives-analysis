"""Free official daily derivatives data. Python stdlib only; no credentials.

Each feed keeps its own source date and last successful snapshot. Trading
sessions and institutional categories are never summed across overlapping rows.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import math
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
try:
    from .trading_calendar import calendar_for_run, fetch_json, default_calendar
    from .publication import WRITTEN
    from .warrant_catalog import publish_catalog
    from .us_sources import US_FEEDS, COT_DATASETS, cot_url, options_document, settlement_document, normalize_us
    from .warrant_history import archive
    from .warrant_spreads import collect_spreads
    from .warrant_focus import collect_focus
    from .warrant_lab import collect_lab
    from .warrant_lab_reports import collect_reports
    from .governance import provenance, collect_quality
except ImportError:  # Direct execution by the scheduled workflow.
    from trading_calendar import calendar_for_run, fetch_json, default_calendar
    from publication import WRITTEN
    from warrant_catalog import publish_catalog
    from us_sources import US_FEEDS, COT_DATASETS, cot_url, options_document, settlement_document, normalize_us
    from warrant_history import archive
    from warrant_spreads import collect_spreads
    from warrant_focus import collect_focus
    from warrant_lab import collect_lab
    from warrant_lab_reports import collect_reports
    from governance import provenance, collect_quality

TAIFEX = "https://openapi.taifex.com.tw/v1/"
TPEX = "https://www.tpex.org.tw/openapi/v1/"
FEEDS = {
    "futures": ("期貨每日行情", TAIFEX + "DailyMarketReportFut"),
    "options": ("選擇權每日行情", TAIFEX + "DailyMarketReportOpt"),
    "futures-institutional": ("期貨三大法人", TAIFEX + "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"),
    "options-institutional": ("選擇權法人買賣權分計", TAIFEX + "MarketDataOfMajorInstitutionalTradersDetailsOfCallsAndPutsBytheDate"),
    "futures-large": ("期貨大額交易人", TAIFEX + "OpenInterestOfLargeTradersFutures"),
    "options-large": ("選擇權大額交易人", TAIFEX + "OpenInterestOfLargeTradersOptions"),
    "futures-fcm": ("期貨商成交量・期貨", TAIFEX + "Daily_FUT"),
    "options-fcm": ("期貨商成交量・選擇權", TAIFEX + "Daily_OPT"),
    "pcr": ("台指選擇權 Put/Call 比", TAIFEX + "PutCallRatio"),
    "twse-warrants": ("上市權證行情與條款", "https://www.twse.com.tw/rwd/zh/stock/warrantStock?response=json"),
    "twse-volume": ("上市認購權證成交量", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=0999&response=json"),
    "twse-volume-put": ("上市認售權證成交量", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=0999P&response=json"),
    "tpex-terms": ("上櫃權證條款", TPEX + "tpex_warrant_issue"),
    "tpex-warrants": ("上櫃權證行情", TPEX + "tpex_warrant_daily_quts"),
    "tpex-underlyings": ("權證標的上櫃股票／ETF 收盤價", TPEX + "tpex_mainboard_daily_close_quotes"),
    **US_FEEDS,
}
ALIASES = {
    "契約代號": "Contract", "商品名稱": "ContractCode", "身份別": "Item",
    "最後成交價": "Last", "結算價": "SettlementPrice", "未沖銷契約數": "OpenInterest", "未沖銷契約量": "OpenInterest",
    "合計成交量": "Volume", "成交量": "Volume", "最後最佳買價": "BestBid", "最後最佳賣價": "BestAsk",
    "交易時段": "TradingSession", "履約價": "StrikePrice", "漲跌%": "%",
    "多方交易口數": "TradingVolume(Long)", "空方交易口數": "TradingVolume(Short)", "多空交易口數淨額": "TradingVolume(Net)",
    "多方未平倉口數": "OpenInterest(Long)", "空方未平倉口數": "OpenInterest(Short)", "多空未平倉口數淨額": "OpenInterest(Net)",
    "日期": "Date", "契約": "Contract", "商品名稱(契約名稱)": "ContractName",
    "買賣權": "CallPut", "到期月份(週別)": "SettlementMonth", "交易人類別": "TypeOfTraders",
    "前五大交易人買方數量": "Top5Buy", "前五大交易人賣方數量": "Top5Sell",
    "前十大交易人買方數量": "Top10Buy", "前十大交易人賣方數量": "Top10Sell",
    "全市場未沖銷部位數": "OIOfMarket",
    "期貨商代號": "FCMCode", "期貨商名稱": "FCMName", "商品": "Contract", "交易量": "Volume",
    "賣權成交量": "PutVolume", "買權成交量": "CallVolume", "買賣權成交量比率%": "PutCallVolumeRatio%",
    "賣權未平倉量": "PutOI", "買權未平倉量": "CallOI", "買賣權未平倉量比率%": "PutCallOIRatio%",
}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(str(value).replace(",", "").replace("%", "").strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def iso_date(value):
    parts = re.findall(r"\d+", str(value or ""))
    if len(parts) == 1 and len(parts[0]) in (7, 8):
        s = parts[0]
        parts = [s[:-4], s[-4:-2], s[-2:]]
    if len(parts) != 3:
        return None
    year, month, day = map(int, parts)
    try:
        return date(year + 1911 if year < 1911 else year, month, day).isoformat()
    except ValueError:
        return None


def cp(value):
    return {"買權": "C", "賣權": "P", "CALL": "C", "PUT": "P", "認購": "C", "認售": "P", "C": "C", "P": "P"}.get(str(value).strip().upper())


def decode(raw):
    text = raw.decode("utf-8-sig")
    if text.lstrip().startswith(("[", "{")):
        return json.loads(text)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or len(rows[0]) < 3:
        raise ValueError("Expected JSON or tabular CSV")
    return [{ALIASES.get(k, k): v for k, v in row.items()} for row in rows]


def request_bytes(url, transform=lambda raw: raw):
    request = urllib.request.Request(url, headers={
        "User-Agent": "DerivativeInsight/2.1 (daily public market research)",
        "Accept": "application/json,text/csv,text/html;q=0.9,*/*;q=0.5",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                raw = response.read(40_000_001)
            if len(raw) > 40_000_000:
                raise ValueError("Response exceeds size limit")
            return transform(raw)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def fetch(url):
    name = next((name for name, (_, source) in US_FEEDS.items() if source == url), None)
    if name == "us-options":
        return request_bytes(url, options_document)
    if name == "us-futures":
        return request_bytes(url, lambda raw: settlement_document(raw, request_bytes))
    return request_bytes(cot_url(name) if name in COT_DATASETS else url, decode)


def normalize(name, raw):
    if name in US_FEEDS:
        return normalize_us(name, raw, number, iso_date)
    if name in ("twse-volume", "twse-volume-put"):
        if not isinstance(raw, dict) or raw.get("stat") != "OK":
            raise ValueError("Missing TWSE warrant trading report")
        tables = [t for t in raw.get("tables", []) if {"證券代號", "成交股數", "成交金額"} <= set(t.get("fields", []))]
        if len(tables) != 1:
            raise ValueError("TWSE warrant trading columns changed")
        table = tables[0]
        rows = [dict(zip(table["fields"], row)) for row in table.get("data", [])]
        result = []
        for row in rows:
            units = number(row.get("成交股數"))
            result.append({"date": iso_date(raw.get("date")), "code": row.get("證券代號"),
                           "volume": units / 1000 if units is not None else None,
                           "value": number(row.get("成交金額"))})
        return result
    if name == "twse-warrants":
        fields = raw.get("fields", []) if isinstance(raw, dict) else []
        expected = ["權證代號", "權證簡稱", "收盤價", "漲跌", "標的代號", "標的名稱",
                    "收盤價/指數", "漲跌", "權證類型", "履約方式", "上市日期", "履約開始日",
                    "最後交易日", "履約截止日", "行使比例", "履約價格(元)/點數"]
        if fields[:16] != expected or raw.get("stat") != "OK":
            raise ValueError("TWSE warrant columns changed")
        day = iso_date(raw.get("date"))
        return [{"date": day, "code": str(r[0]).strip(), "name": r[1], "price": number(r[2]),
                 "underlyingPrice": number(r[6]),
                 "underlying": str(r[4]).strip(), "underlyingName": r[5], "cp": cp(r[8]),
                 "style": r[9], "lastTrade": iso_date(r[12]), "expiry": iso_date(r[13]),
                 "ratio": number(r[14]), "strike": number(r[15]), "market": "TWSE"}
                for r in raw.get("data", []) if len(r) >= 16]
    if not isinstance(raw, list) or not raw or not all(isinstance(r, dict) for r in raw):
        raise ValueError("Expected non-empty list of records")
    rows = []
    for r in raw:
        out = {"date": iso_date(r.get("Date"))}
        if name in ("futures", "options"):
            out.update(contract=r.get("Contract"), month=r.get("ContractMonth(Week)", r.get("SettlementMonth")),
                       session=r.get("TradingSession"), price=number(r.get("SettlementPrice")),
                       last=number(r.get("Last", r.get("Close"))), volume=number(r.get("Volume")),
                       oi=number(r.get("OpenInterest")), bid=number(r.get("BestBid")), ask=number(r.get("BestAsk")))
            if name == "options":
                out.update(cp=cp(r.get("CallPut")), strike=number(r.get("StrikePrice")))
            else:
                out["changePct"] = number(r.get("%"))
        elif name.endswith("-institutional"):
            out.update(contract=r.get("ContractCode"), investor=r.get("Item"), cp=cp(r.get("CallPut")))
            for field, source in {"buy": "TradingVolume(Long)", "sell": "TradingVolume(Short)", "net": "TradingVolume(Net)",
                                  "longOi": "OpenInterest(Long)", "shortOi": "OpenInterest(Short)", "netOi": "OpenInterest(Net)"}.items():
                out[field] = number(r.get(source))
        elif name.endswith("-large"):
            out.update(contract=r.get("Contract"), name=r.get("ContractName"), month=r.get("SettlementMonth"),
                       traderType=str(r.get("TypeOfTraders", "")), cp=cp(r.get("CallPut")))
            for field, source in {"top5Buy": "Top5Buy", "top5Sell": "Top5Sell", "top10Buy": "Top10Buy", "top10Sell": "Top10Sell", "oi": "OIOfMarket"}.items():
                out[field] = number(r.get(source))
        elif name.endswith("-fcm"):
            out.update(code=r.get("FCMCode"), name=r.get("FCMName"), contract=r.get("Contract"), volume=number(r.get("Volume")))
        elif name == "pcr":
            if "PutCallOIRatio%" not in r:
                raise ValueError("P/C source columns changed: " + ", ".join(str(k) for k in r))
            for field, source in {"putVolume": "PutVolume", "callVolume": "CallVolume", "volumeRatio": "PutCallVolumeRatio%",
                                  "putOi": "PutOI", "callOi": "CallOI", "oiRatio": "PutCallOIRatio%"}.items():
                out[field] = number(r.get(source))
        elif name == "tpex-terms":
            out.update(code=r.get("Code"), name=r.get("Name"), underlying=r.get("UnderlyingStockCode"),
                       underlyingName=r.get("UnderlyingStock"), cp=cp(r.get("Type")), style=r.get("American/European"),
                       expiry=iso_date(r.get("ExpiryDate")), ratio=number(r.get("Latest ExerciseRatio", r.get("ExerciseRatio"))),
                       strike=number(r.get("LatestExercisePrice")), market="TPEX")
        elif name == "tpex-warrants":
            units = number(r.get("TradeVol.", r.get("TradeVol")))
            out.update(code=r.get("Code"), name=r.get("Name"), price=number(r.get("Close")),
                       volume=units / 1000 if units is not None else None, value=number(r.get("TradeValue")), market="TPEX")
        elif name == "tpex-underlyings":
            if not {'Date', 'SecuritiesCompanyCode', 'CompanyName', 'Close'} <= r.keys():
                raise ValueError('Underlying quote columns changed')
            out.update(code=r['SecuritiesCompanyCode'], name=r['CompanyName'], price=number(r['Close']))
        rows.append(out)
    return rows


def validate(name, rows, today, calendar=None):
    if not rows or any(not r.get("date") for r in rows):
        raise ValueError("Missing records or source dates")
    latest = max(r["date"] for r in rows)
    age = (today - date.fromisoformat(latest)).days if name in US_FEEDS else (calendar or default_calendar()).age(latest, today)
    if not 0 <= age <= (35 if name in COT_DATASETS else 14):
        raise ValueError(f"Source date out of range: {latest}")
    latest_rows = [r for r in rows if r["date"] == latest]
    field = "code" if name in ("twse-warrants", "twse-volume", "twse-volume-put", "tpex-warrants", "tpex-terms", "tpex-underlyings") else "contract"
    if name != "pcr" and any(not r.get(field) for r in latest_rows):
        raise ValueError(f"Missing {field}")
    numeric = "oi" if name in COT_DATASETS else "totalVolume" if name == "us-options" else "price" if name == "us-futures" else "netOi" if name.endswith("-institutional") else "oi" if name.endswith("-large") else "oiRatio" if name == "pcr" else "strike" if name == "tpex-terms" else "price" if name in ("futures", "options", "twse-warrants", "tpex-warrants", "tpex-underlyings") else "volume"
    if not any(r.get(numeric) is not None for r in latest_rows):
        raise ValueError(f"Missing numeric field: {numeric}")
    return latest, latest_rows


FEED_KEYS = ("date", "fetchedAt", "rows", "history", "provenance")


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def read_feeds(output):
    """Rejoin persisted content with check state, including legacy feed snapshots."""
    manifest = read(output / 'manifest.json') or {}
    sources = {s['id']: s for s in manifest.get('sources', [])}
    feeds = {}
    for name in FEEDS:
        payload = read(output / f'{name}.json') or {}
        source = sources.get(name)
        if source:
            aligned = source.get('date') == payload.get('date')
            state = source.get('status') if aligned else ('retained' if payload.get('rows') else 'unavailable')
            payload = {**payload, 'status': state, 'error': source.get('error') if aligned else 'Manifest and feed dates differ'}
        elif 'status' not in payload:
            payload = {**payload, 'status': 'unavailable', 'error': 'Source check state is missing'}
        feeds[name] = payload
    return feeds


def save(path, obj):
    """Publish canonical JSON; HTTP compression is provided by the static host."""
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    compressed = path.with_suffix(path.suffix + ".gz")
    removed = compressed.exists()
    if removed:
        compressed.unlink()
        WRITTEN.add(compressed)
    if path.is_file() and path.read_bytes() == data:
        return removed
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(data)
    temp.replace(path)
    WRITTEN.add(path)
    return True


def retained(old, error):
    """Keep the last derived result, flag it, and drop legacy per-run check times."""
    return {**{k: v for k, v in old.items() if k != 'checkedAt'}, 'status': 'retained', 'error': error}


def stamp(payload, previous, now):
    """updatedAt moves only when some published file changed or the payload itself differs from the previous one."""
    previous = previous or {}
    unchanged = {k: v for k, v in previous.items() if k not in ("updatedAt", "checkedAt")}
    return {**payload, "updatedAt": now if WRITTEN or payload != unchanged else previous.get("updatedAt") or previous.get("checkedAt") or now}


def update_feed(name, old, raw, now, today, calendar=None):
    rows = normalize(name, raw)
    latest, current = validate(name, rows, today, calendar)
    if old and old.get("date") and latest < old["date"]:
        raise ValueError("Source regressed to an earlier date")
    # History is a union by date, not by fetch time. Re-runs replace that date.
    history = {}
    if name.endswith("-institutional") or name == "pcr" or name in US_FEEDS:
        limit = 26 if name in COT_DATASETS else 60
        history = {entry["date"]: entry for entry in (old or {}).get("history", [])}
        for day in sorted({r["date"] for r in rows})[-limit:]:
            history[day] = {"date": day, "rows": [r for r in rows if r["date"] == day]}
        history = [history[d] for d in sorted(history)[-limit:]]
    return {"date": latest, "fetchedAt": now, "rows": current, "history": history or []}


def combine_warrants(feeds):
    volumes = {r["code"]: r for name in ("twse-volume", "twse-volume-put") for r in feeds.get(name, {}).get("rows", [])}
    terms = {r["code"]: r for r in feeds.get("tpex-terms", {}).get("rows", [])}
    result = []
    for row in feeds.get("twse-warrants", {}).get("rows", []):
        v = volumes.get(row["code"], {})
        same_day = v.get("date") == row["date"]
        result.append({**row, "volume": v.get("volume") if same_day else None,
                       "value": v.get("value") if same_day else None, "termsDate": row["date"]})
    for row in feeds.get("tpex-warrants", {}).get("rows", []):
        t = terms.get(row["code"], {})
        result.append({**t, **row, "termsDate": t.get("date")})
    return result


def collect(output, fetcher=fetch, now=None, calendar_fetcher=None):
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = datetime.fromisoformat(now).astimezone(timezone(timedelta(hours=8))).date()
    output.mkdir(parents=True, exist_ok=True)
    WRITTEN.clear()
    calendar, calendar_status = calendar_for_run(output, today.year, save, calendar_fetcher)
    old_manifest = read(output / "manifest.json") or {}
    feeds, statuses, errors = {}, [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {name: pool.submit(fetcher, url) for name, (_, url) in FEEDS.items()}
        for name, (label, url) in FEEDS.items():
            path = output / f"{name}.json"
            old = read(path)
            error = None
            try:
                payload = update_feed(name, old, tasks[name].result(), now, today, calendar)
                if name == 'tpex-underlyings':
                    underlyings = {r.get('underlying') for n in ('tpex-terms', 'twse-warrants') for r in feeds.get(n, {}).get('rows', [])}
                    payload['rows'] = [r for r in payload['rows'] if r['code'] in underlyings]
                if name == "options-fcm":
                    names = {r["code"]: r["name"] for r in feeds.get("futures-fcm", {}).get("rows", [])}
                    for r in payload["rows"]:
                        if not r.get("name") or "\ufffd" in r["name"]:
                            r["name"] = names.get(r["code"], r["code"])
                # Identical content keeps its first fetch time and provenance, so a published file only changes with the data.
                if old and all(old.get(k) == payload[k] for k in ("date", "rows", "history")):
                    payload["fetchedAt"] = old.get("fetchedAt") or now
                    payload["provenance"] = old.get("provenance") or provenance(payload, None, url)
                else:
                    payload["provenance"] = provenance(payload, old, url)
                status = "ok"
            except Exception as exc:
                error = str(exc)[:220]
                errors.append(name)
                payload = {k: old[k] for k in FEED_KEYS if k in old} if old else {"date": None, "fetchedAt": None, "rows": [], "history": []}
                status = "retained" if old and old.get("rows") else "unavailable"
            # Feed files hold content only; the check status and error text live in manifest.json.
            stored = {k: payload[k] for k in FEED_KEYS if k in payload}
            if not error or not path.exists():
                save(path, stored)
            feeds[name] = {**stored, "status": status, "error": error}
            statuses.append({"id": name, "label": label, "url": url, "date": stored["date"],
                             "status": status, "fetchedAt": stored.get("fetchedAt"), "count": len(stored["rows"]), "error": error,
                             "region": "US" if name in US_FEEDS else "TW", "frequency": "weekly" if name in COT_DATASETS else "daily",
                             "staleAfterDays": 14 if name in COT_DATASETS else 4, "provenance": stored.get("provenance")})
            if name not in US_FEEDS:
                statuses[-1].update(staleAfterTradingDays=4, freshnessBasis='TW trading days')
            print(json.dumps({"feed": name, "date": stored["date"], "status": status, "count": len(stored["rows"]), "error": error}, ensure_ascii=False))
    warrants = combine_warrants(feeds)
    save(output / "warrants.json", {"rows": warrants})
    publish_catalog(output, warrants, save)
    archive(output, warrants, feeds, now, today, read, save)
    try:
        collect_spreads(output, warrants, feeds, now, today, read, save)
    except Exception as exc:
        old_spreads = read(output / 'warrant-spreads.json') or {'items': []}
        save(output / 'warrant-spreads.json', retained(old_spreads, '價差計算暫時失敗，保留上次結果'))
        errors.append('warrant-spreads')
        print(json.dumps({'feed': 'warrant-spreads', 'status': 'retained', 'error': type(exc).__name__}))
    focus_report = None
    try:
        focus_report = collect_focus(output, warrants, feeds, now, today, read, save, calendar=calendar)
    except Exception as exc:
        old_focus = read(output / 'warrant-focus.json') or {'items': []}
        save(output / 'warrant-focus.json', retained(old_focus, '觀察清單計算失敗，保留上次結果與原日期'))
        errors.append('warrant-focus')
        print(json.dumps({'feed': 'warrant-focus', 'status': 'retained', 'error': type(exc).__name__}))
    try:
        collect_lab(output, warrants, feeds, now, today, read, save, focus=focus_report)
    except Exception as exc:
        old_lab = read(output / 'warrant-lab.json') or {}
        save(output / 'warrant-lab.json', retained(old_lab, '模擬試驗暫停，保留原帳戶與紀錄；請檢查資料或版本。'))
        errors.append('warrant-lab')
        print(json.dumps({'feed': 'warrant-lab', 'status': 'retained', 'error': type(exc).__name__}))
    try:
        collect_reports(output, now, today, read, save)
    except Exception as exc:
        old_reports = read(output / 'warrant-lab-reports.json') or {'items': []}
        save(output / 'warrant-lab-reports.json', retained(old_reports, '觀察報告產生失敗，保留既有報告與原時間。'))
        errors.append('warrant-lab-reports')
        print(json.dumps({'feed': 'warrant-lab-reports', 'status': 'retained', 'error': type(exc).__name__}))
    manifest = {"schemaVersion": 6, "sources": statuses, "warrantCount": len(warrants), "calendarStatus": calendar_status,
                "schedule": "預定台北 08:23、11:23，以及 18:23 至 23:23 每小時檢查；可能延遲或漏跑，內容未變動不重新發布"}
    collect_quality(output, manifest, read, save, today, now, calendar=calendar)
    # An unchanged rerun leaves every file, including the manifest, byte-identical.
    save(output / "manifest.json", stamp(manifest, old_manifest, now))
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/derivatives"))
    args = parser.parse_args()
    raise SystemExit(1 if collect(args.output, calendar_fetcher=fetch_json) else 0)
