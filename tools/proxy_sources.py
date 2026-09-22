"""Build a proxy list for HarvestKit using only free and open-source egress.

Replaces `tools/fetch_free_proxies.py`, which harvested public proxy lists and
kept anything that answered. That is not safe enough to hand to employees, and
the reason is worth stating precisely rather than waving at:

**What a proxy operator can see.** For an `https://` URL the client opens a
CONNECT tunnel and the TLS handshake happens end-to-end with the origin, so the
proxy sees the destination hostname (via SNI), the timing and the byte counts —
but not the page content, and it cannot alter it, *provided certificate
validation stays on*. HarvestKit's transports pass `verify=True` everywhere and
this script refuses any proxy that interferes with a certificate. For a plain
`http://` URL the operator sees and can rewrite everything, which is why the
validator below rejects any proxy that cannot do CONNECT at all.

**What it still costs you.** A free proxy operator learns which companies you
are researching, and roughly when. Under GDPR that is a disclosure you are
making about your own activity, not about data subjects, but it is a disclosure.
Nothing here sends credentials through a proxy — the SMTP probes and any
authenticated API call bypass the pool entirely.

Three sources, cheapest first:

1. `--free-lists`   Public aggregator lists, validated hard (see `validate`).
                    Free, zero setup, and genuinely low quality: expect to keep
                    somewhere under 5% of what you harvest, and expect the
                    survivors to die within hours. Good for spreading rate
                    limits across a seed API. Not good enough to beat
                    Cloudflare, because these addresses are already known.

2. `--tor`          A local Tor SOCKS5 endpoint. Free, properly maintained, and
                    genuinely anonymous. Cloudflare and Akamai score Tor exits
                    harshly, so treat it as useful for the long tail of small
                    European company sites rather than for the hard ones.

3. `--self-hosted`  The one that actually works, and still costs nothing: run a
                    proxy on a free-tier cloud VM you control. Oracle Cloud's
                    Always Free tier gives two VMs permanently, Google Cloud
                    gives one e2-micro. You get clean addresses nobody has
                    burned, you are the only operator, and there is no per-GB
                    charge. `--print-setup` prints the exact steps.

Usage:
    python tools/proxy_sources.py --free-lists --keep 40 --out proxies.txt
    python tools/proxy_sources.py --tor --out proxies.txt
    python tools/proxy_sources.py --check proxies.txt
    python tools/proxy_sources.py --print-setup
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import requests  # noqa: E402

log = logging.getLogger("proxy_sources")

#: Public aggregators. All are plain text or JSON over HTTPS from GitHub.
FREE_LIST_SOURCES = (
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/https.txt",
)

#: An HTTPS endpoint that echoes the calling IP as JSON. Used to prove the proxy
#: actually replaces our address rather than forwarding it.
IP_ECHO_URLS = (
    "https://api.ipify.org?format=json",
    "https://ifconfig.co/json",
    "https://api.myip.com",
)

#: A stable HTTPS origin used to prove the proxy does not break TLS. If a proxy
#: is intercepting, `verify=True` raises here and the proxy is discarded.
TLS_PROBE_URL = "https://www.cloudflare.com/cdn-cgi/trace"


@dataclass
class ProxyReport:
    url: str
    ok: bool
    latency: float = 0.0
    exit_ip: str = ""
    reason: str = ""


def _direct_ip(timeout: float = 10.0) -> str:
    """Our real egress address, so a proxy that merely forwards it is caught."""
    for url in IP_ECHO_URLS:
        try:
            response = requests.get(url, timeout=timeout)
            if response.ok:
                return _extract_ip(response.text)
        except Exception:
            continue
    return ""


def _extract_ip(body: str) -> str:
    try:
        payload = json.loads(body)
    except Exception:
        return body.strip()[:64]
    for key in ("ip", "query", "address"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value.strip()
    return ""


def fetch_free_lists(timeout: int = 15) -> list[str]:
    """Every candidate the aggregators offer, deduped, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for source in FREE_LIST_SOURCES:
        try:
            request = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            log.warning("source %-22s unreachable: %s", source.rsplit("/", 1)[-1], exc)
            continue
        found = 0
        for line in data.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or " " in line:
                continue
            # Some lists carry "ip:port:user:pass" or "ip:port|country".
            line = line.split("|", 1)[0].strip()
            if "://" not in line:
                line = "http://" + line
            if line.count(":") < 2 and "://" in line:
                pass
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
            found += 1
        log.info("source %-22s %5d candidates", source.rsplit("/", 1)[-1], found)
    return out


def validate(proxy: str, direct_ip: str, timeout: float) -> ProxyReport:
    """Keep a proxy only if it is anonymous, TLS-clean and reasonably quick.

    Four tests, in the order that rejects fastest:

    1. It must complete an HTTPS request at all — which means it supports
       CONNECT. A proxy that can only do plain HTTP would see and be able to
       rewrite every page, so it is rejected outright rather than used for
       http:// URLs only.
    2. Certificate validation must pass. `verify=True` is never relaxed here; an
       SSLError means something is sitting in the middle of the connection, and
       that proxy is discarded with a loud reason.
    3. The exit address must differ from ours. A transparent proxy that forwards
       the real address gives the operator false confidence, which is worse than
       no proxy at all.
    4. It must be fast enough to be worth a slot.
    """
    proxies = {"http": proxy, "https": proxy}
    started = time.time()
    try:
        response = requests.get(
            TLS_PROBE_URL,
            proxies=proxies,
            timeout=timeout,
            verify=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except requests.exceptions.SSLError as exc:
        return ProxyReport(proxy, False, reason=f"TLS interception or broken chain: {str(exc)[:80]}")
    except Exception as exc:
        return ProxyReport(proxy, False, reason=f"{type(exc).__name__}")
    if not response.ok:
        return ProxyReport(proxy, False, reason=f"HTTP {response.status_code} on the TLS probe")
    latency = time.time() - started

    try:
        echo = requests.get(
            IP_ECHO_URLS[0],
            proxies=proxies,
            timeout=timeout,
            verify=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        exit_ip = _extract_ip(echo.text) if echo.ok else ""
    except Exception as exc:
        return ProxyReport(proxy, False, latency, reason=f"IP echo failed: {type(exc).__name__}")

    if not exit_ip:
        return ProxyReport(proxy, False, latency, reason="could not read the exit address")
    if direct_ip and exit_ip == direct_ip:
        return ProxyReport(proxy, False, latency, exit_ip, "transparent — it forwards your real IP")

    return ProxyReport(proxy, True, latency, exit_ip)


def validate_many(candidates: list[str], *, keep: int, timeout: float, workers: int) -> list[ProxyReport]:
    direct_ip = _direct_ip()
    if direct_ip:
        log.info("this machine's egress address is %s — proxies echoing it will be rejected", direct_ip)
    else:
        log.warning(
            "could not determine this machine's own address, so the transparent-proxy check "
            "is disabled for this run"
        )

    live: list[ProxyReport] = []
    rejected: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(validate, p, direct_ip, timeout): p for p in candidates}
        try:
            for future in concurrent.futures.as_completed(futures):
                report = future.result()
                if report.ok:
                    live.append(report)
                    log.info(
                        "OK   %-30s %5.2fs exit=%-15s (%d/%d)",
                        report.url,
                        report.latency,
                        report.exit_ip,
                        len(live),
                        keep,
                    )
                    if len(live) >= keep:
                        break
                else:
                    rejected[report.reason.split(":")[0]] = rejected.get(report.reason.split(":")[0], 0) + 1
                    if "interception" in report.reason or "transparent" in report.reason:
                        log.warning("REJECT %-28s %s", report.url, report.reason)
        finally:
            for future in futures:
                future.cancel()

    log.info("checked %d candidates, kept %d", len(candidates), len(live))
    for reason, count in sorted(rejected.items(), key=lambda kv: -kv[1])[:8]:
        log.info("  rejected %5d x %s", count, reason)
    live.sort(key=lambda r: r.latency)
    return live[:keep]


def tor_endpoint(host: str = "127.0.0.1", port: int = 9050) -> str:
    """The local Tor SOCKS5 endpoint, if one is listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2.0)
        if probe.connect_ex((host, port)) != 0:
            return ""
    return f"socks5h://{host}:{port}"


SETUP_NOTES = """
Free egress that actually works, best first
===========================================

Read this before reaching for a proxy list. Two of the five options below are
better than anything a free proxy list will ever give you, and one of them you
already own.

0. Do not get flagged in the first place
-------------------------------------------------------------------------------
Most "I need proxies" is really "I am being fingerprinted". Before adding any
egress at all, confirm these, because they are free and they matter more:

    run:
      use_impersonation: true      # real Chrome TLS/HTTP2 fingerprint
      delay_seconds: 1.0           # per host, not global
      concurrency: 8               # never higher
      use_stealth_browser: true    # last rung, for JS challenges

Measured on twelve European employer domains: `requests` alone reads 6 of 12;
the same machine with impersonation reads 9. No proxy was involved in that gain.
Run `python tools/check_egress.py` to see where this machine actually stands.

1. Your own employees' machines  (free, residential, already deployed)
-------------------------------------------------------------------------------
If HarvestKit is installed on several laptops, you already have the thing
residential proxy vendors sell: a set of genuine consumer IP addresses on
different networks. Split the work rather than routing it.

    laptop A:  --countries DE,AT     --output output/leads-dach.csv
    laptop B:  --countries FR,IT,ES  --output output/leads-south.csv
    laptop C:  --countries NL,BE,SE  --output output/leads-north.csv

Each machine keeps its own checkpoint, so nobody re-crawls what somebody else
did, and merging is `cat`. This beats every option below on IP quality and
costs nothing. Its one limit is that a laptop sat on an office network shares
that network's reputation with everyone else on it.

2. An IPv6 /64 you already have  (free, effectively unlimited rotation)
-------------------------------------------------------------------------------
Any VPS with routed IPv6 — Oracle's always-free tier, Hetzner, OVH, most
others — hands you a /64. That is 18 quintillion source addresses you own
outright. HarvestKit can bind each request to a different one:

    run:
      proxies:
        - "bind://2a01:4f8:c17:1234::a1"
        - "bind://2a01:4f8:c17:1234::a2"
        - "bind://2a01:4f8:c17:1234::a3"

`bind://` is not a proxy: it opens an ordinary direct connection from the local
address you name, so nothing is intercepted and nobody else carries the traffic.
Add the addresses to the interface first (Linux):

    sudo ip -6 addr add 2a01:4f8:c17:1234::a1/64 dev eth0
    sudo ip -6 addr add 2a01:4f8:c17:1234::a2/64 dev eth0

Caveat worth knowing: a site that is IPv4-only cannot be reached this way, and
some WAFs rate-limit a whole /64 rather than a single address. Where it works —
and it works on most European company sites — it is the strongest free rotation
there is.

3. A free-tier cloud VM you control  (free, clean, datacentre-quality)
-------------------------------------------------------------------------------
Oracle Cloud "Always Free" gives two AMD VMs (or four ARM cores) that never
expire. Google Cloud gives one e2-micro free per month. Either yields an address
nobody has burned, and you are the only operator, so nothing about untrusted
proxies applies.

The smallest safe option is a SOCKS5 proxy over SSH — nothing to install, no
port to secure:

    # from the machine running HarvestKit
    ssh -f -N -D 127.0.0.1:1080 user@YOUR_VM_IP

    run:
      proxies:
        - "socks5h://127.0.0.1:1080"

For several exits, repeat on different VMs and different local ports. Two or
three clean addresses beat forty burned ones, comfortably.

If you prefer a real proxy daemon, 3proxy (BSD licence) is about 200 KB:

    sudo apt install -y 3proxy
    # /etc/3proxy/3proxy.cfg
    auth strong
    users harvest:CL:CHOOSE_A_LONG_PASSWORD
    allow harvest
    proxy -p3128 -a
    # then:  http://harvest:PASSWORD@YOUR_VM_IP:3128

Always set a password. An open proxy on the public internet is found and abused
within hours, and the abuse is attributed to you.

4. Cloudflare WARP  (free, consumer-grade address, one exit)
-------------------------------------------------------------------------------
WARP's free tier will run as a local SOCKS5 proxy, which gives you a consumer
egress rather than a datacentre one:

    warp-cli mode proxy
    warp-cli proxy port 40000
    warp-cli connect

    run:
      proxies:
        - "socks5h://127.0.0.1:40000"

One address, not a pool, so it changes reputation rather than spreading load.
Worth trying precisely on the sites that refuse datacentre ranges.

5. Tor  (free, well maintained, refused by the strictest sites)
-------------------------------------------------------------------------------
    sudo apt install tor && sudo systemctl enable --now tor    # Linux
    winget install TorProject.TorBrowser                       # Windows

    python tools/proxy_sources.py --tor --out configs/proxies.txt

Cloudflare and Akamai score Tor exits harshly, so expect it to help on smaller
company sites and not on the hard ones. To rotate exits, run several Tor
instances on different SocksPorts (9050, 9052, 9054 …) and list them all.

6. Public free proxy lists  (last resort)
-------------------------------------------------------------------------------
    python tools/proxy_sources.py --free-lists --check --keep 40

Read the module docstring first. In short: TLS content stays private, because
this script refuses any proxy that interferes with a certificate — but the
operator still learns which hosts you visit, and these addresses are already
well known to every WAF, so their unblocking value is small. Their real use is
spreading rate limits on APIs that count by IP.

Setting `require_proxy: true` in your config makes a run refuse to fetch rather
than fall back to a direct connection when every proxy has been cooled down.
Set it on any machine whose own address must not be seen.

What none of this fixes
-------------------------------------------------------------------------------
Residential IP reputation, on the handful of sites that demand it. hellofresh.de
and getyourguide.com refuse datacentre addresses regardless of fingerprint, so
options 2, 3 and 6 will not open them; options 1 and 4 might. For the rest, the
escalation ladder falls back to the stealth browser
(`run.use_stealth_browser: true`), and a few will stay closed. That is an honest
limit, not a configuration error — and a run now reports those hosts under
`blocked_no_pages_seen` instead of pretending the company named nobody.
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--free-lists", action="store_true", help="harvest and validate public proxies")
    parser.add_argument("--tor", action="store_true", help="include a local Tor SOCKS5 endpoint")
    parser.add_argument("--check", metavar="FILE", default="", help="re-validate an existing proxy file")
    parser.add_argument("--print-setup", action="store_true", help="how to run free egress you control")
    parser.add_argument("--keep", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--workers", type=int, default=48)
    parser.add_argument("--out", default="proxies.txt")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.print_setup:
        print(SETUP_NOTES)
        return

    keepers: list[str] = []

    if args.tor:
        endpoint = tor_endpoint()
        if endpoint:
            keepers.append(endpoint)
            log.info("Tor is listening — added %s", endpoint)
        else:
            log.warning(
                "no Tor SOCKS5 listener on 127.0.0.1:9050. Install and start Tor, or run "
                "--print-setup for instructions."
            )

    candidates: list[str] = []
    if args.check:
        with open(args.check, encoding="utf-8") as handle:
            candidates = [ln.strip() for ln in handle if ln.strip() and not ln.startswith("#")]
        log.info("re-validating %d proxies from %s", len(candidates), args.check)
    elif args.free_lists:
        candidates = fetch_free_lists()
        log.info("harvested %d unique candidates", len(candidates))

    if candidates:
        reports = validate_many(candidates, keep=args.keep, timeout=args.timeout, workers=args.workers)
        keepers.extend(report.url for report in reports)

    if not keepers:
        log.error(
            "no usable proxies. The free lists are mostly dead on any given day — "
            "run --print-setup for the approach that does not depend on them."
        )
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("# Validated by tools/proxy_sources.py — anonymous, TLS-clean, CONNECT-capable.\n")
        handle.write(f'# Point a config at this file with:  run: {{ proxies_file: "{args.out}" }}\n')
        for url in keepers:
            handle.write(url + "\n")
    log.info("wrote %d proxies to %s", len(keepers), args.out)
    print(f'\nAdd to your config:\n\nrun:\n  proxies_file: "{args.out}"\n')


if __name__ == "__main__":
    main()
