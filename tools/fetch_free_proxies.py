"""Fetch a list of free public HTTP/HTTPS proxies, validate each, write the live
ones to a YAML fragment ready to paste into config.yaml under `run.proxies`.

Free proxies are unreliable — many are dead, slow, or hostile. This script
runs a connectivity probe against each before keeping it. For real production
use, prefer paid residential / datacenter proxies (Oxylabs, BrightData, etc.).

Usage:
  python tools/fetch_free_proxies.py
  python tools/fetch_free_proxies.py --probe-url https://www.jobs.ch --keep 30 --timeout 5
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from typing import List, Tuple

# Multiple sources — if one is down, the others might work.
SOURCES = [
    # GitHub aggregators (frequently updated)
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.json",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
]


def fetch_source(url: str, timeout: int = 10) -> List[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logging.warning("source %s: %s", url, exc)
        return []
    proxies: List[str] = []
    if url.endswith(".json"):
        try:
            j = json.loads(data)
            for entry in j if isinstance(j, list) else []:
                ip = entry.get("ip") if isinstance(entry, dict) else None
                port = entry.get("port") if isinstance(entry, dict) else None
                if ip and port:
                    proxies.append(f"http://{ip}:{port}")
        except Exception:
            pass
    else:
        for line in data.splitlines():
            line = line.strip()
            if not line or "#" in line[:2]:
                continue
            if "://" not in line:
                line = "http://" + line
            proxies.append(line)
    return proxies


def probe(proxy: str, target: str, timeout: int) -> Tuple[str, bool, float]:
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
    ]
    t0 = time.time()
    try:
        with opener.open(target, timeout=timeout) as r:
            ok = r.status < 400
            return proxy, ok, time.time() - t0
    except Exception:
        return proxy, False, time.time() - t0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-url", default="https://httpbin.org/ip")
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--keep", type=int, default=30, help="max number of working proxies to keep")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--out", default="proxies.txt")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    all_proxies: List[str] = []
    for src in SOURCES:
        chunk = fetch_source(src)
        logging.info("source %s → %d proxies", src.rsplit("/", 1)[-1], len(chunk))
        all_proxies.extend(chunk)
    # Dedupe
    seen = set()
    deduped = []
    for p in all_proxies:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    logging.info("total unique proxies to probe: %d", len(deduped))
    if not deduped:
        sys.exit("no proxies fetched")

    live: List[Tuple[str, float]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, p, args.probe_url, args.timeout): p for p in deduped}
        for fut in concurrent.futures.as_completed(futures):
            proxy, ok, dur = fut.result()
            if ok and dur < args.timeout:
                live.append((proxy, dur))
                logging.info("OK %s (%.2fs) — %d so far", proxy, dur, len(live))
                if len(live) >= args.keep:
                    for f in futures:
                        f.cancel()
                    break

    if not live:
        sys.exit("no live proxies — try a different probe URL")

    live.sort(key=lambda x: x[1])
    out = "\n".join(p for p, _ in live[: args.keep])
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out + "\n")
    logging.info("wrote %d live proxies to %s", min(len(live), args.keep), args.out)
    print("\nPaste into config.yaml under run.proxies:\n")
    print("run:")
    print("  proxies:")
    for p, _ in live[: args.keep]:
        print(f"    - {p}")


if __name__ == "__main__":
    main()
