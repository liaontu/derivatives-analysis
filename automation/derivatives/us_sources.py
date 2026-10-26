"""Credential-free US public reports; no brokerage or quote-session cookies."""
import csv
import io
import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode

COT_DATASETS = {
    "us-cot-financial": "gpe5-46if",
    "us-cot-financial-combined": "yw9f-hn96",
    "us-cot-commodity": "72hh-3qpy",
    "us-cot-commodity-combined": "kh3c-gbw2",
}
US_FEEDS = {
    name: ("CFTC 金融" if "financial" in name else "CFTC 商品") +
          ("期權合併籌碼（週報）" if name.endswith("combined") else "期貨籌碼（週報）")
    for name in COT_DATASETS
}
US_FEEDS = {name: (label, f"https://publicreporting.cftc.gov/resource/{COT_DATASETS[name]}.json") for name, label in US_FEEDS.items()}
US_FEEDS.update({
    "us-options": ("Cboe 選擇權每日統計", "https://www.cboe.com/markets/us/options/market-statistics/daily"),
    "us-futures": ("CFE 期貨每日結算價", "https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily"),
})
# Long, short, spreading, change in long, change in short. Missing is never zero.
FINANCIAL = {
    "dealer": ("dealer_positions_long_all", "dealer_positions_short_all", "dealer_positions_spread_all", "change_in_dealer_long_all", "change_in_dealer_short_all"),
    "assetManager": ("asset_mgr_positions_long", "asset_mgr_positions_short", "asset_mgr_positions_spread", "change_in_asset_mgr_long", "change_in_asset_mgr_short"),
    "leveraged": ("lev_money_positions_long", "lev_money_positions_short", "lev_money_positions_spread", "change_in_lev_money_long", "change_in_lev_money_short"),
    "other": ("other_rept_positions_long", "other_rept_positions_short", "other_rept_positions_spread", "change_in_other_rept_long", "change_in_other_rept_short"),
    "nonreportable": ("nonrept_positions_long_all", "nonrept_positions_short_all", None, "change_in_nonrept_long_all", "change_in_nonrept_short_all"),
}
COMMODITY = {
    "producer": ("prod_merc_positions_long", "prod_merc_positions_short", None, "change_in_prod_merc_long", "change_in_prod_merc_short"),
    "swap": ("swap_positions_long_all", "swap__positions_short_all", "swap__positions_spread_all", "change_in_swap_long_all", "change_in_swap_short_all"),
    "managedMoney": ("m_money_positions_long_all", "m_money_positions_short_all", "m_money_positions_spread", "change_in_m_money_long_all", "change_in_m_money_short_all"),
    "other": FINANCIAL["other"], "nonreportable": FINANCIAL["nonreportable"],
}


def cot_url(name, today=None):
    fields = ["report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "market_and_exchange_names",
              "contract_market_name", "open_interest_all", "change_in_open_interest_all", "contract_units", "futonly_or_combined"]
    groups = FINANCIAL if "financial" in name else COMMODITY
    fields += [field for group in groups.values() for field in group if field]
    return US_FEEDS[name][1] + "?" + urlencode({
        "$select": ",".join(dict.fromkeys(fields)),
        "$where": f"report_date_as_yyyy_mm_dd >= '{(today or date.today()) - timedelta(days=120)}'",
        "$order": "report_date_as_yyyy_mm_dd DESC,cftc_contract_market_code ASC", "$limit": 50000,
    })


class PublicHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.links, self.current = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = []
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def options_document(raw):
    """Read JSON text from the official rendered page. Never execute scripts."""
    parser = PublicHTML()
    parser.feed(raw.decode("utf-8-sig"))
    chunks = []
    for script in parser.scripts:
        match = re.fullmatch(r"self\.__next_f\.push\((\[.*\])\);?", script, re.S)
        if match:
            value = json.loads(match[1])
            if len(value) > 1 and value[0] == 1 and isinstance(value[1], str):
                chunks.append(value[1])
    # Next streams one JSON object across several script elements.
    stream = "".join(chunks)
    marker = stream.find('"optionsData":')
    start = stream.rfind('"data":', 0, marker)
    if marker < 0 or start < 0:
        raise ValueError("Cboe options report schema changed")
    result, _ = json.JSONDecoder().raw_decode(stream[start + len('"data":'):].lstrip())
    if not isinstance(result.get("optionsData"), dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result.get("selectedDate", "")):
        raise ValueError("Missing Cboe report date or table")
    return result


def settlement_document(raw, request_bytes):
    parser = PublicHTML()
    parser.feed(raw.decode("utf-8-sig"))
    prefix = "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv?dt="
    links = sorted({url for url in parser.links if re.fullmatch(re.escape(prefix) + r"\d{4}-\d{2}-\d{2}", url)})
    if not links:
        raise ValueError("Missing official dated CFE CSV link")
    url = links[-1]
    rows = list(csv.DictReader(io.StringIO(request_bytes(url).decode("utf-8-sig"))))
    if not rows or not {"Product", "Symbol", "Expiration Date", "Price"} <= rows[0].keys():
        raise ValueError("CFE settlement CSV columns changed")
    return {"date": url[-10:], "rows": rows}


def normalize_us(name, raw, number, iso_date):
    if name.startswith("us-cot-"):
        if not isinstance(raw, list) or not raw or len(raw) >= 50000:
            raise ValueError("Empty or truncated CFTC response")
        groups = FINANCIAL if "financial" in name else COMMODITY
        scope = "Combined" if name.endswith("combined") else "FutOnly"
        result, seen = [], set()
        for r in raw:
            day = iso_date(str(r.get("report_date_as_yyyy_mm_dd", ""))[:10])
            code = r.get("cftc_contract_market_code")
            if r.get("futonly_or_combined") != scope or (day, code) in seen:
                raise ValueError("CFTC scope mismatch or duplicate contract/date")
            seen.add((day, code))
            categories = []
            for key, fields in groups.items():
                long, short, spread, change_long, change_short = [number(r.get(f)) if f else None for f in fields]
                if long is None or short is None:
                    raise ValueError("Missing CFTC positions")
                categories.append({"category": key, "long": long, "short": short, "spread": spread,
                                   "net": long - short, "change": change_long - change_short if change_long is not None and change_short is not None else None})
            result.append({"date": day, "contract": code, "name": r.get("market_and_exchange_names"),
                           "shortName": r.get("contract_market_name"), "units": r.get("contract_units"),
                           "scope": scope, "oi": number(r.get("open_interest_all")),
                           "oiChange": number(r.get("change_in_open_interest_all")), "categories": categories})
        return result
    if name == "us-options":
        day, result = iso_date(raw.get("selectedDate")), []
        data = raw.get("optionsData", {})
        for contract, values in data.items():
            if contract == "ratios":
                continue
            by_name = {r["name"]: r for r in values}
            if not {"VOLUME", "OPEN INTEREST"} <= by_name.keys():
                raise ValueError("Missing Cboe volume/OI table")
            row = {"date": day, "contract": contract}
            for label, suffix in [("VOLUME", "Volume"), ("OPEN INTEREST", "Oi")]:
                call, put, total = [number(by_name[label].get(f)) for f in ("call", "put", "total")]
                if any(x is None or x < 0 for x in (call, put, total)) or call + put != total:
                    raise ValueError("Invalid Cboe totals")
                row.update({"call" + suffix: call, "put" + suffix: put, "total" + suffix: total,
                            suffix.lower() + "Ratio": put / call if call else None})
            result.append(row)
        if not any(r["contract"] == "SUM OF ALL PRODUCTS" for r in result):
            raise ValueError("Missing Cboe total")
        return result
    if name == "us-futures":
        rows = [{"date": iso_date(raw.get("date")), "contract": r.get("Product"), "symbol": r.get("Symbol"),
                 "expiry": iso_date(r.get("Expiration Date")), "price": number(r.get("Price"))} for r in raw.get("rows", [])]
        if any(not r["symbol"] or not r["expiry"] or r["price"] is None for r in rows):
            raise ValueError("Missing CFE contract, expiration or settlement")
        if len({r["symbol"] for r in rows}) != len(rows):
            raise ValueError("Duplicate CFE settlement symbol")
        return rows
    raise ValueError("Unknown US report")
