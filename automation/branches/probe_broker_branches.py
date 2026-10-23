"""Safely parse a bounded sample of public nStock broker rankings.

The standalone CLI is an on-demand probe, not a redistribution licence.
The scheduled encryptor imports this parser without writing plaintext files.
It reads page data without executing JavaScript, authentication or CAPTCHA work.
Results stay in data/private; each side contains at most 15 ranked branches.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://www.nstock.tw"
USER_AGENT = "StockBranchProbe/0.1"
MAX_BYTES = 4_000_000
DECODER = json.JSONDecoder()
IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


class DataError(ValueError):
    pass


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.active = False


def literal(text, aliases):
    """Parse one JSON scalar or a known Nuxt scalar reference. Never eval JS."""
    text = text.lstrip()
    try:
        value, _ = DECODER.raw_decode(text)
    except json.JSONDecodeError:
        match = IDENTIFIER.match(text)
        if not match or match[0] not in aliases:
            raise DataError("Unknown data reference") from None
        value = aliases[match[0]]
    if isinstance(value, (dict, list)):
        raise DataError("Expected scalar data")
    return value


def property_value(text, key, aliases):
    match = re.search(r'(?<![\w$])(?:' + re.escape(json.dumps(key, ensure_ascii=False))
                      + r'|' + re.escape(key) + r')\s*:\s*', text)
    if not match:
        raise DataError(f"Missing field: {key}")
    return literal(text[match.end():], aliases)


def array_at(text, start, aliases):
    """Convert the data array's scalar references into JSON, preserving strings."""
    if text[start] != "[":
        raise DataError("Branch data is not an array")
    tokens, depth, i = [], 0, start
    while i < len(text):
        char = text[i]
        if char == '"':
            _, end = DECODER.raw_decode(text[i:])
            tokens.append(text[i:i + end])
            i += end
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char.isalpha() or char in "_$":
            match = IDENTIFIER.match(text, i)
            if not match:
                raise DataError("Unsupported data syntax")
            value = literal(match[0], aliases)
            tokens.append(json.dumps(value, ensure_ascii=False))
            i = match.end()
            continue
        elif char not in " \t\r\n:,.0123456789-+":
            raise DataError("Unsupported data syntax")
        tokens.append(char)
        i += 1
        if depth == 0:
            try:
                return json.loads("".join(tokens)), i
            except json.JSONDecodeError as exc:
                raise DataError("Invalid branch data") from exc
    raise DataError("Truncated branch array")


def number(value, label):
    if isinstance(value, bool) or value is None:
        raise DataError(f"Missing number: {label}")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise DataError(f"Invalid number: {label}") from exc
    if not result.is_finite():
        raise DataError(f"Non-finite number: {label}")
    return result


def parse_page(html, symbol):
    parser = Scripts()
    parser.feed(html)
    scripts = [s for s in parser.scripts if "window.__NUXT__=" in s]
    if len(scripts) != 1:
        raise DataError("Expected one public Nuxt data script; page may have changed")
    script = scripts[0]
    params = re.search(r"window\.__NUXT__=\(function\(([^)]*)\)\{return", script)
    args = re.search(r"\}\((.*)\)\);?\s*$", script, re.S)
    if not params or not args:
        raise DataError("Unrecognised Nuxt serialization")
    try:
        values = json.loads("[" + args[1] + "]")
    except json.JSONDecodeError as exc:
        raise DataError("Non-literal Nuxt arguments; refusing to execute JavaScript") from exc
    keys = params[1].split(",")
    if len(keys) != len(values) or any(isinstance(v, (dict, list)) for v in values):
        raise DataError("Unexpected Nuxt references")
    aliases = dict(zip(keys, values))
    if str(property_value(script, "股票代號", aliases)) != symbol:
        raise DataError("Response stock code differs from requested stock")
    quote_date = date.fromisoformat(str(property_value(script, "最近交易日期", aliases)))
    marker = re.search(r"\bbranchData\s*:\s*", script)
    if not marker:
        raise DataError("No broker rankings in page")
    pairs, end = array_at(script, marker.end(), aliases)
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 15:
        raise DataError("Unexpected ranking length")
    raw_date = str(property_value(script[end:end + 1000], "updatedDate", aliases))
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", raw_date)
    if not match:
        raise DataError("Unrecognised branch date")
    month, day = map(int, match.groups())
    year = quote_date.year - ((month, day) > (quote_date.month, quote_date.day))
    data_date = date(year, month, day)
    rows, warnings = [], []
    for rank, pair in enumerate(pairs, 1):
        for label, side, net_key in (("買方", "buy", "買超"), ("賣方", "sell", "賣超")):
            raw = pair[label]
            broker_code, broker_name = raw["分點代號"], raw["分點名稱"]
            if not isinstance(broker_code, str) or not re.fullmatch(r"[A-Za-z0-9]{4,8}", broker_code):
                raise DataError("Invalid broker identifier")
            if not isinstance(broker_name, str) or not broker_name.strip():
                raise DataError("Missing broker name")
            if int(number(raw[f"{net_key}排名"], "rank")) != rank:
                raise DataError("Unexpected source ranking order")
            buy, sell, net = [number(raw[k], k) for k in ("買張", "賣張", net_key)]
            if buy < 0 or sell < 0 or (side == "buy" and net < 0) or (side == "sell" and net > 0):
                raise DataError("Invalid quantity or ranking direction")
            difference = buy - sell - net
            if abs(difference) > 1:
                raise DataError(f"Inconsistent quantities for {broker_code}")
            if difference:
                warnings.append(f"{broker_code}: displayed lots differ by {difference}; retained source values")
            rows.append({
                "side": side, "rank": rank, "broker_code": broker_code,
                "broker_name": broker_name,
                "buy_lots": float(buy), "sell_lots": float(sell), "net_lots": float(net),
                "avg_buy_price": str(number(raw["買均價"], "買均價")),
                "avg_sell_price": str(number(raw["賣均價"], "賣均價")),
            })
    if len({r["broker_code"] for r in rows}) != len(rows):
        raise DataError("Duplicate broker in rankings")
    return {
        "schema_version": 1, "source": "nStock public stock page",
        "source_url": f"{ORIGIN}/{symbol}?status=9", "symbol": symbol,
        "stock_name": property_value(script, "股票名稱", aliases),
        "data_date": data_date.isoformat(), "quote_date": quote_date.isoformat(),
        "source_date_label": raw_date, "unit": "張",
        "scope": "top15_net_buy_and_top15_net_sell", "complete_branch_coverage": False,
        "missing_branch_means_zero": False,
        "includes_block_trades": "此數據含鉅額交易" in html,
        "public_redistribution_permission": "not_verified",
        "warnings": warnings, "rows": rows,
    }


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=25) as response:
        if response.status != 200:
            raise DataError(f"HTTP {response.status}")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise DataError("Response too large")
    return data.decode("utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--html", type=Path, help="Parse an already downloaded page; one symbol only")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data/private/broker-branch-probe")
    options = ap.parse_args()
    symbols = list(dict.fromkeys(options.symbols))
    if not 1 <= len(symbols) <= 10 or any(not re.fullmatch(r"\d{4,6}", s) for s in symbols):
        ap.error("Provide 1–10 stock codes, each 4–6 digits")
    if options.html and len(symbols) != 1:
        ap.error("--html accepts one stock only")
    interval = 3
    if not options.html:
        robots = RobotFileParser()
        robots.parse(fetch(urljoin(ORIGIN, "/robots.txt")).splitlines())
        if any(not robots.can_fetch(USER_AGENT, f"{ORIGIN}/{s}?status=9") for s in symbols):
            raise DataError("Source robots rules disallow this request")
        interval = max(interval, robots.crawl_delay(USER_AGENT) or 0)
        if interval > 60:
            raise DataError("Source requests a longer crawl delay; probe stopped")
    for index, symbol in enumerate(symbols):
        if not options.html:
            time.sleep(interval)  # Also separates robots.txt from the first page.
        html = options.html.read_text(encoding="utf-8") if options.html else fetch(f"{ORIGIN}/{symbol}?status=9")
        result = parse_page(html, symbol)
        result["processed_at"] = datetime.now(timezone.utc).isoformat()
        result["input_mode"] = "saved_page" if options.html else "live_http"
        folder = options.out_dir / result["data_date"]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{symbol}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        print(json.dumps({"symbol": symbol, "date": result["data_date"], "rows": len(result["rows"]),
                          "output": str(target), "warnings": result["warnings"]}, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Probe stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
