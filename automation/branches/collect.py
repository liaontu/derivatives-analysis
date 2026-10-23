"""Emit one bounded watchlist to the parent encryptor; never log source rows."""
import json
from pathlib import Path
import re
import sys
import time
from urllib.robotparser import RobotFileParser

from probe_broker_branches import ORIGIN, USER_AGENT, fetch, parse_page


def collect(symbols):
    if not 1 <= len(symbols) <= 10 or len(set(symbols)) != len(symbols) or any(
        not re.fullmatch(r"\d{4}", s) for s in symbols
    ):
        raise ValueError("Watchlist must contain 1–10 unique four-digit stock codes")
    try:
        robots = RobotFileParser()
        robots.parse(fetch(ORIGIN + "/robots.txt").splitlines())
        interval = max(3, robots.crawl_delay(USER_AGENT) or 0)
        if interval > 60:
            raise ValueError("Crawl delay exceeds job limit")
    except Exception:
        return [{"symbol": s, "error": "robots_unavailable"} for s in symbols]
    results = []
    for symbol in symbols:
        url = f"{ORIGIN}/{symbol}?status=9"
        if not robots.can_fetch(USER_AGENT, url):
            results.append({"symbol": symbol, "error": "source_disallowed"})
            continue
        time.sleep(interval)
        try:
            results.append({"symbol": symbol, "payload": parse_page(fetch(url), symbol)})
        except Exception:
            # Errors deliberately contain no response text or source data.
            results.append({"symbol": symbol, "error": "source_unavailable_or_changed"})
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(collect(config["symbols"]), ensure_ascii=False))
