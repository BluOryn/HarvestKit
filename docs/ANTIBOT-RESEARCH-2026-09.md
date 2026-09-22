# Anti-blocking and egress research — 2026-09-21

Open-source tooling assessed for HarvestKit. `verdict` is the researching agent's call.

The fact-check pass did not complete (token limit), so confirm licence and maintenance before adopting.


## curl_cffi — adopt

https://github.com/lexiforest/curl_cffi · MIT · Python (bindings to a curl-impersonate fork)

**What.** A requests-compatible HTTP client that reproduces a real browser's TLS ClientHello (JA3/JA4) and HTTP/2 SETTINGS/header-order fingerprint via `impersonate="chrome"`. Header spoofing happens above the handshake; this operates at the handshake.

**Fit.** I measured this on the exact hosts in the investigator's fact #3, from this machine, same IP, same UA string, back to back. requests -> curl_cffi: www.sap.com 403(379B) -> 200(59KB); www.getyourguide.com 403(5666B) -> 200(290KB); www.zalando.de ReadTimeout -> 200(565KB); www.personio.com 429 -> 200(1.75MB). Controls unchanged: siemens.com, n26.com, adyen.com all 200 both ways. Only www.hellofresh.de stayed 403 across chrome/chrome124/chrome131/safari/firefox impersonation, and its response carries `server: cloudflare` + `cf-ray` — a managed challenge needing a real browser. So 4 of the 5 failing EU hosts were never an IP problem. The lead famine is predominantly a TLS/HTTP2 fingerprint problem, and no proxy — free, paid, or self-hosted — would have fixed it. This is the single highest-impact change available and it costs nothing.

**Maintenance.** Very much alive. ~5.5-6.5k stars, MIT, frequent releases; recent work tracks Chrome 150/152 handshake changes. Already installed in this repo's environment at version 0.15.0 — but absent from requirements.txt and unused by src/job_scraper/http.py, so nobody is getting the benefit.

**Safety.** Low supply-chain risk: MIT, widely used, ships as a binary wheel wrapping a curl-impersonate build, so pin the exact version and hash in requirements.txt since it carries compiled code. No data-exposure risk — traffic goes to the same destinations, it only changes how the handshake looks. It is safer than the proxy route because it removes the reason to hand traffic to third parties at all.

**Integration.** src/job_scraper/http.py builds `self._session = requests.Session()` at line ~348 and calls it at three places: line 526 (`get`), 583 (`head`), 641 (`post_json`). Swap to `from curl_cffi import requests as cffi_requests; self._session = cffi_requests.Session(impersonate="chrome")`. The call signatures are compatible for `headers=`, `timeout=`, `allow_redirects=`, `proxies=`, `cookies=`, and responses keep `.status_code`, `.text`, `.content`, `.url`, `.headers`. Three things must change with it: (1) the urllib3 `Retry`/`HTTPAdapter` mount at http.py:352-380 has no curl_cffi equivalent, so that carefully-reasoned 429/Retry-After logic must move into an explicit retry loop around the call — do not drop it, the comments there record real incidents; (2) `response.apparent_encoding` at http.py:~553 is a requests-only attribute, use curl_cffi's `default_encoding="utf-8"` on the Session instead; (3) `_stealth_headers` at http.py:463 hand-writes Sec-CH-UA for "Chrome 120" and DEFAULT_UA_POOL at http.py:105 pins Chrome 120/121 and Firefox 121 — with impersonation active those become a contradiction (Chrome-152 handshake claiming to be Chrome 120), which is itself a detection signal. Let curl_cffi set the browser-consistent header set and reduce `_stealth_headers` to Accept-Language and Referer only, and either retire the UA pool or key it to the impersonation target.

**Caveats.** It does not execute JavaScript, so Cloudflare managed challenges (hellofresh.de, measured) still fail — that residue needs a real browser. Pin the version: impersonation targets are renamed between releases and `impersonate="chrome"` tracks a moving default.


## gluetun — adopt

https://github.com/qdm12/gluetun · MIT · Go (Docker container)

**What.** A VPN client in a thin container (WireGuard or OpenVPN, ~20+ providers), with an HTTP proxy and a Shadowsocks proxy built in, so other machines and containers can egress through the tunnel.

**Fit.** This is the cheapest credible way to build architecture (b) — a company-run central egress. Run one small EU VPS per exit identity, each with gluetun holding a WireGuard tunnel and exposing its built-in HTTP proxy on the private network; HarvestKit's existing `run.proxies` list then points at them. It gives you named, accountable, company-controlled egress IPs instead of anonymous strangers' machines, and MIT/Go means no licence friction with HarvestKit's proprietary LICENSE.

**Maintenance.** Healthy and very active: ~15.6k stars, MIT, regular releases, large user base, single-maintainer risk partly offset by how widely it is deployed.

**Safety.** Good. The container is yours, the VPN credentials are yours, and nobody outside your chosen VPN provider sees the traffic — and that provider sees TLS SNI only, not content. The residual question is your VPN provider's own logging and jurisdiction, which is a contract you can read, unlike a free proxy which is a contract you cannot. Do not expose the built-in HTTP proxy to the public internet: it has basic auth at best. Bind it to a private network or WireGuard interface only.

**Integration.** No code change. Put the gateway URLs in `run.proxies` in configs/leads/eu-it.yaml and configs/leads/swiss-it.yaml (currently unset — investigator fact #4), or in a `proxies_file` which src/job_scraper/config.py:173 `_load_proxies` merges in. src/leadgen/cli.py:32 `_build_http` already forwards them to HttpClient. Two blockers to fix first, or the gateway will not actually be used: (1) src/job_scraper/robots.py:58-62 fetches robots.txt with a bare `urllib.request.urlopen`, which ignores the proxy pool entirely — since robots is consulted before the first request to every host, the employee's real IP touches every target domain in the run regardless of proxy config; (2) `head()` at http.py:583 is the one request path that omits `proxies=self._proxies_dict(...)`, so HEAD probes also leak. Also lower `proxy_max_failures` concerns: with a small gateway pool, http.py:545's `if response.status_code == 403: report_failure(...)` will cool down a perfectly healthy gateway after 3 WAF walls, and `_ProxyPool.acquire` then falls back to a direct connection (it warns, http.py:184) — with company gateways you almost certainly want that fallback to be a hard failure instead.

**Caveats.** Adds a per-hop latency and a VPS bill; commercial VPN exit IPs are themselves widely flagged, so prefer a plain VPS public IP or an ISP-proxy upstream over a consumer VPN endpoint for scraping.


## 3proxy — trial

https://github.com/3proxy/3proxy · BSD-3-Clause · C

**What.** A very small, very old, very stable proxy server (HTTP, HTTPS CONNECT, SOCKS4/5) with per-ACL control of the outgoing source address — the classic way to put N public IPs on one box and hand each request a different one.

**Fit.** If you rent one EU VPS with, say, 8 additional IPv4 addresses (or a /64 of IPv6), 3proxy turns that box into your own rotating gateway: listen on 8 ports, bind each to a different external IP, and list all 8 in HarvestKit's `run.proxies`. The existing `_ProxyPool` then rotates across them with health tracking and per-proxy cookie jars — the plumbing at http.py:120-220 was written for exactly this and has never had anything to rotate.

**Maintenance.** Alive and stable rather than busy: 1.0.0 released August 2026, plus a 0.9.x LTS branch. BSD-3, tiny codebase, long track record.

**Safety.** Low risk if you run it: it is your box, your IPs, your logs. It is C code that parses attacker-adjacent input, so keep it bound to a private interface behind WireGuard and never expose it to the internet with weak auth — 3proxy installs are a well-known target precisely because open ones get abused as anonymous relays. Enable its own auth and IP allow-list.

**Integration.** Zero code change beyond the two leaks noted for gluetun. Config side: `run.proxies: [http://user:pass@10.8.0.1:3128, ... :3129, ...]` in configs/leads/*.yaml, one entry per external IP. Keep `proxy_rotation: round_robin` (config.py:60) so load spreads evenly, and raise `deep_per_host_concurrency` only as far as the number of gateway IPs — the module docstring at http.py:14-17 already states that rule, and the `(host, proxy)` throttle key at http.py:513 makes it true.

**Caveats.** Additional IPv4 at most EU hosts costs roughly EUR 1-2/IP/month and they are datacentre IPs, which some WAFs weight negatively. It gives you rotation, not residential trust.


## GOST (GO Simple Tunnel) v3 — trial

https://github.com/go-gost/gost · MIT · Go

**What.** A modern multi-protocol tunnel/proxy: HTTP, HTTP/2/3, SOCKS4/5, Shadowsocks, QUIC, KCP, gRPC, WebSocket, TUN/TAP, with multi-hop forwarding chains, admission control/auth, built-in load balancing and node selection across upstream nodes, and a web API for dynamic reconfiguration.

**Fit.** The most capable open-source stand-in for what Scrapoxy used to do, and the natural modern alternative to 3proxy. One gost instance on the gateway can present a single endpoint to HarvestKit and load-balance behind it across several upstream exits (your own VPSs, or a paid ISP-proxy provider), which means HarvestKit needs to know about exactly one proxy URL and you change the fleet without touching configs. Single static Go binary, so deploying it on a small VPS is trivial and there is no runtime to patch.

**Maintenance.** Active: ~7.5k stars, MIT, v3.3.0 released August 2026.

**Safety.** Low risk, self-hosted, MIT, no third party in the path. Same rule as 3proxy: enable auth, bind to the private/WireGuard interface, never leave an open relay facing the internet. The dynamic web API is a control plane — firewall it.

**Integration.** HarvestKit sees one URL: `run.proxies: ["http://user:pass@gateway.internal:8080"]`. Because that is a single pool entry, fix the 403 bookkeeping first — http.py:545 calls `report_failure` on every 403, and with `proxy_max_failures: 3` (config.py:61) three EU bot walls in a row will cool your only gateway for 300 seconds and silently drop the run onto direct connections. Either set `proxy_max_failures` high, or better, distinguish 'the proxy failed' from 'the origin said no' in `get()` so a WAF 403 stops penalising the transport. That same distinction is worth making regardless of which gateway you pick.

**Caveats.** Documentation is heavily Chinese-first, which slows onboarding. Feature surface is large; use a small subset.


## mubeng — assess

https://github.com/mubeng/mubeng · Apache-2.0 · Go

**What.** Runs a local HTTP proxy that rotates your outbound IP across a supplied proxy list every N requests (sequential or random), plus a `--check` mode that validates a proxy list and can filter by country. Speaks HTTP/S, SOCKS4/4a/5, and Amazon API Gateway endpoints.

**Fit.** If you buy a block of ISP or datacentre proxies, mubeng in front of them gives you rotation and health-checking in one small binary, and HarvestKit points at a single local endpoint. Its `--only-cc` country filter is directly useful for an EU-targeted run where you want German companies seen from German IPs. It is also a straight, safe replacement for the role tools/fetch_free_proxies.py plays today — checking and rotating — minus the part where the list comes from strangers.

**Maintenance.** Moderate: ~2.7k stars, Apache-2.0, releases through 2025; steady rather than fast-moving. Small enough to audit and fork if it stalls.

**Safety.** Low supply-chain risk (Apache-2.0, small Go codebase). It is a rotator, not a source of trust: it inherits whatever the upstream proxies are, so mubeng in front of a free proxy list is exactly as dangerous as the free list. Bind its listener to localhost.

**Integration.** Run `mubeng -f proxies.txt -a 127.0.0.1:8089 -r 1 --sync`, then `run.proxies: ["http://127.0.0.1:8089"]`. Because HarvestKit then sees one entry, the per-proxy cookie jar at http.py:209 `jar_for()` collapses to a single jar while the egress IP changes underneath — cookies from IP A get replayed from IP B, which is a fingerprint inconsistency. Either run mubeng with `-r` high enough to be sticky per session, or keep rotation in `_ProxyPool` and use mubeng only for its checker.

**Caveats.** Overlaps with what `_ProxyPool` already does in-process. On a device fleet its value is mostly the checker and the country filter.


## WireGuard (self-hosted multi-exit, e.g. wg-quick or wg-easy on EU VPSs) — trial

https://www.wireguard.com/ · GPL-2.0 (kernel module); userspace tools GPL-2.0, wg-easy AGPL-3.0 · C / kernel

**What.** A minimal, audited, modern VPN. Each employee laptop holds one tunnel to company infrastructure; the gateway side decides which public IP the traffic leaves from.

**Fit.** This is the transport for architecture (b) and it is the part of the design that actually solves 'we cannot use our own IP' honestly. Employee laptops never talk to target sites directly; they talk to your gateway, and the gateway (running 3proxy or gost) presents company IPs. That gets the employee's home address out of the picture entirely — which matters as much for the employee's privacy as for blocking — and gives you one place to log, rate-limit, rotate and shut off. Roughly EUR 4-6/month per exit at Hetzner-class pricing with 20TB included, so a 5-exit fleet is EUR 25-30/month.

**Maintenance.** In-kernel since Linux 5.6, ubiquitous, audited. Not going anywhere.

**Safety.** The strongest posture of any option here. Keys are yours, no third party sees the traffic, and you can prove where a request came from when a target complains. Two duties come with it: key rotation/revocation when a laptop is lost or an employee leaves, and — because you now see all tunnelled traffic — a written, narrow scope so the tunnel carries scraper traffic only and is not, and cannot be mistaken for, employee monitoring. Split-tunnel it to HarvestKit's traffic rather than the whole device.

**Integration.** Nothing in http.py changes if you route at the OS level, but that is the wrong choice here: a full-device tunnel sweeps up the employee's personal browsing. Prefer split tunnelling plus an explicit proxy so the routing is visible in config rather than implicit in the OS — set `run.proxies` to the gateway address reachable over the tunnel. Whichever you choose, fix src/job_scraper/robots.py:58 (`urllib.request.urlopen`, proxy-blind) and http.py:583 (`head()`, no `proxies=`) first; under OS-level routing they are covered by accident, under proxy config they are live IP leaks.

**Caveats.** You now operate infrastructure: monitoring, patching, abuse handling, and an on-call story when a gateway IP gets blocked. That is the real cost, not the VPS bill.


## HAProxy — assess

https://www.haproxy.org/ · GPL-2.0-or-later (libs LGPL) · C

**What.** Industrial-grade TCP/HTTP load balancer with health checks, observability, retries, and per-backend `source` address binding.

**Fit.** Not a scraping tool, but the right front door if you build the central gateway: put HAProxy in front of N upstream proxies or N `source`-bound backends so a dead exit is detected and removed automatically, with real metrics. HarvestKit's `_ProxyPool` health tracking is decent but blind — it cannot tell a dead proxy from a hostile origin. HAProxy can, and it exports that to a dashboard you can look at during a run.

**Maintenance.** Mature, corporate-backed, releases on a predictable cadence. Zero abandonment risk.

**Safety.** Very low. Self-hosted, no third party, well-audited, decades of production use. Standard hardening applies (bind internally, auth on the stats socket).

**Integration.** Point `run.proxies` at the HAProxy frontend. Note the practical gotcha documented by practitioners: connection reuse defeats per-request rotation, so `option httpclose`/`http-reuse never` on the relevant backend, otherwise every request rides the same upstream connection and the same IP. Pair with per-backend `source` lines to bind each backend to a different additional IP on the box.

**Caveats.** Rotating the *source* address is an awkward fit for HAProxy's model — it is designed to balance across backends, not to cycle egress identities. For pure egress rotation 3proxy or gost is simpler; use HAProxy when you want the health-checking and metrics layer.


## Squid — assess

https://www.squid-cache.org/ · GPL-2.0-or-later · C++

**What.** The classic forward proxy/cache. `tcp_outgoing_address` selects the outbound source IP per ACL, letting one box present many IPs.

**Fit.** It is the best-documented recipe for multi-IP egress (the Squid wiki has a dedicated 'Rotating the Squid outbound IPs' page), and if your ops people already know Squid the learning cost is zero. The standard pattern — several named `http_port`s, each mapped by ACL to a different `tcp_outgoing_address` — produces exactly the list of endpoint URLs that HarvestKit's `run.proxies` wants.

**Maintenance.** Long-lived and still released, but heavier than the alternatives and carrying a long CVE history typical of a large C++ codebase with a big parsing surface.

**Safety.** Self-hosted so no third-party exposure, but patch discipline matters more than with 3proxy or gost: Squid has had repeated remote vulnerabilities. Never expose it publicly; internal interface only.

**Integration.** Same as 3proxy — one `run.proxies` entry per named port. Caching should be disabled or scoped carefully, because HarvestKit already has its own SQLite response cache (`_ResponseCache`, http.py:261) with block-detection on read (http.py:290) and a second opaque cache layer would let a WAF page persist where that logic cannot see it.

**Caveats.** Important: `tcp_outgoing_address` is reported as removed in Squid 8, so this approach pins you to Squid 7 or earlier. Verify against the version your distro ships before committing.


## Commercial ISP / datacentre proxies (Webshare, IPRoyal, Decodo, Oxylabs, Evomi) — trial

https://www.webshare.io/pricing · Proprietary commercial service (the client side stays open source — you consume them through gost, mubeng, or HarvestKit's own _ProxyPool) · n/a (service)

**What.** Rents you static IPv4 addresses that are registered to real ISPs (ISP/static-residential) or to datacentres, as authenticated HTTP/SOCKS endpoints.

**Fit.** This is the honest 'open source cannot compete' line, and it is narrower than the market wants you to believe. Open source can rotate, health-check, route and fingerprint perfectly well. What it cannot do is manufacture IP addresses with good reputation — reputation comes from the registry, and you buy that. Static ISP proxies are the right shape for B2B lead scraping: a stable, geographically-correct, accountable identity per worker rather than a churn of anonymous residentials. Pricing is per-IP and modest — roughly USD 0.30-2.40 per proxy per month at Webshare/IPRoyal-class vendors, so a 10-IP EU block is single-digit to low-tens of dollars a month, far below per-GB residential.

**Maintenance.** Commercial, contractual. Pick one that will sign a DPA and name its EU entity.

**Safety.** Materially better than free proxies because there is a contract, an identifiable counterparty, a DPA, and liability. Still a processor in your path: they can see destination hosts and TLS SNI, so the DPA and retention terms are the control, not the technology. Prefer ISP/datacentre over rotating *residential*: residential pools are sourced from consumer devices via bandwidth-sharing SDKs, the consent chain is unverifiable from your side, and buying that supply chain while processing EU personal data is a GDPR argument you do not want to have. Bright Data and Oxylabs hold SOC 2 Type II (Bright Data also ISO 27001) and offer DPAs — ask for both in writing.

**Integration.** Drop the endpoint URLs straight into `run.proxies` (or a `proxies_file`, merged by config.py:173) and they flow through src/leadgen/cli.py:32 into `_ProxyPool`. Credentials embedded in proxy URLs will then sit in a YAML file on employee laptops — move them to environment variables and read them in `_load_proxies` rather than committing them, and note that `logging.info` at http.py:207 prints `entry["url"]` on cooldown, which will write user:pass into your run logs.

**Caveats.** Per-IP costs scale with fleet size; if every employee laptop needs its own identity you are paying per seat. That is an argument for the central gateway, where a shared pool is amortised.


## Camoufox — trial

https://github.com/daijro/camoufox · MPL-2.0 · Python wrapper over a patched Firefox

**What.** A Firefox fork patched at the C++ level to spoof fingerprinting surfaces (canvas, WebGL, screen geometry, navigator) with internally consistent values, plus geolocation/timezone/locale spoofing aligned to the proxy's country. Playwright-compatible interface.

**Fit.** It addresses the residue curl_cffi cannot: the measured hellofresh.de case, which returns Cloudflare 403 with a cf-ray under every impersonation profile and needs real JS execution. Its locale/timezone alignment is specifically valuable for EU targets — a German site reached through a German IP but with an en-US timezone is a visible mismatch. Use it as a narrow fallback tier for the ~1-in-6 hosts that hard-block, not as the default client.

**Maintenance.** Mixed, and worth knowing before you build on it: ~8-12k stars and currently under active development, but the README itself records roughly a year-long maintenance gap that let it fall behind on base Firefox version and fingerprint consistency. Some future patches may be closed source, though the maintainer commits to the open code remaining buildable.

**Safety.** MPL-2.0 is compatible with keeping HarvestKit proprietary (file-level copyleft; you must publish changes to Camoufox's own files, not your code). It downloads a large prebuilt browser binary — pin the version and verify the checksum, and note that a patched browser fetched from a project download endpoint is a heavier supply-chain commitment than a Python wheel. Running full browsers on employee laptops also burns visible CPU and RAM, which employees will notice.

**Integration.** Do not put this in `HttpClient`. Add it as a separate last-resort fetcher: in the lead pipeline's per-company crawl, when `get()` returns None and the response looked like a Cloudflare wall, queue that host for a browser pass with a low concurrency cap. The repo already depends on `playwright>=1.45.0` in requirements.txt, so a Playwright-shaped API costs little to add. Budget it: a browser fetch is ~50-100x the cost of an HTTP fetch, so cap it per run and cache the result in `_ResponseCache` (http.py:261).

**Caveats.** Heavy. Do not route the whole crawl through it — it would turn a thousands-of-domains run into an overnight job.


## Patchright (Python) — trial

https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python · Apache-2.0 · Python

**What.** A drop-in patched Playwright that removes the CDP-level automation tells (Runtime.enable, Console.enable, --enable-automation flag leaks, closed shadow-root handling) that make vanilla Playwright trivially detectable.

**Fit.** The lower-effort alternative to Camoufox for the JS-challenge tier, and the one that fits this repo best because requirements.txt already pins playwright>=1.45.0 — the change is an import swap, `from playwright...` to `from patchright...`, with the same API. Apache-2.0 sits cleanly alongside a proprietary product. Independent 2026 benchmarking puts it among the most credible maintained stealth options.

**Maintenance.** Active, ~1.5-3.2k stars, Apache-2.0, described in third-party benchmarking as the most actively maintained patched-Playwright fork.

**Safety.** Low. Apache-2.0, permissive, no copyleft exposure. It patches an existing dependency rather than adding a new browser binary, so the supply-chain delta over what you already ship is small. Chromium-only — no Firefox or WebKit.

**Integration.** Same fallback-tier placement as Camoufox: a separate browser fetcher invoked only for hosts that returned a WAF wall, never in the hot path. Reuse the existing Playwright dependency and keep one browser context per target country so locale and timezone stay consistent with whichever egress IP you used.

**Caveats.** Chromium only. Patched forks chase upstream detection changes, so pin the version and expect to re-test after Cloudflare ships a new challenge.


## nodriver — assess

https://github.com/ultrafunkamsterdam/nodriver · AGPL-3.0 · Python

**What.** The official successor to undetected-chromedriver: fully async, drives Chrome directly over CDP with no webdriver binary, which removes an entire class of automation tells.

**Fit.** Technically the strongest of the stealth-browser options — a 2026 comparative benchmark had it clearing every Cloudflare target tested, ahead of Patchright and Camoufox. If the JS-challenge tier turns out to be a large fraction of your EU target list rather than a small one, this is the tool that would hold up.

**Maintenance.** Active: ~4.7k stars, releases through May 2026, same maintainer as undetected-chromedriver.

**Safety.** The technology is fine; the licence is the problem. AGPL-3.0 is strong copyleft with a network clause, and HarvestKit's LICENSE is explicitly proprietary, confidential, and licensed to individuals who install it on machines they control — which is arguably conveying. Combining AGPL code into that product is a licence question for a lawyer, not for a scraper config. Treat it as blocked until counsel clears it, or isolate it behind a process boundary with its own licensing story.

**Integration.** If cleared: run it as a standalone microservice on the gateway box, not as a library import in src/. HarvestKit calls it over localhost HTTP for the handful of blocked hosts. That keeps the AGPL boundary clean-ish (still get advice) and keeps browsers off employee laptops, which is where you want them anyway.

**Caveats.** Licence conflict with a proprietary product is the deciding factor, not capability.


## requests-ip-rotator (AWS API Gateway rotation) — avoid

https://github.com/Ge0rg3/requests-ip-rotator · GPL-3.0 · Python

**What.** Spins up AWS API Gateway endpoints in many regions and proxies requests through them, so each request egresses from a different address in AWS's enormous pool. Mounts onto a requests Session for a single target site.

**Fit.** Honestly: it does not fit here, and I want to be clear why, since it was on the list to evaluate. Three disqualifiers. (1) Licence: GPL-3.0 against a proprietary product distributed to authorised individuals — a real legal question, not a formality. (2) Detection: the README itself warns that these requests carry unique AWS headers such as X-Amzn-Trace-Id and are easily identified and blocked, and the egress is well-known AWS cloud ranges, which is the opposite of what an EU B2B site's WAF wants to see. (3) Shape: each ApiGateway instance targets one site, but the lead pipeline crawls thousands of distinct company domains — you would be creating and tearing down gateways per domain, at ~USD 3 per million requests plus USD 0.09/GB, with a documented footgun where un-shutdown gateways keep billing.

**Maintenance.** Alive: ~1.7k stars, releases into 2026, open PRs as recently as September 2026.

**Safety.** No third-party data exposure (it is your AWS account), so it is far safer than free proxies. The exposure is financial and legal: forgotten gateways bill silently, and GPL-3.0 conflicts with this repo's LICENSE. Also consider whether large-scale IP rotation through AWS sits well with your AWS acceptable-use terms.

**Integration.** Would require bypassing `_ProxyPool` entirely, since it mounts an adapter per target site rather than supplying a proxy URL — a structural mismatch with http.py's single shared `_session`. I recommend not doing it.

**Caveats.** Useful for hammering one API from many IPs; wrong tool for crawling a long tail of domains.


## Scrapoxy — avoid

https://github.com/scrapoxy/scrapoxy · Commercial licence that prohibits forking, redistribution and derivative works — it was never released as open source, and the EOL FAQ confirms it will not be · TypeScript/Node

**What.** Was the reference proxy-orchestration layer: one endpoint in front of cloud instances, proxy vendors and 4G farms, with auto-scaling, sticky sessions and ban-aware routing.

**Fit.** It no longer does, and this is the most important negative finding in the research. Scrapoxy was discontinued on 6 February 2026 after eleven years — the maintainer's own announcement cites one maintainer and one credit card paying for everything. Docker images have been pulled from public registries, public documentation is offline, and the shared backend (GeoIP, proxy status checks) that self-hosted instances depended on has been shut down for non-paying users. The FAQ is explicit that there is no community takeover, no new maintainer, no replacement, and that the licence still prohibits forking even post-shutdown. Any guide, blog post or LLM answer recommending Scrapoxy is now stale.

**Maintenance.** Dead. Announced EOL 6 February 2026; the GitHub repo description literally reads 'Scrapoxy has been discontinued.' No releases published.

**Safety.** Building on it now would mean deploying an unmaintained Node service, with no security updates, that depends on a backend someone else has turned off — while it sits in the path of EU personal data. That is an unacceptable posture regardless of the licence question.

**Integration.** None. If you want its architecture, gost (MIT) plus HAProxy gives you the endpoint-consolidation and health-checking parts self-hosted, and a commercial provider's own manager gives you the vendor-integration part.

**Caveats.** Anything pinning an old Docker tag still works until it does not; treat existing installs as end-of-life and migrate.


## Bright Data Proxy Manager (luminati-proxy) — assess

https://github.com/luminati-io/luminati-proxy · Open source (permissive), but functionally tied to a Bright Data account · Node.js

**What.** A self-hosted local proxy manager: rotation rules, connection pooling, load balancing across super-proxies, per-port configuration, stats, SOCKS5, all driven from a local web UI.

**Fit.** The closest live equivalent to what Scrapoxy provided, maintained by a company with an obvious incentive to keep maintaining it (active npm releases as of 2026). If you end up buying proxies from Bright Data anyway, this gives you the orchestration layer for free and HarvestKit points at one local port.

**Maintenance.** Actively released (version series 1.64x, published within weeks of now).

**Safety.** The code is yours to run locally, so it is not a data-exposure vector in itself — but it is vendor-coupled: it requires a Bright Data account and its value evaporates without one. Bright Data holds SOC 2 Type II and ISO 27001 and will sign a DPA, which is the posture you need for EU personal data. Be deliberate about which product you buy from them: their residential pool carries the consent-chain concerns described under commercial providers; their datacentre/ISP products do not, to the same degree.

**Integration.** Run it on the gateway box, not on employee laptops (it has a web UI and an account credential — neither belongs on a fleet machine). `run.proxies: ["http://gateway.internal:24000"]`, one port per rotation policy if you want several identities.

**Caveats.** Lock-in by design. Evaluate it only if Bright Data is already your provider; do not let it drive the provider choice.


## Free public proxy lists (jhao104/proxy_pool, TheSpeedX/PROXY-List, monosans/proxy-list — and this repo's tools/fetch_free_proxies.py) — avoid

https://github.com/jhao104/proxy_pool · MIT (the pool software). The proxies themselves have no licence, no operator, no contract and no accountability · Python

**What.** proxy_pool harvests free proxies from public sources on a schedule, validates them and serves them over an HTTP API. tools/fetch_free_proxies.py in this repo does a cut-down version of the same thing against five GitHub raw lists, probes each against httpbin.org, and prints them ready to paste into `run.proxies`.

**Fit.** It does not, and this is the recommendation I would push hardest. Concretely, what the operator of a free plaintext HTTP proxy in your `run.proxies` can do: for any http:// request, read and rewrite the full URL, headers, cookies and body, and inject content into the response your parser then treats as truth — which for a lead pipeline means they can fabricate the names, emails and company data you sell. For https:// requests they see the CONNECT target and the TLS SNI, giving them a complete, timestamped log of every company you are prospecting — your target list, which is arguably your most commercially sensitive asset — and they can attempt TLS interception, which the peer-reviewed 2024 study 'Free Proxies Unmasked' found proxies doing in the wild along with ad injection and certificate manipulation; that same body of work reports the large majority of listed free proxies do not even support HTTPS properly. Who these operators are is unknowable: a significant share of free proxies sit on networks with critical vulnerabilities or were placed there deliberately. Under GDPR this is fatal, not merely risky: routing B2B personal data through an anonymous third party means you, as controller, have engaged a processor with no Article 28 contract, no Article 32 security assessment, no idea of the transfer destination for Chapter V purposes, and no ability to report or even detect a breach. The CNIL's EUR 240,000 fine against Kaspr in December 2024 — for scraping LinkedIn professional contact details, with findings on Articles 5(1)(e), 12, 14 and 15 — shows this regulator is actively enforcing against exactly your business model, and it did not even need a proxy angle to do so. Deployed on employee laptops it is worse again: you would be instructing employees to install software that routes traffic through unknown machines, from their home networks, with their company credentials in the same process — a security incident waiting to be named after someone.

**Maintenance.** proxy_pool is alive (~23k stars, MIT, releases continuing), which is precisely the trap: the software is well-maintained, the *supply* is not, and the supply is the dangerous part.

**Safety.** Disqualifying. Not 'use with care' — remove it. tools/fetch_free_proxies.py also probes candidates against httpbin.org, a third-party endpoint, and its own docstring already concedes free proxies are 'unreliable... slow, or hostile' and points at paid providers; the tool contradicts its own advice by existing as the path of least resistance.

**Integration.** The change I would make is subtractive, plus one guard: delete tools/fetch_free_proxies.py, and add a startup validation in `_load_proxies` (src/job_scraper/config.py:173) that rejects any proxy URL that is not on an allow-list of company gateways or contracted providers — so a future operator cannot paste a harvested list into `run.proxies` and quietly reintroduce this. Also delete the `proxies: []` stub in configs/sites/jobsch.yaml if it is only there as an invitation.

**Caveats.** There is no volume of free proxies that makes this safe, and no probe that detects a passive logger. The failure mode is silent by construction.


## Tor (torsocks / control-port circuit rotation) — avoid

https://www.torproject.org/ · BSD-3-Clause · C

**What.** Onion-routed anonymity network; with the control port you can request a new circuit and thus a new exit IP on demand.

**Fit.** It genuinely does not, for four independent reasons, and it is worth writing them down so nobody re-proposes it in three months. (1) The exit list is published in real time by the Tor Project itself, so Cloudflare, DataDome and PerimeterX can and do score every exit IP as high-threat pre-emptively — you would be trading a clean unknown IP for a famously dirty known one, on exactly the WAF-protected hosts you are failing on now. (2) The exit pool is small (low thousands) and shared with all Tor users, so its reputation is other people's behaviour, and you cannot improve it. (3) Throughput and latency are poor and the network is a donated commons — pushing a commercial lead-scraping crawl of thousands of domains through it consumes volunteer capacity intended for people who need anonymity to be safe, which is a straightforward ethical objection independent of whether it works. (4) The exit operator is an anonymous volunteer in an unknown jurisdiction, which reproduces the GDPR processor problem of free proxies almost exactly.

**Maintenance.** Excellent — it is simply the wrong tool.

**Safety.** Exit nodes see the same plaintext a free proxy would for any non-TLS traffic, and malicious exits are a documented, researched phenomenon. Same Article 28/32/Chapter V problem as free proxies, with an added reputational dimension if a target's abuse team traces a scrape to Tor.

**Integration.** Technically it would be `run.proxies: ["socks5://127.0.0.1:9050"]` and that is all — which is exactly why it keeps getting suggested. Do not.

**Caveats.** Using Tor does not make lawful scraping unlawful, and it does not make unlawful scraping lawful. It only changes who is in your path, for the worse.


## FlareSolverr — avoid

https://github.com/FlareSolverr/FlareSolverr · MIT · Python

**What.** A proxy server that launches a stealth browser to clear Cloudflare's interstitial and hands back the resulting cookies and HTML.

**Fit.** It is the name everyone reaches for when they see a Cloudflare 403 like hellofresh.de, so it deserves an explicit verdict rather than silence. It was built for the legacy IUAM 'Just a moment...' page; Cloudflare has moved to Turnstile and managed challenges, which it does not reliably clear, and its maintenance has been slow to chase those changes. Its own issue tracker in 2026 carries reports of it returning 200 while Cloudflare blocks anyway.

**Maintenance.** Nominally maintained (issues active through 2026) but effectiveness against current Cloudflare is materially degraded; the v1 repo is explicitly unmaintained.

**Safety.** MIT and self-hosted, so no third-party exposure. The risk is operational self-deception: a tool that reports success while the content is a challenge page will quietly poison the lead pipeline with garbage — the same class of failure as the RUNBOOK's '403 is fine' health check.

**Integration.** If you want a browser tier, use Patchright or Camoufox directly rather than through FlareSolverr's API — you already have Playwright as a dependency, and you get to see the real page state rather than a proxy's optimistic verdict.

**Caveats.** If you do trial it, assert on page content (an expected selector), never on HTTP 200.


## curl_cffi — adopt

https://github.com/lexiforest/curl_cffi · MIT (verified from installed dist metadata: License-Expression = MIT). Bundles a fork of libcurl-impersonate + BoringSSL + nghttp2 as a single 3.2 MB `_wrapper.pyd`; those carry curl/MIT, Apache-2.0 and MIT terms respectively — all permissive, all safe to ship inside a proprietary product with attribution. No copyleft anywhere in the chain. · Python binding (cffi) over a C library (forked libcurl + BoringSSL). Pure-Python API layer.

**What.** Drives a patched libcurl whose TLS ClientHello, extension order, GREASE, ALPN, and HTTP/2 SETTINGS/WINDOW_UPDATE/PRIORITY frames are byte-identical to a named browser build. Exposes it behind an API that deliberately mimics `requests` (module-level `get/post`, a `Session` class, `Response` with `.status_code/.text/.url/.headers/.cookies`). Supports HTTP/1.1, HTTP/2 and HTTP/3, WebSockets, and raw `ja3=`/`akamai=` strings for fingerprints not in the preset list.

**Fit.** I measured it against the exact failure mode. On the 6 domains the lead investigator probed, plain `requests` + Chrome UA got 2/6 × 200; curl_cffi with `impersonate="chrome136"` got 5/6 × 200 — sap.com 403→200, getyourguide.com 403→200, zalando.de ReadTimeout→200 (565 KB of real page). Widened to a 35-domain EU sample (DAX/CAC/AEX corporates plus EU scale-ups: adyen, klarna, n26, personio, celonis, mollie, asml, doctolib, backmarket, payfit, sumup, forto...), the 200-rate went 22/35 (63%) → 27/35 (77%), and hard 403s went 8 → 5. On a JA4 echo (tls.peet.ws) plain requests fingerprints as `t13d2812h2_257f3020b3a2_cbb9361b6bf9` — the unmistakable Python/OpenSSL 28-cipher hello — while curl_cffi chrome136 produces `t13d1516h2_8daaf6152771_d8a2da3f94cd`, a genuine Chrome JA4, with Chrome's exact HTTP/2 pseudo-header and header ordering. That is the whole gap between a 403 and a 200 on Akamai edge-deny and Cloudflare bot-fight. It is also the only candidate whose kwargs (`headers=`, `timeout=`, `allow_redirects=`, `cookies=`, `proxies=`) line up 1:1 with what `HttpClient.get()` already passes, so the diff is small.

**Maintenance.** Extremely alive. 0.16.3 released 2026-09-02, 0.16.4b1 on 2026-09-20 — i.e. 19 days ago. Stable releases every 2-4 weeks through 2026 (0.16.1 Aug 21, 0.16.2 Aug 25, 0.16.3 Sep 2). ~6.5k stars, 582 commits on main. Fingerprint list is current: chrome150 is in the free preset set, and 0.16.3 shipped Chrome 152's TLS trust_anchors extension. Since 0.15.1 fingerprints ship separately (`curl-cffi update` / the `curl-cffi-fingerprints` package) so you get new Chrome/Safari/Firefox profiles without a version bump. Note the commercial split: Chrome, Safari and Firefox updates are free; exotic profiles live behind impersonate.pro. Everything HarvestKit needs is on the free side.

**Safety.** Honest read: this is a 3.2 MB prebuilt native binary from a single-maintainer project, and you are proposing to put it on employee laptops. That is real supply-chain exposure and should not be waved away.

What mitigates it: MIT, no telemetry, no network callbacks of its own, no post-install script, ~3.9 MB total install, wheels built in public GitHub Actions CI, and a large enough user base (yfinance, scrapling and others depend on it) that a backdoored release would be noticed fast.

What does NOT mitigate it: it is a forked libcurl+BoringSSL, so it does NOT inherit upstream curl's security patch cadence automatically. Upstream curl 8.21.0 patched 18 CVEs in 2026; the fork lags.

One concrete, directly relevant CVE: **CVE-2026-33752, CVSS 8.6, redirect-based SSRF, affects every curl_cffi < 0.15.0.** The library followed redirects into internal/private IP ranges, and its own TLS impersonation made those requests look like browser traffic to egress controls. HarvestKit's threat model is precisely this — it crawls thousands of third-party company domains with `allow_redirects=True`, so a hostile or compromised careers page can 302 an employee laptop at 169.254.169.254 or an internal 10.x host. This machine currently has 0.15.0, which is the minimum fixed version but is 4 minor releases stale.

Required posture: pin `curl_cffi>=0.16.3` (get the fingerprint updates *and* the accumulated fixes), hash-pin the wheel in the lockfile, and pass `allow_redirects=CurlFollow.SAFE` (or the string `"safe"`) which rejects redirects to private IPs — I confirmed `CurlFollow.SAFE` exists in the installed build. Also set `trust_env=False`: curl_cffi honours `http_proxy`/`https_proxy` from the environment by default, which on a corporate laptop silently reroutes every scrape through the company proxy and destroys your control over egress IP.

**Integration.** Smallest real diff of any candidate — `src/job_scraper/http.py` keeps its shape.

1. `HttpClient.__init__` (line ~356): replace `self._session = requests.Session()` + the `Retry(...)` + `HTTPAdapter(...)` + two `mount()` calls with one constructor:
   `self._session = curl_cffi.requests.Session(impersonate=self.impersonate, retry=max_retries, trust_env=False, allow_redirects=CurlFollow.SAFE, max_redirects=10, default_encoding="utf-8")`.
   The whole `Retry` block (and its long comment about urllib3 sleeping unboundedly on Retry-After) can go — curl_cffi's `retry=` has no such behaviour, and your own 30 s-capped 429 handler at lines 541 and 653 already does the better job. `pool_connections/pool_maxsize` have no analogue and are not needed; concurrency is already governed by `HostThrottle`.

2. `HttpClient.get()` (line ~529): the call site is unchanged. `self._session.get(url, headers=merged, timeout=..., allow_redirects=True, proxies=self._proxies_dict(proxy_entry), cookies=jar)` is accepted verbatim — I confirmed `proxies`, `proxy`, `proxy_auth`, `timeout`, `allow_redirects`, `headers`, `cookies`, `impersonate`, `verify`, `http_version` are all valid *per-request* params in `SessionRequestParams`, and that a `requests.cookies.RequestsCookieJar` is accepted as `cookies=`. Your `_ProxyPool.acquire()`-per-request design therefore survives intact — this is the single most important compatibility fact, and it is where primp fails.

3. **Three things that will break and must be fixed in the same commit:**
   a. Line ~547, `response.apparent_encoding` — does not exist on curl_cffi's Response (I checked `vars(r)`; you get `.encoding`, `.charset`, `.charset_encoding` instead). Drop the whole ISO-8859-1 fallback block and pass `default_encoding="utf-8"` on the Session; curl_cffi already resolved example.com to utf-8 correctly in my probe.
   b. Lines 536, 590, 648, `except requests.RequestException` — will NOT catch curl_cffi errors. I verified `issubclass(curl_cffi.requests.RequestsError, requests.RequestException)` is **False**; its MRO is `RequestException → CurlError → OSError`. Every one of those handlers must become `except (requests.RequestException, curl_cffi.requests.RequestsError)`. Miss this and a transient network error stops being a `return None` and becomes an uncaught exception that kills the worker — exactly the kind of silent famine you are chasing.
   c. `_stealth_headers()` (line ~463) and `DEFAULT_UA_POOL` (line ~101) become actively harmful. When impersonating, the UA, `Sec-CH-UA*`, `Accept` and `Accept-Encoding` come from the profile and are internally consistent with the TLS hello. Your pool rotates a **Firefox 121 UA** and a **Chrome 120/121** UA; sending any of those over a chrome136 handshake is a consistency mismatch that anti-bot scoring is built to catch. Strip `_stealth_headers` down to `Accept-Language` and `Referer` only, delete `_pick_ua()`, and replace the UA pool with an impersonate-profile pool (`["chrome136","chrome142","chrome145","chrome146","chrome150"]`) picked per request via the per-request `impersonate=` kwarg. I verified curl_cffi re-orders merged headers into the profile's canonical order and that JA4 stayed identical (`t13d1516h2_...`) with your headers merged in, so the merge itself is safe — it is the *values* that are wrong.

4. `crawl.py` needs nothing: it only touches `HttpClient.get()` (line 239) and gets the same `tuple[str, str] | None`.

5. Free bonus for the proxy work: the response exposes `.primary_ip` (egress IP as libcurl saw it) and `.local_ip`/`.http_version`. That is the correct primitive for a real proxy health check — and the correct fix for `docs/RUNBOOK-leads.md`, which currently tells the operator that a 403 means "reachable, fine". Assert `status==200` *and* `primary_ip` is the expected exit, not "anything but 000".

6. `pyproject.toml`/`requirements.txt`: add `curl_cffi>=0.16.3`. Note it is **already importable on this dev box at 0.15.0** — but only as a transitive dep of `scrapling` and `yfinance`, which are not HarvestKit dependencies. So it exists on your machine and will not exist on an employee laptop. Declare it explicitly.

**Caveats.** It fixes TLS/H2 fingerprinting and nothing else. I checked what survives: of the 5 domains still 403 after impersonation, hellofresh.de, doctolib.fr and backmarket.fr all returned `cf-mitigated: challenge` with `server: cloudflare` and a "Just a moment..." body — a Cloudflare JS/managed challenge that needs a real JS runtime, not a better handshake. bol.com returned `server: AkamaiNetStorage` with an `ak_bmsc` cookie — Akamai Bot Manager's sensor-data tier, same story. infarm.com looks like a plain Cloudflare firewall/geo deny. No HTTP client, this one included, clears those; that is the tier-3 browser fallback's job.
Second caveat: `impersonate=` must be kept fresh. A chrome99 fingerprint in 2026 is *more* suspicious than no impersonation, because nobody runs Chrome 99. Wire `curl-cffi update` or a pinned `curl-cffi-fingerprints` bump into your release checklist.
Third: the free preset list tops out at chrome150 with a paid tier at impersonate.pro for weekly-updated profiles. You do not need it today; know it exists so a future "why did our rate drop" has an answer.


## Camoufox (tier-3 fallback, not an HTTP client) — trial

https://github.com/daijro/camoufox · MPL-2.0 (it is a Firefox fork). File-level copyleft only: you must publish changes to Camoufox's *own* files, but running it as a separate binary from proprietary HarvestKit code triggers nothing. The Python driver package is MIT-ish over a Playwright API. Safe for a proprietary product as long as you do not patch its source and ship the patch. · Patched Firefox (C++) + Python driver on Playwright's Juggler protocol

**What.** A hardened Firefox build where the anti-detect work is done in the C++ layer rather than by injecting JS into the page — canvas, WebGL, fonts, screen metrics, navigator and hardware entropy are all spoofed below the point where detection scripts can see the patch. Rotates a coherent device profile per context.

**Fit.** This is the answer to the 5 domains curl_cffi cannot reach. My probe showed those are `cf-mitigated: challenge` (Cloudflare managed challenge) and Akamai `ak_bmsc` sensor-data — both require executing the challenge JS. HarvestKit already has the shape for this: `crawl.py` line 12 says "Fall back to plain HttpClient when Playwright isn't installed", and `playwright>=1.45.0` is already a declared dependency. So the escalation tier exists architecturally; Camoufox is a drop-in upgrade of that tier's stealth, because vanilla Playwright Firefox/Chromium is itself trivially detected.

**Maintenance.** Mixed, and this is the reason it is `trial` not `adopt`. MPL-2.0, well regarded, and still the most-cited open-source answer for Cloudflare Turnstile in 2026 — but the maintainer has acknowledged roughly a year-long activity gap through 2025-2026, with redevelopment described as ongoing. Treat it as a component you may have to fork or replace, not a dependency you can stop thinking about. SeleniumBase UC mode and Patchright are the usual alternates in the same tier.

**Safety.** Heavier than curl_cffi by two orders of magnitude: a full custom-compiled Firefox (hundreds of MB) downloaded at first run by `camoufox fetch`, from a project with an admitted maintenance gap. On employee laptops that is a significantly bigger attack surface and a much larger thing to vet.

My recommendation is to NOT ship this to employee laptops at all. Run the browser tier centrally — one hardened box or container you control — and let laptops run only the curl_cffi tier. That also lets you give the browser tier the EU residential exit it needs without provisioning proxy credentials to every endpoint, and keeps the expensive, fragile, large-download component in one auditable place. Pin the binary by hash if you do deploy it.

**Integration.** Do not touch `http.py`. Extend the fetcher-selection already in `crawl.py` (the `fetcher.get(url) if fetcher else self.http.get(url)` branch at line 239) into an explicit three-tier ladder, and escalate on *evidence* rather than blindly:
  T1 `requests` — internal/API endpoints and hosts with a recorded clean history (cheap, keeps robots+cache path).
  T2 curl_cffi impersonate — default for every third-party company domain.
  T3 Camoufox — entered only when T2 returns 403/503 **and** the response carries a challenge signature. Detect it properly instead of guessing: `cf-mitigated: challenge` header, `server: cloudflare` + `<title>Just a moment...`, or a `Set-Cookie: ak_bmsc=` / `server: Akamai*`. Note `_looks_like_block()` at line 66 would miss all of these — hellofresh's challenge body is 5962 bytes and the keyword branch only fires under 5000, so today the challenge is invisible to the heuristic and simply looks like nothing. That is a separate bug worth fixing regardless of which client you pick.
Record the tier that won per host in the existing SQLite cache so the next run starts at the right tier instead of paying the 403 again. That is the 'smart routing' the user asked for, and it is what keeps a browser tier affordable across thousands of domains.

**Caveats.** Slow (seconds per page vs ~0.3 s), memory-hungry, and it will not clear a CAPTCHA that demands human interaction — it clears the automatic/managed ones. Budget it as the exception path for maybe 15-20% of domains, never the default. If the maintenance gap persists into 2027, Patchright or SeleniumBase UC mode are the migration targets.


## wreq-python (formerly rnet) — assess

https://github.com/0x676e67/wreq-python · **Read this carefully — there is a trap.** The GitHub project `wreq-python` is Apache-2.0, fine for proprietary use. But the older PyPI package `rnet` is published under **GPLv3**, which for a product you just relicensed to proprietary (commits 6f59aef, 8c7eb71) is an outright blocker. The repo `0x676e67/rnet` now redirects to `0x676e67/wreq-python` and the package to install is `wreq`, not `rnet`. If anyone on the team types `pip install rnet` from an older blog post, you have imported GPLv3 into a proprietary codebase. Verify the licence of whatever actually lands in your lockfile. · Rust (PyO3/maturin binding over the `wreq` Rust HTTP client)

**What.** Rust-native HTTP client with granular, first-class control of the TLS and HTTP/2 layers — JA3, JA4 and Akamai h2 signatures are constructed from a structured emulation profile rather than replayed from a captured string. Ships 100+ browser device profiles. Async and blocking clients.

**Fit.** Architecturally the most interesting of the lot: because it models the protocol rather than replaying a fingerprint blob, profiles stay coherent across TLS/H2/header layers, which is exactly what the 2026 'consistency score' detectors grade. Windows x86_64 wheels are published. It is the most credible candidate to displace curl_cffi over the next couple of years.

**Maintenance.** Alive and fast-moving, but young and churning. `wreq` 0.12.2 was published 2026-09-16 (five days ago); 1.4k stars, 813 commits. It has already been through a project rename inside the last year, and it is pre-1.0 at 0.12.x — expect breaking changes. Requires Python **>=3.11** (you are on 3.12, so fine, but it narrows your floor from the `requires-python = ">=3.10"` in pyproject.toml). Note the stale `rnet` PyPI listing still shows 2.4.2 from 2025-08-02, which is a full year old — another reason the name confusion is dangerous.

**Safety.** Rust core, so no memory-safety class of bug in the client itself, and no vendored libcurl/BoringSSL fork to fall behind on patches — a genuine structural advantage over curl_cffi. Offset against that: a single pseudonymous maintainer, a project rename mid-flight, pre-1.0 versioning, and prebuilt binary wheels you cannot easily reproduce. Roughly the same trust posture as curl_cffi with less operational history and a much smaller blast-radius-of-scrutiny. The GPLv3-vs-Apache-2.0 split across two package names is itself a supply-chain hazard for you specifically, because getting it wrong is a licensing incident rather than a security one.

**Integration.** Not a requests drop-in — a Rust-idiomatic client with its own `Client`/`Response` types, so `HttpClient.get()` would need a real adapter layer, not a constructor swap: response attribute mapping, an exception-translation shim, and cookie-jar bridging from `requests.cookies.RequestsCookieJar`. Rotating proxies are a first-class feature, but I could not confirm from the docs whether a **per-request** proxy override exists (the pattern shown is client-level rotation), and `_ProxyPool.acquire()` calls a proxy per request. Until that is confirmed, treat it as a rewrite of `http.py`'s transport layer rather than an edit. Sensible plan: adopt curl_cffi now behind a small `Transport` interface, and re-evaluate wreq in ~6 months — if it reaches 1.0 with per-request proxies, the interface makes the swap a day's work instead of a rewrite.

**Caveats.** Do not install the package named `rnet` — GPLv3. Install `wreq` (Apache-2.0) or nothing. Python floor rises to 3.11. Pre-1.0 API instability means a pinned version and a planned re-test, not a `>=` range.


## primp (Python Requests IMPersonate) — assess

https://github.com/deedy5/primp · MIT (per PyPI metadata). Permissive, fine for proprietary. The README carries an "educational purposes only" disclaimer, which is boilerplate posturing rather than a licence term, but note it exists if your counsel reads READMEs. · Rust (PyO3 binding over the rquest/wreq Rust HTTP stack)

**What.** Fast Rust-backed HTTP client with browser impersonation: replicates browser headers plus TLS/JA3/JA4/HTTP2 fingerprints. `Client` and `AsyncClient` classes with a requests-flavoured method surface.

**Fit.** On the pure fingerprinting question it is a peer of curl_cffi — independent tests against live JA3/JA4 echoes show primp, curl_cffi and tls-client all producing the same correct Chrome JA4 over HTTP/2. Its profile list is the freshest of any candidate (Chrome 144-153, Firefox 140-151, Safari 18.5/26.x, Edge 144-153, Opera 126-135) with an `impersonate_os` axis, and it is the fastest of the three. Version 2.0.1 landed 2026-09-13 with **published `win_amd64` wheels** for CPython 3.10-3.14 including the 3.14 free-threading build.

**Maintenance.** Active — 2.0.1 on 2026-09-13, eight days ago. ~600 stars, smaller community than curl_cffi and noticeably thinner documentation (the Python client docs live in a subdirectory and do not document the Response surface at all). One historical smell worth knowing: versions 1.2.0-1.2.2 were **yanked from PyPI for a parallel client initialisation deadlock**, which is precisely the failure class that matters when you construct clients inside a ThreadPoolExecutor. Current releases do not document thread-safety guarantees either way.

**Safety.** MIT, Rust core, no vendored C TLS fork — a good structural position, comparable to wreq. Weaker than curl_cffi on the axis that actually protects you: far fewer eyes. curl_cffi is a transitive dependency of yfinance and scrapling and gets scrutinised accordingly; primp does not have that ambient review. Prebuilt binary wheels, not reproducible by you. The yanked-for-deadlock history is a maintenance-quality signal, not a security one, but it is the kind of bug that would manifest in HarvestKit as a hung run with no log line — the exact symptom class the existing Retry-After comment in http.py was written to prevent.

**Integration.** **This is where it loses to curl_cffi, and the reason is structural, not aesthetic.** `proxy` is a *constructor* parameter on `Client` (e.g. `primp.Client(proxy="socks5://127.0.0.1:9150")`); the documented method signatures expose no per-request proxy override. HarvestKit's `_ProxyPool` picks a proxy per request and pairs a cookie jar to it (`jar_for(entry)`, http.py lines 209-216), and the `HostThrottle` key is literally `f"{host}|{proxy_url}"` — a per-request proxy identity is baked into the design. Adopting primp means building and caching one `Client` per proxy entry, wiring `_ProxyPool` to hand back clients instead of dicts, and reasoning about N pools of connections instead of one — a genuine redesign of `HttpClient`, on top of a library whose thread-safety is undocumented and which has already shipped a parallel-init deadlock.
Second friction: `headers` is documented as **ignored when `impersonate` is set**, so `_stealth_headers()` stops working entirely rather than merging (curl_cffi merges and re-orders, which I verified). You would lose per-request `Referer` and `Accept-Language` control unless there is an undocumented path.

**Caveats.** Revisit only if curl_cffi's uplift plateaus and you need raw throughput, or if per-request proxy support lands. It is a good library that happens to be shaped wrong for this codebase.


## tls-client (bogdanfinn) + its Python wrappers — assess

https://github.com/bogdanfinn/tls-client · **BSD-4-Clause** on the Go core — the original four-clause BSD, which includes the advertising clause requiring that all advertising materials mentioning features of the software display an attribution acknowledgement. That is a real, if unfashionable, obligation for a commercial product, and it is incompatible-ish with GPL though that does not affect you. The Python wrappers vary independently (`tls-client-python`, `noble-tls`, `wrapper-tls-requests`, `async-tls-client` are separate projects with separate licences you would have to check individually). Compared to curl_cffi's clean MIT, this is strictly more legal homework. · Go core (utls-based) exposed as a C shared library, consumed from Python over CFFI

**What.** Go HTTP client built on utls that gives full control over the ClientHello and HTTP/2 frame settings, with named browser profiles (Chrome 150 among them), HTTP/1.1, HTTP/2, HTTP/3, WebSockets, certificate pinning, cookie management and bandwidth tracking. HTTP and SOCKS5 proxies supported.

**Fit.** Technically it works — it produces a correct Chrome JA4, same as curl_cffi and primp. It is the oldest and most battle-tested of the three engines and the one most sneaker/retail automation is built on, so its profiles get adversarial testing at volume.

**Maintenance.** The Go core is maintained (385 commits, 10 open PRs, ~1.9k stars, Chrome 150 profile present), and the README itself notes the *original upstream* projects it forked are no longer maintained — this fork is the living branch. The problem is the Python side: there is no single canonical binding. `tls-client-python`, `noble-tls` (which auto-updates JA3 fingerprints), `wrapper-tls-requests` (1.2.5, 2026-02-23) and `async-tls-client` (2026-03-23) are four separate community wrappers of varying quality and release cadence. You would be picking a maintainer, not a library.

**Safety.** The worst supply-chain shape of the serious candidates, for a reason that has nothing to do with the authors' intent: you are shipping a **Go runtime compiled into a C shared library**, loaded via CFFI, wrapped by a *third-party* Python package that is not the same project as the core. That is three trust boundaries (Go core author → shared-library build → Python wrapper author) instead of curl_cffi's one. On employee laptops, each boundary is a separate thing to vet and a separate thing to monitor for a hijacked release. The wrapper layer is the weak link: these are small packages with few maintainers and high install counts — a classic PyPI account-takeover target.

**Integration.** Depends entirely on which wrapper you pick, which is itself the argument against it. `noble-tls` is explicitly "based on requests" and would map most cleanly onto `HttpClient`, but it is async-first now, which fights HarvestKit's synchronous `ThreadPoolExecutor` design in `deep_scrape.py`. Any route requires shipping and loading a platform-specific `.dll` on Windows and translating a bespoke exception and response model. Realistically 3-5× the integration effort of curl_cffi for the same measured uplift.

**Caveats.** Only worth revisiting if curl_cffi's fingerprints ever fall behind on a target you specifically need and the pro tier is not an option. The BSD-4-Clause advertising clause should be run past whoever handled the proprietary relicence before anything ships.


## Scrapling — assess

https://github.com/D4Vinci/Scrapling · BSD-3-Clause (verified from the installed dist metadata on this machine: `scrapling 0.4.7`, "BSD 3-Clause License, Copyright (c) 2024, Karim shoair"). No advertising clause, proprietary-friendly. · Python

**What.** A fetching + adaptive-parsing framework rather than an HTTP client. Its `Fetcher`/`StealthyFetcher` tiers wrap curl_cffi for the impersonation layer and Camoufox/Playwright for the browser layer behind one API, and its headline feature is a parser that survives DOM drift — selectors re-locate elements when class names and structure change.

**Fit.** Two reasons it is worth knowing about, neither of which is "replace http.py". First, **it is already installed on this dev machine at 0.4.7** and is in fact the reason `curl_cffi 0.15.0` is importable here at all (`scrapling -> curl_cffi>=0.15.0; extra == "fetchers"`). Nothing in HarvestKit imports or declares it — I grepped; there are zero references in the repo — so this is incidental dev-box state, not a dependency. Worth knowing so nobody concludes "curl_cffi already works here" and forgets to declare it. Second, its adaptive-parser idea is directly relevant to the other half of your lead famine: even on the domains that now return 200, extraction breaks silently when a careers page restructures, and 453 green unit tests will not tell you.

**Maintenance.** Active through 2026 and widely cited in current anti-bot tooling round-ups as the tool that wires the other tiers together. Smaller and younger than the primitives it wraps.

**Safety.** Pure Python, BSD-3, no native code of its own — its risk is entirely inherited from whatever it pulls in (curl_cffi, Camoufox, Playwright), each of which you would be better off depending on directly and pinning yourself. A framework dependency also means its upgrade cadence dictates yours: if it pins an older curl_cffi, you inherit that, including a CVE window. For a codebase that already owns a considered `HttpClient` with throttling, proxy health, robots and caching, taking the framework means either duplicating or discarding that work.

**Integration.** Do not adopt as a dependency. Take the two ideas instead: (1) the tiered fetcher ladder, which you implement yourself in `crawl.py` as described in the Camoufox entry; (2) resilient extraction — worth a look at how its adaptive matching works before the next round of `extract.py`/`universal.py` selector maintenance. If you ever do want the whole stack turnkey rather than hand-rolled, this is the one to evaluate, but it is a different product decision, not a bug fix.

**Caveats.** Its presence on this machine is accidental and will NOT be present on an employee laptop. Whatever you do, declare `curl_cffi` explicitly in pyproject.toml and requirements.txt — relying on it being importable because something else dragged it in is exactly the kind of environment-dependent behaviour that makes a scraper work on the dev box and return nothing in the field.


## hrequests — avoid

https://github.com/daijro/hrequests · MIT · Python over a bundled Go TLS client, plus BrowserForge header generation and Playwright/Camoufox browser tiers

**What.** Aims to be the all-in-one requests replacement: TLS fingerprint spoofing via a bundled Go backend, realistic header generation via BrowserForge, HTML parsing, gevent-based concurrency, and headless browsing in one package. `os='win'|'mac'|'lin'` session parameter.

**Fit.** On paper it is the closest to what the user asked for — one library, terminal-first, impersonation plus browser fallback plus parsing. That is why it appears on every "best scraping library" list.

**Maintenance.** **This is the disqualifier. Last release 2024-11-28 — roughly 22 months stale as of today.** ~1.0k stars, 63 commits, issues still being filed into 2026 but not resolved into releases. Not formally archived, which is worse than archived: it looks maintained at a glance.

**Safety.** Staleness *is* the security finding here. A bundled Go TLS binary that has not been rebuilt since November 2024 has 22 months of unpatched upstream Go and crypto fixes in it, and you would be putting that on employee laptops. On top of that, its BrowserForge header data and Go client profiles are frozen at roughly Chrome 124-era — so in 2026 it does not merely fail to help, it actively *hurts*: a 2024 fingerprint presented in 2026 is a stronger bot signal than an honest Python one, because no human runs a two-year-old Chrome. Recommend against, without qualification.

**Integration.** None. Do not integrate. If you want its feature set, it is curl_cffi (impersonation) + your existing Playwright/Camoufox tier (browser) + your existing BeautifulSoup/lxml (parsing) — components you pin and update independently rather than one frozen bundle.

**Caveats.** If it resumes releases with current profiles, re-evaluate. Until a 2026-dated release exists, treat any blog post recommending it as out of date.


## httpx (and requests) — the incumbent, for completeness — avoid

https://github.com/encode/httpx · BSD-3-Clause (httpx); Apache-2.0 (requests). Both installed here: httpx 0.28.1, requests 2.33.1. · Python

**What.** Excellent general-purpose HTTP clients. httpx adds HTTP/2 and async over requests' API. Neither offers any control over the TLS ClientHello.

**Fit.** It does not, for this job, and the measurement says so precisely. Both delegate TLS to the system OpenSSL, so the cipher list, extension order and GREASE placement are baked in below the Python layer and cannot be patched from above. I measured plain `requests` on this machine fingerprinting as JA4 `t13d2812h2_257f3020b3a2_cbb9361b6bf9` — 28 ciphers, the canonical Python signature — against Chrome's `t13d1516h2_8daaf6152771_d8a2da3f94cd`. Switching requests→httpx changes nothing about that; httpx's HTTP/2 SETTINGS frame is also its own, distinct from Chrome's. Every hour spent tuning `_stealth_headers()` in http.py is spent above the layer where the decision is already made. **This is the single most important finding of this research: the headers were never the problem.**

**Maintenance.** Both are healthy, mainstream, and should stay in the project.

**Safety.** The safest things in this report by a wide margin: pure Python, huge review surface, no native binaries beyond the system OpenSSL you already trust. That is exactly why they should keep serving the traffic that does not need impersonation.

**Integration.** Keep `requests` as tier 1. It remains correct for internal endpoints, ATS JSON APIs, `robots.txt` fetches, MX/DNS-adjacent work, and any host with a recorded clean history — which per my 35-domain sample is still the majority (22/35 already return 200 to plain requests). Do not rip it out; route around it. Concretely: keep `HttpClient` as the public interface, put the transport behind a small internal seam, and pick per-host based on recorded outcome. That preserves the throttling, robots, caching and proxy-health machinery the codebase already has — which is genuinely good work and should not be collateral damage of a client swap. There is no reason to add httpx; it buys nothing requests does not, and nothing curl_cffi does.

**Caveats.** Avoid *for fingerprint-sensitive fetches only*. As general HTTP clients they are fine and requests should remain a declared dependency.


## curl_cffi — adopt

https://github.com/lexiforest/curl_cffi · MIT (the binding); bundles prebuilt curl-impersonate native libs in the wheel · Python (CFFI binding over a patched libcurl / curl-impersonate)

**What.** A requests/httpx-shaped HTTP client that reproduces a real browser's TLS ClientHello (JA3/JA4), HTTP/2 SETTINGS + pseudo-header order, and HTTP/3, selected by `impersonate="chrome"` / `"firefox"` / `"safari"` or a pinned version like `chrome142`. Sessions, cookie jars, http/socks proxies and an AsyncSession are all supported.

**Fit.** This is the single highest-yield change measured against HarvestKit's actual target population. I ran 38 mid-market EU company homepages (personio.de, contentful.com, raisin.com, doctolib.fr, qonto.com, klarna.com, docplanner.com, ...) through two tiers from this machine: `requests` carrying HarvestKit's current `_stealth_headers` failed 8/38 (21%); `curl_cffi` with `impersonate="chrome"` failed 3/38 (8%). Named flips: personio.de 429 -> 200 (1.78 MB), contentful.com 429 -> 200 (814 KB), raisin.com 403 -> 200 (211 KB), messagebird.com 502 -> 200 (878 KB), brainly.com 403 -> 200 (262 KB). On the lead investigator's 6-domain set it also recovered getyourguide.com (403 -> 200, 293 KB). Crucially, `httpx` with `http2=True` and identical corrected headers scored the *same* as `requests` with corrected headers on that set — HTTP/2 alone bought nothing. What moves these sites is the ClientHello, which `requests`/urllib3 cannot alter at all. It is also already importable in this environment at 0.15.0, so the cost of trialling it is zero.

**Maintenance.** Alive and current. 6.5k stars, ~582 commits on main, releases through 2026 tracking new Chrome fingerprints (chrome142/145/146, Chrome 152 trust_anchors extension). lexiforest is the de-facto upstream now that the original curl-impersonate is quiet; the author also sells a paid 'impersonate.pro' fingerprint tier, which is a sustainability signal but also means the free target list lags the paid one slightly.

**Safety.** Moderate, manageable. The wheel ships a prebuilt native libcurl-impersonate — a binary blob from PyPI running in-process on an employee laptop, so a compromised release is native-code execution, not sandboxed Python. Mitigate by pinning an exact version with a hash (`curl_cffi==0.15.0 --hash=sha256:...`) in a lockfile, and mirroring the wheel to an internal index if one exists. Data-exposure surface is no worse than `requests`: it makes outbound TLS calls only, phones nothing home, and the fingerprint data is static tables in the package. Note also that impersonation deliberately misrepresents the client to the server; that is a policy question for the operator, not a technical risk to the laptop.

**Integration.** A sibling agent has already landed `src/job_scraper/transport.py` with `ImpersonateTransport` at rung 1, so the code path exists. The gap is deployment: `curl_cffi` is in neither `requirements.txt` nor `pyproject.toml` `[project].dependencies`. It imports fine here because this dev machine happens to have 0.15.0 installed, which means `ImpersonateTransport.available()` will silently return False on every employee laptop and the whole rung will be dead in production while passing every test locally. Add `curl_cffi>=0.15,<1.0` to both files. Second gap: `identity.py` must NOT override the UA or sec-ch-ua on this rung — curl_cffi emits a header set matched to the chosen profile, and overriding it is exactly what breaks the match. Pass only `accept-language` (country-derived) and `referer`. Third: a curl_cffi Session wraps a libcurl handle that is not thread-safe; `pipeline.py` runs an 8-wide ThreadPoolExecutor, so hold one Session per thread via `threading.local()` (transport.py already does this — keep it).

**Caveats.** Not a Cloudflare-challenge solver. hellofresh.de returned 403 with a 5,962-byte 'Just a moment...' interstitial (markers: `cloudflare`, `/cdn-cgi/challenge-platform`, `enable javascript`) at every HTTP rung including curl_cffi; only a real browser clears that. Also: pinning a stale profile (`chrome120`) is worse than the floating `chrome`, because a two-year-old fingerprint is itself a reputation signal.


## Patchright (Python) — adopt

https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python · Apache-2.0 · Python

**What.** A drop-in patched fork of the Playwright Python package that closes the CDP-level leaks stock Playwright emits: it removes the `Runtime.enable` leak by executing scripts in isolated contexts, disables `Console.enable`, strips `--enable-automation`, adds `--disable-blink-features=AutomationControlled`, and can reach into closed shadow roots. Same `sync_playwright()` / `async_playwright()` API.

**Fit.** HarvestKit already depends on `playwright>=1.45.0` and already launches Chromium in `crawl.py`'s `PlaywrightFetcher`, so the browser is already in the threat model and the install footprint. Patchright is a one-line import swap that makes that existing rung actually survive the sites it exists for. It is the rung that recovers the residual ~8% — hellofresh.de's Cloudflare managed challenge and doctolib.fr's 135 KB captcha interstitial are both browser-only. In the 2026 open-source stealth benchmarks Patchright scores at or near the top while being the least invasive option.

**Maintenance.** Active. ~1.5k stars on the Python package (the org's tooling ~4k across repos), commits through August 2026, published on PyPI. Its one structural risk is that it must chase upstream Playwright releases, so a Playwright bump can strand it briefly.

**Safety.** Low incremental risk for this repo specifically, because Playwright + a downloaded Chromium is already a dependency. Patchright is pure Python patching plus its own Chromium download (`patchright install chromium`); it does not ship a custom-built browser engine. On employee laptops the real considerations are unchanged from today: a browser process with a persistent profile directory holds cookies for every site crawled, so the profile dir must live in the run directory and be wiped between runs, not in the user's home. Pin the version and the Chromium revision.

**Integration.** `transport.py`'s `BrowserTransport` already prefers `patchright` over `playwright` when importable — so, as with curl_cffi, the only real work is declaring it: add `patchright>=1.48` to `pyproject.toml` `[project.optional-dependencies]` (call it `stealth`) and document `patchright install chromium` in the runbook, or the rung is permanently unavailable in the field. Beyond that, two things are missing and matter more than the swap itself: (1) a global semaphore + per-run budget on browser launches — at ~80-150 MB and 1-2 s per page, an unbudgeted rung 2 will OOM a laptop crawling thousands of companies; (2) the cf_clearance handoff. When rung 2 clears a challenge, export `context.cookies()` plus the exact UA the context used and hand them to rung 1's curl_cffi session, pinned to the same egress IP and the same impersonate profile, then re-drop to rung 1 for the remaining 5-14 pages of that company. The clearance cookie is bound to IP + UA + JA3, so rotating the proxy or switching impersonate profile mid-company kills it on the first reuse.

**Caveats.** Chromium only — no Firefox/WebKit. Console API is disabled as a stealth trade-off, so anything in `crawl.py` that reads console messages will go quiet. It also does not fix a bad egress IP: a challenge solved from a datacentre IP on a blocklist will simply re-challenge.


## BrowserForge — trial

https://github.com/daijro/browserforge · Apache-2.0 · Python

**What.** Generates coherent browser header sets — in the correct order, with matching `sec-ch-ua`/`sec-ch-ua-platform`/UA/Accept/Accept-Language/HTTP version — and matching JS-level fingerprints, sampled from a Bayesian network trained on real traffic distributions. ~0.1-0.2 ms per generation. It is the header/fingerprint generator Crawlee for Python uses internally (`BrowserforgeHeaderGenerator`).

**Fit.** HarvestKit's current header story is the second-largest source of 403s after TLS, and it is incoherent in ways no real browser can be. `_stealth_headers()` pins `Sec-CH-UA: "Chromium";v="120"` while `_pick_ua()` draws randomly per request from a pool containing Firefox 121 and Safari 17 UAs — so one host sees a Firefox UA carrying Chromium client hints, over one connection, sharing one cookie jar. It hardcodes `Sec-Fetch-Site: same-origin` on every request including the very first navigation to a domain (real Chrome sends `none`). It sends `DNT: 1` (Chrome removed the DNT setting entirely) and `Cache-Control: no-cache` + `Pragma: no-cache` on every request (Chrome sends those only on a hard reload — every navigation being a hard reload is a signature). It omits `priority: u=0, i`, which Chrome has sent since v117. And it sends one five-language `Accept-Language` to Norway and Portugal alike. This is measurable, not theoretical: swapping only these headers — nothing else, still plain `requests` over HTTP/1.1 — flipped www.zalando.de from 403 with a 160,907-byte block page to 200 with a 575,436-byte real page. BrowserForge gives you that coherence as data instead of as a hand-maintained dict that goes stale.

**Maintenance.** ~1.2k stars, Apache-2.0, adopted as a dependency by Crawlee for Python, which is the strongest maintenance signal available for a library of this size — Apify has an interest in it staying current. Commit cadence is modest (~32 commits on main); treat it as a stable data package rather than a fast-moving one.

**Safety.** Lowest risk on this list. Pure Python, no native code, no browser download, outbound network only if the model data is fetched. Older releases pulled fingerprint model data over the network on first use; confirm the pinned version bundles it, and if it does not, pre-seed the cache during install so an employee laptop does not make a surprise download on first run. It reads nothing from the host machine.

**Integration.** Use it as the data source behind the already-landed `src/job_scraper/identity.py`, not as a replacement for it. `identity_for(host)` should keep its current and correct design — derive a *stable* identity per registrable domain so a host sees one browser for the whole run — but populate that identity from `HeaderGenerator().generate(browser='chrome', os='windows', locale=<country locale>)` instead of the hand-written `DEFAULT_UA_POOL` (currently Chrome 120/121 and Safari 17.1, roughly two years stale against Chrome 141+ in September 2026). Then map the generated browser/version onto the curl_cffi `impersonate` profile so rungs 0 and 1 present the *same* browser. One thing BrowserForge cannot fix: `requests`/urllib3 will not let you control wire header order (it always injects its own `Connection: keep-alive` and its session defaults first), so rung 0 gets the right header *values* but not Chrome's order — `sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, upgrade-insecure-requests, user-agent, accept, sec-fetch-site, sec-fetch-mode, sec-fetch-user, sec-fetch-dest, accept-encoding, accept-language, cookie, priority`. Order fidelity is another reason rung 1 exists.

**Caveats.** It generates a plausible identity; it does not know what the *target country* wants. Keep `identity.py`'s `ACCEPT_LANGUAGE_BY_COUNTRY` table and override the generated Accept-Language with it — a German site receiving `de-DE,de;q=0.9,en-US;q=0.8` from a German-ish IP is the coherent story; a generated `en-US` is not.


## Crawlee for Python — assess

https://crawlee.dev/python/ · Apache-2.0 · Python (asyncio)

**What.** Apify's crawling framework. The parts relevant here are `SessionPool` (sessions carrying a proxy + cookie jar + headers, retired on `max_usage_count` / `max_age` / `max_error_score` / `blocked_status_codes`), `AdaptivePlaywrightCrawler` (a `RenderingTypePredictor` that chooses static-HTTP vs browser per request and *learns* from outcomes), `ImpitHttpClient` (Rust-backed impersonating client, the v1 default), and Browserforge-based fingerprint generation.

**Fit.** As a source of design, strongly. As a runtime for HarvestKit, no — and I want to be unambiguous about that. Its `SessionPool` is the exact semantics HarvestKit's identity layer should copy: bind UA + headers + cookie jar + proxy into one object, retire it on a usage count, an age, or an error score, and treat a 403 as an immediate retire rather than a retry. Its `AdaptivePlaywrightCrawler` is the one piece of published prior art for the escalation ladder that solves the ratchet problem: `RenderingTypePrediction` carries a `detection_probability_recommendation` (0-1) that makes the crawler *re-run both tiers* some fraction of the time and compare results via a `result_comparator`, so a domain that was briefly angry gets demoted back to the cheap rung instead of paying browser cost forever. HarvestKit's new `TransportMemory` currently only ratchets upward; it needs exactly this epsilon.

**Maintenance.** Very healthy. v1.0 stable shipped in 2026 with adaptive crawling and unified storage; Apache-2.0; commercially backed by Apify; active release cadence.

**Safety.** Library itself is low-risk and self-hostable with no Apify account. Two things to verify before it touches a laptop: (a) the dependency tree is large (asyncio stack, Browserforge, Impit native wheels, optional Playwright) — more surface than the three libraries above combined; (b) confirm no usage telemetry is enabled by default in a self-hosted configuration. The Apify-platform integration is opt-in, but an org deploying to employee machines should verify that rather than assume it.

**Integration.** Do not integrate. Port the ideas into the existing modules instead: (1) In `identity.py`, add a `Session` object holding `{identity, cookie_jar, proxy_entry, uses, created_at, error_score}` keyed by registrable domain, retired at ~50 uses / ~20 minutes / error_score>=3 / any 403-classified outcome — that is Crawlee's SessionPool in ~40 lines and it fits the threaded model. (2) In `transport.py`, give `TransportMemory.preferred_rung()` an epsilon: with probability ~0.1, try one rung below the remembered one and record the result, so a domain can walk back down. (3) Copy `blocked_status_codes` semantics into `_ProxyPool`: retire the *session*, not the proxy, on a 403.

**Caveats.** The blocker is the concurrency model, not quality. HarvestKit is synchronous top to bottom — `pipeline.py` uses `ThreadPoolExecutor(max_workers=concurrency)`, `deep_scrape.py` likewise, every adapter takes a sync `http` object, and 453 tests are written against that. Crawlee is asyncio-first with async handlers. Adopting it means rewriting the pipeline, the CLI, all 14 adapters and the test suite in order to replace ~600 lines of `http.py`.


## Scrapy + scrapy-impersonate — avoid

https://github.com/jxlil/scrapy-impersonate · MIT (scrapy-impersonate); BSD-3 (Scrapy) · Python (Twisted)

**What.** Scrapy is the incumbent large-scale Python crawling framework — scheduler, dupefilter, middlewares, AutoThrottle. `scrapy-impersonate` is a download handler that routes Scrapy requests through curl_cffi, so Scrapy requests carry a real browser TLS/JA3 fingerprint; it accepts `chrome`, `firefox`, `safari`, `edge`, `tor` or pinned versions like `chrome133a`, and requires the asyncio Twisted reactor.

**Fit.** It mostly does not, and the reason is worth stating because Scrapy is the obvious default answer. Scrapy's value is its frontier: scheduler, dupefilter, depth/priority queues, and AutoThrottle — all built for crawling *one or a few sites deeply*. HarvestKit's lead pipeline does the opposite shape of work: roughly 6-14 targeted requests (`/team`, `/impressum`, `/ueber-uns`, `/contact`, per `person/paths.py`) against each of thousands of *distinct* origins. Almost none of the frontier machinery earns its keep on that shape, while all of the rewrite cost is paid. The one Scrapy idea genuinely worth stealing is AutoThrottle's core rule — target delay = latency / target_concurrency, averaged with the previous delay, and *never let a non-200 latency decrease the delay* (error pages return fast, so a naive latency-based controller speeds up exactly when a host starts refusing you). HarvestKit's fixed `HostThrottle` has precisely that hole.

**Maintenance.** Scrapy is at 2.16 and healthy. scrapy-impersonate is smaller — ~242 stars, ~98 commits, MIT, tracking curl_cffi's target list. It is a thin, comprehensible shim rather than an abandoned one, but it is one maintainer deep.

**Safety.** Inherits curl_cffi's native-binary consideration and adds Twisted plus Scrapy's middleware surface. For an employee-laptop deployment the incremental supply-chain surface over the current `requests`+`playwright` baseline is large and, on this analysis, unrewarded.

**Integration.** No integration. Port one algorithm instead: in `http.py`'s `HostThrottle`, replace the fixed `min_delay` with an AutoThrottle-style controller keyed on registrable domain — `target = latency / target_concurrency; delay = (delay + target) / 2`, clamped to `[min_delay, max_delay]`, and skip the update entirely when the response classified as anything other than `Outcome.OK`. That is about 20 lines and captures the part of Scrapy that HarvestKit is actually missing.

**Caveats.** If HarvestKit's shape ever changes to millions of pages a day against a stable target list with distributed scheduling, revisit — that is genuinely Scrapy's home ground. It is not today's shape.


## impit — assess

https://github.com/apify/impit · Apache-2.0 · Rust core with Python and Node bindings (PyO3, native wheels)

**What.** Apify's browser-impersonating HTTP client: reqwest + rustls + tokio underneath, HTTP/1.1, HTTP/2 and HTTP/3, ~20 browser profiles (Chrome and Firefox families, e.g. Firefox 144), exposed through an httpx/requests-shaped Python API. It is the default HTTP client in Crawlee for Python v1 (`ImpitHttpClient`).

**Fit.** It is the credible alternative to curl_cffi for rung 1, and having a second option matters because rung 1 is the load-bearing rung. It is Rust rather than a patched libcurl, Apache-2.0 with a company behind it, and HTTP/3 is native. If curl_cffi's single-maintainer/paid-tier dynamic ever becomes a problem, impit is the swap — and `transport.py`'s rung abstraction means that swap is one class, not a rewrite.

**Maintenance.** Younger and smaller than curl_cffi: ~593 stars, ~524 commits on master, benchmarks dated September 2026. Real backing from Apify via Crawlee, which is the reason to take it seriously despite the star count.

**Safety.** Apache-2.0, native wheels — same 'binary blob from PyPI' consideration as curl_cffi, with fewer independent eyes on it. It also builds against *patched forks* of `rustls` and `h2` hosted on Apify's own GitHub plus a `--cfg reqwest_unstable` flag; that is a legitimate engineering necessity for fingerprint control, but it means you are trusting forks of two security-critical crates rather than the upstream releases. For a laptop fleet, prefer the library with the larger audit surface until impit's adoption grows.

**Integration.** Nothing to do now. Keep `transport.py`'s `Transport` base class honest — `available()`, `fetch()`, `rung`, `close()` — so adding `ImpitTransport` later is ~60 lines slotted at rung 1 alongside `ImpersonateTransport`, with `TransportMemory` unchanged. If you ever want to A/B the two, the rung abstraction plus the canary domain list (below) gives you the measurement harness for free.

**Caveats.** Fewer impersonation targets than curl_cffi (~20 vs ~37+) and much less field history. HTTP/3 support is real but, per my measurements, protocol version is not what these EU sites are scoring — do not adopt it *for* HTTP/3.


## Camoufox — assess

https://github.com/daijro/camoufox · MPL-2.0 (browser); MIT (Python package) · C++ (patched Firefox) with a Python wrapper

**What.** A Firefox fork patched at the C++ engine level so that fingerprint surfaces (navigator, canvas, WebGL, fonts, screen, timezone, locale) are injected below the JavaScript layer — there is no JS patch for a detection script to find. Ships with proxy and geolocation/locale spoofing wired in.

**Fit.** It is the strongest open-source answer for the hardest residual — the doctolib.fr-class interstitials (403, 135,095-byte body, empty `<title>`, captcha/challenge markers) that survive every HTTP rung and that a patched-automation approach may still lose to. For HarvestKit that residual is small: after the corrected-header and TLS-impersonation rungs, my 38-domain sample was down to 3 failures, and one of those (gorillas.io, 200/2,394 bytes) is a genuine tombstone page rather than a block. Buying the last ~5% with a custom browser engine is a poor trade for this product.

**Maintenance.** Very active and popular — ~11k stars, pushes through August/September 2026, regular Python package releases. Not a maintenance concern; a deployment concern.

**Safety.** The highest-risk item here for the stated deployment, and the reason is the deployment, not the project. Camoufox downloads a large (~100-200 MB) custom-built Firefox binary from GitHub Releases at first run. Putting a browser engine that did not come from Mozilla onto employee laptops means: no vendor security-update channel, EDR/allowlisting almost certainly flags or blocks it, and any upstream Firefox CVE is patched on the fork's schedule rather than Mozilla's. It is a security-review item, not a `pip install`. On a dedicated scraping VPS this calculus flips entirely and it becomes reasonable.

**Integration.** Keep it out of the laptop build. If the residual justifies it, implement it as an *optional* rung 3 behind an explicit config flag (`browser_engine: camoufox`) that is off by default and refuses to activate unless an env var set by the ops image is present — so a laptop install can never silently pull a browser engine. The `Transport` interface makes this a sibling of `BrowserTransport` with no other code changes. Better still: route the residual through a single hardened VPS worker rather than shipping the engine to N laptops.

**Caveats.** Firefox-based, so its TLS/HTTP2 fingerprint is a Firefox one — if you have already trained a domain's `TransportMemory` on a Chrome identity, escalating to Camoufox changes browser identity mid-domain, which is itself incoherent. Pick one browser family per domain and stay in it across rungs.


## zendriver (maintained fork of nodriver) — assess

https://github.com/cdpdriver/zendriver · MIT · Python (asyncio, direct CDP)

**What.** Drives an unmodified Chrome over raw Chrome DevTools Protocol with no WebDriver and no Playwright injection layer at all — the successor lineage to undetected-chromedriver, written by the same author (nodriver) and continued by the community as zendriver after nodriver's cadence slowed.

**Fit.** It is the natural fallback if Patchright ever stops tracking upstream Playwright, and it wins on a specific axis: because there is no automation framework in the page, there is nothing to patch away. For HarvestKit's browser rung — which needs to load a `/team` page, dismiss a consent banner, and read the DOM — the CDP-direct approach is entirely sufficient.

**Maintenance.** zendriver ~1.4k stars with pushes through August 2026; upstream nodriver ~4.6k stars but last push around May 2026 and drifting. Prefer zendriver if you go this route; note the fork exists precisely because the original stopped taking contributions.

**Safety.** MIT, pure Python, drives a stock Chrome you already trust — arguably the *cleanest* supply chain of any browser option here, since nothing about the browser binary is modified. The caution is asyncio: bolting an async driver into HarvestKit's threaded pipeline means running an event loop per worker thread or serialising through one, and that seam is where resource leaks live.

**Integration.** Only relevant as an alternative implementation of `transport.py`'s `BrowserTransport`. If Patchright breaks on a Playwright bump, write `ZendriverTransport(Transport)` at the same `rung = 2`, run a single dedicated event loop in one background thread, and hand page requests to it over a queue — do not spin a loop per worker. Everything above it (`TransportMemory`, classification, the cf_clearance handoff) is unchanged.

**Caveats.** In the 2026 benchmarks it scores below Patchright and Camoufox on the hardest walls. It is the insurance policy, not the first pick.


## Botasaurus — avoid

https://github.com/omkarcloud/botasaurus · MIT · Python

**What.** An all-in-one scraping framework bundling its own browser driver, anti-detect layer, caching, human-like interaction simulation, and tooling to wrap a scraper as a desktop app or web UI.

**Fit.** It does not fit HarvestKit. It is a framework that wants to own the whole program — driver management, caching, output, UI — and HarvestKit already has all of that, in a shape tuned to its 56-field schema, its checkpointing, its exporters and its 453 tests. Adopting Botasaurus means discarding that; cherry-picking from it means depending on a large framework for a small part. It is included here because it appears in every 'best scraping framework' listicle and someone will suggest it.

**Maintenance.** MIT and reasonably popular, but the cadence is uneven — the main repo was last meaningfully updated some months before this writing. For the component sitting between you and every anti-bot vendor, 'reasonably active' is not enough; that component needs to move as fast as Chrome does.

**Safety.** The largest and least auditable surface on this list for a laptop fleet: heavy transitive dependency tree, its own driver-download machinery, and framework code paths that execute a lot on your behalf. A wide dependency graph with uneven maintenance is exactly the profile you do not want auto-installed on employee machines.

**Integration.** None. If one of its behaviours proves valuable — its human-like interaction timings, say — reimplement that specific behaviour inside `BrowserTransport` rather than taking the dependency.

**Caveats.** No reflection on the project's quality for greenfield single-file scrapers, where it is genuinely productive. It is the wrong shape for an established codebase with its own architecture.


## katana (ProjectDiscovery) — assess

https://github.com/projectdiscovery/katana · MIT · Go (standalone binary, also a Go library)

**What.** A fast crawler built for pipelines: headless and non-headless modes, JavaScript parsing and endpoint extraction (`-jc`, `-jsl` via jsluice), robots/sitemap seeding (`-kf`), XHR endpoint extraction (`-xhr`), form auto-fill, and passive discovery from Wayback/CommonCrawl/AlienVault OTX.

**Fit.** Relevant to a specific HarvestKit weakness, not to the fetch path. `person/paths.py` guesses person-page URLs from a hardcoded per-country list (`/impressum`, `/ueber-uns`, `/geschaeftsleitung`, `/equipe`, ...) and `crawl.py` does a plain BFS over `<a href>`. Both miss team pages that live at unguessed paths or are loaded via XHR — a real cause of 'company named nobody' that has nothing to do with blocking. Katana's sitemap seeding and XHR/JS endpoint extraction find those. Its *passive* mode is more interesting still: querying Wayback/CommonCrawl for a domain's known URLs costs the target zero requests and would surface `/about/leadership-team` without any guessing.

**Maintenance.** Excellent. ProjectDiscovery is a funded security-tooling company, katana is MIT with frequent releases through 2026 and a large user base.

**Safety.** Low risk as software (MIT, reputable vendor, single static binary), but it introduces a second runtime and a second update channel onto every employee laptop alongside the Python CLI. It is also an offensive-security tool by branding and default settings — the auto-form-fill (`-aff`) and aggressive crawl profiles are inappropriate for a lead-gen crawl and would need to be explicitly disabled. If used, use it narrowly and passively.

**Integration.** Do not shell out to a Go binary from the lead pipeline. Instead take the *idea* into `person/paths.py`: before guessing paths, fetch `https://<domain>/sitemap.xml` (HarvestKit already has `safe_xml.py` for safe parsing) and select URLs whose path matches the person-page patterns — one cheap request that beats six or ten guesses. Optionally add a passive CommonCrawl/Wayback index lookup for domains where the sitemap is absent. Reserve katana itself for an offline, operator-run enrichment pass on a VPS if the sitemap route proves insufficient.

**Caveats.** Its headless mode overlaps `BrowserTransport` and would fragment the identity/session model — two different crawlers presenting two different browsers to the same origin is exactly the incoherence to avoid. Use it for discovery, never for fetching.


## pybreaker — assess

https://github.com/danielfm/pybreaker · BSD · Python

**What.** A small, stable implementation of the circuit-breaker pattern: closed -> open after N consecutive failures -> half-open single probe after a reset timeout -> closed or re-open, with listeners and pluggable storage.

**Fit.** HarvestKit has no breaker at any level, and the absence shows in a specific way I observed live: after repeated probes from this IP within a minute, www.zalando.de stopped answering and read-timed out at 20 s rather than returning 403. That is tarpitting, and a fixed-rate throttle cannot see it — it will keep queueing workers against a host that has decided to stop talking, burning the 20 s timeout `leadgen/cli.py` sets, per request, per worker. A per-domain breaker converts that from minutes of dead time into one cheap probe.

**Maintenance.** Stable and maintained (1.4.x, releases through 2025), BSD, tiny surface. Its sibling `aiobreaker` covers asyncio; `tenacity` (Apache-2.0) is the modern retry library now that `backoff` is retired, if you want decorator-based retry as well.

**Safety.** Negligible. Pure Python, no native code, no network, no transitive weight.

**Integration.** Honestly, assess rather than adopt — the dependency probably is not worth it, because what HarvestKit needs is a *keyed, persisted* breaker and pybreaker gives you a single unkeyed one. The right shape is about 30 lines inside the existing `TransportMemory` in `src/job_scraper/transport.py`: extend the `transport_memory` table from `(domain, rung, successes, failures, updated_at)` to also carry `consecutive_blocks` and `blocked_until`. On `Outcome.BLOCKED` at the top available rung, increment and set `blocked_until = now + min(300 * 2**consecutive_blocks, 86400)`; `HttpClient.get()` short-circuits to `Outcome.BLOCKED` without a request while `blocked_until` is in the future; one success resets it. Because it is in the same SQLite file as the rung memory, the state survives across runs for free — which is the requirement, since the current design re-discovers every block from scratch on every run. Read pybreaker for the state machine, then write the keyed version.

**Caveats.** Do not let the breaker swallow the distinction the new `Outcome` enum draws: open the circuit on BLOCKED and repeated ERROR/timeouts; never open it on NOT_FOUND or EMPTY, which are the site's honest answers and say nothing about whether you are welcome.


## curl_cffi — adopt

https://github.com/lexiforest/curl_cffi · MIT (verified in installed dist metadata: curl_cffi 0.15.0 -> MIT) · Python

**What.** A requests-API-compatible HTTP client built on a patched libcurl (curl-impersonate) that reproduces a real browser's TLS ClientHello (JA3/JA4), HTTP/2 SETTINGS and header-frame ordering. 37+ preset profiles (chrome/safari/firefox/edge, incl. chrome136+), HTTP/2 and HTTP/3, sync + AsyncSession, HTTP and SOCKS proxies.

**Fit.** This is the single highest-yield change available, and I measured it against HarvestKit's actual problem. On 30 EU company domains from this machine: plain requests reached 19/30; curl_cffi with impersonate='chrome' reached 24/30. Specifically recovered: www.sap.com 403->200 (59 KB), www.getyourguide.com 403->200 (295 KB), www.zalando.de ReadTimeout->200 (557 KB), www.personio.de 429->200, www.criteo.com 403->200. Those four 403s are pure ClientHello scoring - no header tuning on top of `requests` can ever reach them, because the block happens before a single header is parsed. 63% -> 80% domain coverage for one dependency, no browser, no display, no extra RAM. For HarvestKit's actual lead pages this is usually sufficient: 6 of 8 German/Swiss/French Impressum and imprint pages I fetched returned 2-7.6 KB of full static text over this rung, because a German Impressum is legally mandated server-rendered text.

**Maintenance.** Very much alive. 0.16.3 released 2 Sep 2026, 0.16.2 on 25 Aug, 0.16.1 on 21 Aug, 0.16.0 on 1 Aug 2026 - roughly weekly. ~6.5k stars. Python 3.10-3.14 incl. free-threading builds.

**Safety.** Lowest risk of anything in this list. Pure pip wheel, ~6 MB, no binary downloaded at runtime, no browser, no display, no sandbox flags to weaken. Pre-built wheels for Windows x86-64 and ARM64, so no compiler on employee laptops. It is a statically linked fork of libcurl/BoringSSL, which means CVE exposure tracks the pinned fork rather than the OS curl - pin an exact version and treat upgrades as security updates, but the attack surface (an HTTP client parsing bytes) is tiny next to a browser engine. Data exposure: none beyond what requests already does; it does not phone home and adds no third-party endpoint. Note the version installed on this box is 0.15.0, already four minors behind.

**Integration.** Already landing: a sibling agent has created src/job_scraper/transport.py with ImpersonateTransport at rung 1 (transport.py:446) and it is wired into HttpClient via build_ladder() at http.py:406 and self._ladder.fetch() at http.py:570. What is missing: (a) curl_cffi is not in pyproject.toml or requirements.txt, so on an employee laptop the rung silently logs 'not installed' and disables itself - add `curl_cffi>=0.16.3` to both; (b) pick the impersonate profile from the same identity_for(host, salt=proxy_url) object that picks the UA, or the TLS profile and the UA string will disagree (a Safari UA over a Chrome ClientHello is a harder tell than either alone); (c) curl_cffi has no urllib3 Retry adapter, so the retry/429 logic must live in the ladder, not the session - which is where http.py already puts it, so this is fine as built.

**Caveats.** Does not execute JavaScript, so it cannot serve an SPA shell or clear a JS challenge. It also did NOT recover the hard tail: 8 different impersonate targets (chrome, chrome124, chrome131, chrome133a, chrome136, safari18_0, firefox135, edge101) all got an identical 403 from www.hellofresh.de, because that is a Cloudflare managed challenge (cf-mitigated: challenge), not a TLS wall.


## Patchright (patchright-python) — adopt

https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python · Apache-2.0 (verified in installed dist metadata: patchright 1.58.2 -> Apache-2.0) · Python

**What.** A drop-in fork of Playwright with the automation tells removed at the driver level: it never issues Runtime.enable (it evaluates in isolated ExecutionContexts instead), disables Console.enable, strips --enable-automation and adds --disable-blink-features=AutomationControlled to the default args, and removes the injected-binding and sourceURL leaks. Same API - `from patchright.sync_api import sync_playwright` replaces `from playwright.sync_api import sync_playwright` and nothing else changes.

**Fit.** HarvestKit already has ~200 lines of Playwright-shaped code in src/job_scraper/crawl.py (cookie-banner dismissal, JSON-LD JobPosting wait, 'Show more' expansion) plus general_scraper/main.py. Patchright is the only option on this list that keeps all of it - it is an import swap. I verified locally that it works and that navigator.webdriver reads False under it, where plain Playwright reads True. Runtime.enable is the signal the 2026 literature is unanimous about: it is emitted during the CDP handshake, before any page script runs, so no JS-injection stealth plugin can reach it. Of the independent benchmarks, techinz/browsers-benchmark scores Patchright at 100% bypass across its nine-vendor matrix; ianlpaterson's 651-verdict run scores it 25 OK / 3 gated / 3 blocked vs vanilla Playwright's 24 / 2 / 5. Treat both as noisy, but the direction is consistent and the migration cost is near zero.

**Maintenance.** Actively maintained and tracking upstream closely: 1.63.0 released 20 Sep 2026 (yesterday), 1.62.3 on 2 Sep, 1.62.2 on 29 Aug, 1.62.1 on 17 Aug. ~1.5k stars. Python 3.10-3.14. Version numbers track the Playwright release they fork.

**Safety.** Moderate, and the moderation is about Chromium, not about Patchright. It downloads its own patched Playwright driver and Chromium build (`patchright install chromium`, ~400-500 MB, from the project's release artefacts rather than Microsoft's CDN) - so an employee laptop gains a third-party-built browser binary that updates on the maintainer's cadence, outside your EDR and OS patch cycle, pointed at attacker-controlled scraped HTML. Mitigate by using channel='chrome' so it drives the machine's already-managed Google Chrome rather than a downloaded Chromium; that is also what the project recommends for evasion. Apache-2.0 is clean against HarvestKit's proprietary LICENCE. One real caveat: it disables the Console API, so page console output is unavailable for debugging.

**Integration.** transport.py:591 already prefers patchright over playwright via _driver_module(). Three concrete fixes to what is there now, all evidenced by measurements on this box: (1) transport.py:610 calls chromium.launch(headless=self.headless) with headless defaulting True and falls back to playwright.sync_api if patchright is absent - on the installed Playwright 1.58.0 that launches chromium_headless_shell-1243, and I captured its real wire headers to siemens.com: `sec-ch-ua: "Not:A-Brand";v="99", "HeadlessChrome";v="145", "Chromium";v="145"`. The literal string HeadlessChrome is in a client hint on every request. Pass channel='chromium' (new headless, full binary) or channel='chrome' and never allow the headless shell. (2) No timezone_id is set, so Intl.DateTimeFormat().resolvedOptions().timeZone reads Asia/Calcutta on this machine - through a German proxy that is an instant geo contradiction. Map country_for_host -> timezone the same way accept_language_for already maps country -> language. (3) transport.py:652 sets locale=accept_language.split(',')[0] AND extra_http_headers['Accept-Language']; I measured that Playwright's locale wins and the wire header collapses to a bare `accept-language: en-US` with no q-values, which no real Chrome sends. Set the full list via the --accept-lang launch arg, or drop extra_http_headers and let locale generate it. Add `patchright>=1.63.0` to pyproject.toml; it is currently installed on this box but declared nowhere.

**Caveats.** Chromium only - no Firefox, no WebKit. It is still a patch-based approach, so it is on the treadmill. And it is not magic: Patchright headless AND Patchright with channel='chrome', headless=False both failed to clear www.hellofresh.de from this machine's IP (403 'Nur einen Moment...' after 12 s).


## BrowserForge — trial

https://github.com/daijro/browserforge · Apache-2.0 (verified in installed dist metadata: browserforge 1.2.4 -> Apache-2.0) · Python

**What.** Generates mutually consistent browser header sets and fingerprints from a Bayesian model trained on real-world traffic - so the User-Agent, Sec-CH-UA brand list, Sec-CH-UA-Platform, Accept, Accept-Language, Accept-Encoding and Sec-Fetch-* all belong to the same plausible browser, in the right order.

**Fit.** HarvestKit's new src/job_scraper/identity.py already does a hand-rolled version of this (identity_for, headers_for, country_for_host) and its docstring at http.py:503 shows the team already got burned by exactly the failure BrowserForge exists to prevent - a Safari UA arriving with a Chrome Sec-CH-UA. That is the right instinct but it is a model that needs maintaining as browsers ship; BrowserForge maintains it for you. It also pairs with curl_cffi: the impersonate profile fixes the ClientHello, BrowserForge fixes the header set above it, and consistency between the two layers is what modern scoring actually checks.

**Maintenance.** Same author as Camoufox (daijro), 1.2.4 current, Apache-2.0. Lower release cadence than curl_cffi or Patchright - it is a data/model package rather than a protocol implementation, so that is less alarming, but check the header dataset's freshness before relying on it for current Chrome versions.

**Safety.** Low risk. Pure Python plus a bundled dataset; it downloads header/fingerprint model data on first use (small, from the project's GitHub release artefacts). No browser, no native code, no network egress at scrape time. Already present on this machine as a Camoufox dependency.

**Integration.** Optional refinement rather than a new tier. In src/job_scraper/identity.py, replace the hand-maintained IDENTITY_POOL with BrowserForge's HeaderGenerator seeded deterministically per (host, proxy) so identity_for(host, salt=proxy_url) keeps its current stability guarantee - the existing call sites in http.py (lines 462, 514, 568, 633, 654, 712) do not change. Keep the existing referer logic; BrowserForge does not know about your crawl graph.

**Caveats.** Only worth doing after the ladder itself is correct. It fixes header self-consistency, which is a second-order signal next to TLS fingerprint and CDP artefacts.


## Camoufox — trial

https://github.com/daijro/camoufox · Python package MIT (verified: installed camoufox 0.4.11 -> MIT); the browser build is a Firefox derivative under MPL-2.0 · Python (drives a custom Firefox build via a Playwright-shaped API over Juggler)

**What.** A Firefox fork that spoofs fingerprint surfaces in C++ inside the engine rather than by injecting JS into the page: navigator properties, screen/window geometry, WebGL vendor/renderer and parameters, AudioContext, hardwareConcurrency, device voices, WebRTC IP, geolocation, timezone and locale. Because the patches are below the JS boundary there is no overridden-getter to detect, and because it is Firefox there is no CDP at all - the entire automation-protocol fingerprinting layer that catches Playwright forks does not apply.

**Fit.** It is the strongest open-source answer to deep fingerprinting (canvas/audio/WebGL/font enumeration - the PerimeterX and DataDome style of scoring), and its geoip extra aligns timezone, locale and WebRTC to the proxy's exit IP automatically, which is exactly the coherence problem HarvestKit has when an Indian-TZ laptop uses a German proxy. Worth keeping as an escape hatch for the specific domains Patchright cannot clear.

**Maintenance.** Active again after an acknowledged year-long gap: 0.5.6 released 6 Sep 2026 on Firefox base 152.0.4-beta.30 (current Firefox is 152.0.6, so an up-to-date Camoufox tracks upstream closely), 0.5.5 on 18 Aug, 0.5.4 on 16 Jul. ~12k stars. The maintainer still labels it 'may not be suitable for a production environment'.

**Safety.** The highest-risk item I would still recommend, and the risk is concrete rather than theoretical. It downloads and runs a third-party-built Firefox binary. The build installed on this machine is 135.0.1-beta.24 with Python package 0.4.11 - that is seventeen major Firefox versions behind current, i.e. an unpatched browser engine being pointed at attacker-controlled scraped HTML on an employee endpoint. That is a real endpoint-compromise path, not a compliance nit. If you deploy it: pin >=0.5.6, treat every Camoufox release as a security update, and keep it off laptops that hold anything sensitive. Footprint is also severe: the Camoufox install on this box measures 959 MB on disk. MPL-2.0 is file-level copyleft - fine for a proprietary product as long as you do not modify the browser's own source files.

**Integration.** Add as an optional rung 3 in src/job_scraper/transport.py, behind an availability check exactly like BrowserTransport.available at transport.py:579, so a laptop without it degrades instead of crashing. Camoufox's sync API is Playwright-shaped (`with Camoufox(headless=True, humanize=True, locale='de-DE', geoip=True) as browser`), so the body of BrowserTransport.fetch (transport.py:623-688) can be reused nearly verbatim; only _ensure_browser changes. Gate it to domains where TransportMemory (transport.py:231) records rung 2 as having failed, so it is paid for by the handful of domains that need it, never by the run.

**Caveats.** It is slow and it is not sufficient on its own. I ran Camoufox headless with humanize=True and locale='de-DE' against the three hardest EU targets from this machine and all three failed: hellofresh.de 403 'Nur einen Moment...', doctolib.fr 403 'Nur einen Moment...', blablacar.fr 403 (DataDome). Its own no-CDP architecture also means it cannot reuse HarvestKit's existing Playwright page code without an adapter for anything beyond goto/content.


## nodriver — assess

https://github.com/ultrafunkamsterdam/nodriver · AGPL-3.0 - this is the problem, see safety_assessment · Python (async)

**What.** Drives Chrome directly over CDP with no chromedriver, no Selenium and no Playwright shim. Because there is no middleware in the control plane, there is no Playwright startup sequence to fingerprint - which is the layer the benchmarks identify as decisive.

**Fit.** On the architecture argument it is the strongest Chromium-side option: ianlpaterson's 651-verdict benchmark puts it alone at 28 OK / 3 gated / 0 blocked, passing four targets (canadianinsider, medium, glassdoor, google-search) that every Playwright fork including Patchright failed. If HarvestKit's hard tail turns out to be automation-protocol fingerprinting rather than IP reputation, this is the tool that fixes it.

**Maintenance.** 0.50.3 released 13 May 2026, ~4.8k stars, Python >=3.9. Slower cadence than Patchright and the maintainer runs a closed contribution process, which is why the Zendriver fork exists.

**Safety.** The licence is the blocker, not the code. HarvestKit's LICENSE was changed to proprietary on 2026-09-21 (commits 6f59aef, 8c7eb71) and the grant contemplates installing the Software on machines authorised persons control. AGPL-3.0 is the most aggressive copyleft in common use: distribution obligations under section 5, plus section 13's network-interaction clause. Whether handing a proprietary CLI to employees constitutes conveying is a question for your counsel, not for me or for another agent - but it is a question you do not have to ask at all if you pick Patchright (Apache-2.0) instead. Technically the tool is fine: no bundled binary beyond whatever Chrome you point it at.

**Integration.** Do not integrate without a licence decision. If counsel clears it, it would be a fourth Transport subclass in src/job_scraper/transport.py; its API is async-only and object-model-different from Playwright, so unlike Patchright it cannot reuse BrowserTransport.fetch or anything in src/job_scraper/crawl.py - budget an adapter, and note HarvestKit's HttpClient and its ThreadPoolExecutor callers are synchronous, so you would need to run its event loop on a dedicated thread.

**Caveats.** Its own docs say to use Xvfb on a display-less host, so the best results assume a display - fine on Windows employee laptops, awkward in a Linux container.


## Zendriver — assess

https://github.com/stephanlensky/zendriver · AGPL-3.0 (inherited from nodriver) - same blocker · Python (async)

**What.** A community fork of nodriver created to land bugfixes the upstream maintainer had not merged and to accept outside contributions. Same CDP-direct, no-WebDriver architecture.

**Fit.** Only relevant as the better-maintained way to get nodriver's architecture. 0.16.0 released 16 Aug 2026, ruff/mypy in CI, Python 3.10-3.13, open contribution process. If you ever clear the licence question, take this rather than upstream.

**Maintenance.** More active than upstream nodriver, regular releases through 2025-2026, one primary maintainer (slensky) - bus-factor 1.

**Safety.** Identical AGPL-3.0 entanglement question as nodriver against HarvestKit's new proprietary licence, and it inherits it by being a fork, so there is no way to take the fixes without the licence. Single-maintainer fork of a single-maintainer project is a supply-chain concentration worth noting for something you would install on employee endpoints.

**Integration.** Same as nodriver: a rung-3 Transport subclass in src/job_scraper/transport.py plus a sync/async bridge, only after a licence decision. Not worth designing for until then.

**Caveats.** Everything true of nodriver is true here, plus fork risk.


## SeleniumBase (UC Mode / CDP Mode) — assess

https://github.com/seleniumbase/SeleniumBase · MIT for the framework - but verify the licence of the vendored undetected-chromedriver-derived code in seleniumbase/undetected/ before shipping, since upstream UC is GPL-3.0 · Python

**What.** A full test/automation framework whose UC Mode drives a patched Chromedriver and whose newer CDP Mode (sb_cdp) drives Chrome over raw CDP with no WebDriver, including helpers for clicking Cloudflare Turnstile checkboxes and human-like interaction.

**Fit.** CDP Mode is architecturally the same bet as nodriver but under an MIT-licensed umbrella, and it is the most actively maintained thing in this category (~13k stars). Its built-in --xvfb flag and headless1/headless2 options make the display question explicit rather than folklore.

**Maintenance.** Very active, frequent releases, large user base, supports Linux/macOS/Windows explicitly.

**Safety.** Framework weight is the main cost: adopting it pulls Selenium, a webdriver manager and a test-runner opinion into a codebase that currently has none of that, for a scraper that does not need a test framework. The licence question on the vendored UC code is the one thing to check with counsel - the MIT badge on the repo does not automatically cover code derived from a GPL-3.0 project. No telemetry concerns found.

**Integration.** I would not adopt the framework. If you want its ideas, take two specific ones into src/job_scraper/transport.py: (1) its explicit headless1/headless2/xvfb distinction, which is precisely the headless-shell trap BrowserTransport currently falls into at transport.py:610; (2) its Turnstile-checkbox click helper, as a post-load step in BrowserTransport.fetch before the classify() call at transport.py:672, for the cf-mitigated challenges that render an interactive checkbox rather than auto-clearing.

**Caveats.** Selenium-based UC Mode is a generation behind CDP Mode on evasion; if you evaluate it, evaluate CDP Mode.


## undetected-chromedriver — avoid

https://github.com/ultrafunkamsterdam/undetected-chromedriver · GPL-3.0 - incompatible in spirit with HarvestKit's new proprietary licence · Python

**What.** Patches Selenium's Chromedriver binary to strip the automation flags a stock driver exposes (cdc_ variables, --enable-automation, navigator.webdriver).

**Fit.** It does not, any more. Its own author wrote nodriver as its official successor; its own README says headless is officially unsupported ('headless is still WIP. Raising issues is needless'); and HarvestKit uses Playwright, not Selenium, so adopting it means introducing WebDriver - the exact layer every 2026 benchmark identifies as the most detectable control plane.

**Maintenance.** ~12.8k stars but lagging; the ecosystem has moved to nodriver and to Playwright forks. Scrapfly's 2026 comparison explicitly marks its maintenance as 'Lagging' and notes it is 'more detectable than CDP-direct tools'.

**Safety.** GPL-3.0 copyleft against a proprietary product. Also worth quoting to the team because it is the most honest line in this whole landscape and it matches what I measured: 'THIS PACKAGE DOES NOT, and i repeat DOES NOT hide your IP address, so when running from a datacenter (even smaller ones), chances are large you will not pass... if your ip reputation at home is low, you won't pass!'

**Integration.** None. If it appears anywhere in a proposal for this repo, that proposal has not accounted for the licence change in commit 6f59aef.

**Caveats.** Its README's IP-reputation warning is the single most useful sentence about it, and it argues against buying any stealth browser before fixing egress.


## rebrowser-patches / rebrowser-playwright — avoid

https://github.com/rebrowser/rebrowser-patches · Not clearly stated on the project page - treat unresolved licence as a blocker by itself · Node.js first (Puppeteer/Playwright); a Python Playwright wrapper exists

**What.** Patches the Runtime.enable leak and related CDP tells (execution-context-id acquisition via addBinding or isolated contexts, the //# sourceURL=pptr: marker, the utility world name) in Puppeteer and Playwright.

**Fit.** Historically important - it is the project that made Runtime.enable detection common knowledge, and the writeups are still the best explanation of why JS-injection stealth cannot fix it. But for a Python Playwright codebase in 2026 Patchright does the same job, does more of it, is Apache-2.0, and shipped a release yesterday.

**Maintenance.** Last releases April/May 2025 (Playwright 1.52.0, Puppeteer 24.8.1) - over a year stale against Playwright 1.58. ianlpaterson's benchmark lists rebrowser-playwright as unmaintained since Sept 2024 and scores it identically to vanilla Playwright (24 OK / 2 gated / 5 blocked), i.e. the patches no longer buy anything measurable.

**Safety.** Stale patches against a fast-moving Chromium are a liability rather than a defence, and the patching model (mutating an installed node_modules or driver tree in place) is awkward to audit on an employee laptop. Unstated licence compounds it.

**Integration.** None. Read https://rebrowser.net/blog for the Runtime.enable explanation, take Patchright for the implementation.

**Caveats.** Benchmarked at parity with unpatched Playwright, which is the clearest possible signal that a patch set has aged out.


## playwright-stealth / puppeteer-extra-plugin-stealth — avoid

https://github.com/berstend/puppeteer-extra/tree/master/packages/puppeteer-extra-plugin-stealth · MIT · Node.js (puppeteer-extra); Python ports exist

**What.** Injects JavaScript evasions into each page before site scripts run: sets navigator.webdriver false, fakes navigator.plugins and mimeTypes, reconstructs window.chrome, patches Notification.permission and navigator.permissions.query, spoofs the WebGL vendor string.

**Fit.** It does not, and it is important that nobody proposes it as the cheap fix. The core has had no meaningful update since March 2023; its evasions were written for the Chrome 109-112 detection era. Architecturally it cannot win: the Runtime.enable and CDP-handshake tells happen before any JS is injected into the page, and the TLS ClientHello happens before the browser has parsed a byte of HTML. The 2026 consensus is that it still clears dated or WAF-less sites and fails against Cloudflare Bot Fight Mode, Turnstile, DataDome, Akamai Bot Manager v4 and PerimeterX/HUMAN - which is precisely HarvestKit's EU blocked set (Cloudflare on hellofresh.de and doctolib.fr, DataDome on blablacar.fr).

**Maintenance.** Effectively unmaintained since 2023. Widely described as obsolete in 2026 writeups.

**Safety.** Low direct risk (it is just injected JS), but high indirect risk: it creates a false sense of coverage, and several of its overridden getters are themselves detectable - an over-patched fingerprint is more anomalous than an honest one.

**Integration.** None. If any HarvestKit config, doc or dependency references it, remove it so it is not mistaken for the browser tier's stealth story.

**Caveats.** The single sentence worth keeping from this entry: stealth applied from inside the page cannot fix anything that happens before the page exists.


## Botasaurus — avoid

https://github.com/omkarcloud/botasaurus · MIT · Python

**What.** An all-in-one scraping framework with its own anti-detect driver, decorator-based task model (@browser/@request/@task), built-in caching, parallelism, authenticated proxy rotation, a scraper UI builder and desktop-app packaging.

**Fit.** Poorly, for this repo specifically. HarvestKit already has its own mature versions of most of what Botasaurus provides: a SQLite response cache with TTL, a per-host token-bucket throttle, a proxy pool with health tracking and per-proxy cookie jars (src/job_scraper/http.py), robots enforcement (robots.py), SSRF guarding (src/leadgen/net_guard.py) and a 56-field shared schema. Adopting Botasaurus means either running two frameworks or rewriting the engine to live inside someone else's decorators - a very large change whose only new capability is the driver, which Patchright provides as a one-line import swap.

**Maintenance.** ~5.7k stars, active, MIT, Windows and headless supported (with the honest note that headless is more detectable).

**Safety.** No evidence of telemetry or phone-home found, and it publishes a SECURITY.md with a disclosure contact. The concerns are structural rather than malicious: it is a single-vendor framework with a large dependency surface and a bundled browser driver, it markets itself in absolute terms ('pass every bot test', 'undefeatable') which is not a claim any tool on this list can support in 2026, and it is notably absent from the independent benchmarks - neither Scrapfly's comparison nor ianlpaterson's 651-verdict run includes it. Adopting an unbenchmarked framework wholesale for endpoint deployment is a larger bet than adopting a benchmarked 6 MB library.

**Integration.** None recommended. If you want one idea from it, take the humanised interaction helpers as an optional post-load step inside BrowserTransport.fetch in src/job_scraper/transport.py - not the framework.

**Caveats.** Judgement is about fit and blast radius, not quality; for a greenfield scraper it would be a reasonable pick.


## Arbetsförmedlingen JobTech JobSearch API (Sweden, Platsbanken) — adopt

https://jobsearch.api.jobtechdev.se/search — docs https://jobtechdev.se/ and https://data.arbetsformedlingen.se/dataservice/jobsearch/ · CC0 1.0 public-domain dedication. Commercial reuse and redistribution explicitly permitted, no attribution required, no API key, no registration, no contract. · HTTP/JSON (any); Python client trivial

**What.** Sweden's national vacancy board as a documented open-data search API. GET /search?q=<term>&limit=100 returns structured ads. Critically it returns an `application_contacts` array — the employer's own named recruiting contact with email and phone as structured JSON — plus `employer.organization_number`, `employer.name`, `employer.url`, `workplace_address`, and full `description`.

**Fit.** I measured this live during the research: q=utvecklare, limit=100 returned 633 total hits, and of the 100 sampled ads 27 carried a populated `application_contacts` entry with a real person's name and in most cases a working employer-domain email (e.g. 'Margareta Ehn Edvinsson / margareta.ehnedvinsson@herrljunga.se', 'Lina Lindström / lina.lindstrom@framtiden.com'). 99/100 carried `employer.organization_number` and 65/100 carried `employer.url`. That is a ~27% named-person-with-email yield for ONE HTTP request per 100 companies. The entire src/leadgen/person/cascade.py crawl — eight page fetches per company, through the 403 bot walls that are causing the lead famine — exists to produce exactly this, and here it arrives pre-parsed and lawful. `employer.url` also short-circuits src/leadgen/company/domain.py, which the codebase's own comments call the slowest and most lossy step. And `organization_number` is a clean join key into Bolagsverket/SCB for company enrichment. This is the single best yield-per-effort source found.

**Maintenance.** Live and healthy — verified 2026-09-21, HTTP 200, 12 ms result time. Run by JobTech Dev, a permanent development unit inside Arbetsförmedlingen (the Swedish state employment agency). A companion historical.api.jobtechdev.se also answered 200 with 163,231 archived ads, useful for backfill.

**Safety.** No supply-chain risk: it is a plain HTTPS GET, no SDK, no dependency, nothing to install on an employee laptop. Data-exposure risk is minimal in the other direction too — no API key means no credential to leak from a laptop. GDPR note: `application_contacts` IS personal data. It is lawfully published by the employer for the purpose of being contacted about that role, which makes a legitimate-interest basis defensible for role-relevant outreach, but it does NOT exempt you from the GDPR Art. 14 duty to notify each person within one month, naming the source. Store the source URL per contact (the pipeline's PersonHit.source_url already does this) so that notice can name it.

**Integration.** New module src/leadgen/seed/jobtech.py modelled exactly on src/leadgen/seed/arbeitnow.py: `def fetch(http, *, max_pages=20, ...) -> list[JobListing]` using `http.get_json(...)` (src/job_scraper/http.py:679). Map hit['employer']['name'] -> JobListing.company, hit['employer']['url'] -> the website field so seed/jobboard.py:companies_from_listings can skip domain guessing, hit['description']['text'] -> description, workplace_address -> city/country. Then — the valuable half — map `application_contacts` straight into PersonHit objects (src/leadgen/person/hit.py) and attach them to CompanyContext.ad_contacts, which src/leadgen/pipeline.py already merges at the 'ad_contacts' funnel counter without any extra requests. Wire it in cli.py alongside the existing `from .seed.arbeitnow import fetch as fetch_arbeitnow` (line 20) and call it next to line 372 behind a `--jobtech-pages` flag.


## Bundesagentur für Arbeit Jobsuche API v6 (Germany) — trial

https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs — community spec at https://github.com/bundesAPI/jobsuche-api · No published licence. This is the Bundesagentur's own app backend, documented by the community bundesAPI project, not an officially licensed open-data product. Treat the terms as unsettled. · HTTP/JSON

**What.** Search across Germany's largest job board. `GET /pc/v6/jobs?was=<keyword>&wo=<city>&size=100&page=N` with header `X-API-Key: jobboerse-jobsuche`. Returns `firma` (employer name), `stellenlokationen` (postcode/city/region + lat/lon), `externeURL`, `referenznummer`. A second call to /pc/v4/jobdetails/{base64(referenznummer)} returns `stellenangebotsBeschreibung`, the full German ad text.

**Fit.** configs/leads/eu-it.yaml currently carries a long comment asserting this source is unreachable and sets `targets: []` as a result — so a Europe run consults nothing in Germany. That premise is out of date. I verified live on 2026-09-21: /pc/v4/jobs and /pc/v5/jobs are indeed dead (403), but **/pc/v6/jobs returns HTTP 200** with `X-API-Key: jobboerse-jobsuche` (the header name is case-insensitive; without any key it is 403). Measured: 'Softwareentwickler' returns maxErgebnisse=3597 nationally; across 400 sampled rows, 100% carried `firma` and 73% carried `externeURL`, giving 259 distinct employers per 400 rows. /pc/v4/jobdetails also returns 200 and yields the full employer-authored German ad body — which is precisely the text src/leadgen/person/jobad.py parses for 'Ihre Ansprechpartnerin: …', the one convention that reliably produces HR contacts with published addresses. This is the largest untapped German seed available.

**Maintenance.** Endpoint live and returning fresh ads (publication dates of 2026-09-15 observed). The bundesAPI/jobsuche-api repo is actively maintained but is reverse-engineered documentation, so version paths move — v4 and v5 have already been retired. Pin v6, and treat a sudden 403 as 'the version rotated', not 'we are blocked'.

**Safety.** No supply-chain risk (plain HTTPS, no SDK). The real risk is policy, and you should see it clearly before switching this on: https://rest.arbeitsagentur.de/robots.txt returns HTTP 403, which RFC 9309 defines as the whole host being disallowed. HarvestKit's client is correct to refuse it today — that is why the config says the source yields nothing. Using it means deciding to override a host's robots signal on an undocumented endpoint. That is a business and legal call for the operator, not something to quietly enable. Note the contrast: www.arbeitsagentur.de/robots.txt returns 200 with `Disallow:` (i.e. everything allowed); only the API host serves the 403.

**Integration.** The mechanism already exists and does not require the blunt `obey_robots: false` the config suggests. src/leadgen/cli.py:297 calls `_build_http(config, robots_exempt_hosts=exempt)` and src/job_scraper/http.py:481 checks that set before consulting robots. Add 'rest.arbeitsagentur.de' to that tuple for this source only. Then write src/leadgen/seed/arbeitsagentur.py with `fetch(http, keywords, *, size=100, max_pages=N)` calling `http.get_json(url, headers={'X-API-Key': 'jobboerse-jobsuche'})`, mapping `firma` -> company, `stellenlokationen[0].adresse` -> city/country, `externeURL` -> apply_url. Fetch jobdetails only for rows whose `firma` survives dedupe, to keep it to roughly one extra request per company. Also fix the stale claim in the configs/leads/eu-it.yaml header comment, which currently misinforms the operator.


## EURES — European Job Mobility Portal search API — adopt

POST https://europa.eu/eures/api/jv-searchengine/public/jv-search/search (portal: https://europa.eu/eures/portal/jv-se/home) · No separate data licence published; governed by the EURES portal terms of use. It is a European Commission / European Labour Authority public service. Unofficial in the sense that no stability or support is guaranteed. · HTTP/JSON (POST body)

**What.** Single pan-European vacancy search across 31 countries. A minimal POST body — `{"resultsPerPage":50,"page":1,"locationCodes":["DE"]}` — is enough; no auth, no key, no cookie. Each row returns `title`, `description`, `employer` {name, legalID, website, sectorCodes}, `locationMap` (ISO country -> NUTS region), and ESCO/ISCO `jobCategoriesCodes`.

**Fit.** This is the geography-first seed configs/leads/eu-it.yaml says the pipeline needs, and it covers the whole continent in one endpoint instead of one adapter per country. Verified live 2026-09-21: 2,071,409 total vacancies; DE 669,174, FR 471,469, NL 235,404, SE 132,040, CH 75,781, AT 64,972, NO 13,987, DK 1,401. The ESCO `jobCategoriesCodes` give a language-independent IT filter, which is strictly better than the keyword-translation approach in configs/leads/keywords-multilingual.txt — 'Softwareentwickler' and 'utvecklare' and 'ontwikkelaar' all collapse to the same ESCO URI. Employer website fill rate is honestly uneven and you should plan around it rather than assume it: measured on 300 sampled rows per country for q=software, DE was 55% populated, AT 2%, CH 0%, NL 0%. So treat `employer.website` as a welcome shortcut in DE and expect to fall back to src/leadgen/company/domain.py elsewhere. Also filter the website field — 'https://www.xing.com' appeared repeatedly as a junk value in the DE sample.

**Maintenance.** Live, European Commission operated, actively serving the current EURES portal SPA. The older /eures/eures-apps/searchengine/… path is dead (404) — that is likely what any previous attempt hit; the /eures/api/jv-searchengine/… path is the current one.

**Safety.** No supply-chain risk. No credential to leak from an employee laptop. Personal data exposure is low at the seed stage: rows carry employer identity, not named individuals, so the GDPR surface only opens when you crawl onward. Two hard operational limits found by probing, which you must design around or the source will silently truncate: `resultsPerPage` above 50 is rejected ('Too many results per page'), and paging past page 200 is rejected ('Too many results') — i.e. a hard 10,000-row ceiling per query. Shard by country × ESCO code × publication window to go deeper. Also note `sortSearch: "BY_PUBLICATION_DESC"` is INVALID and returns HTTP 400; the accepted value is `"MOST_RECENT"`.

**Integration.** src/leadgen/seed/eures.py using the existing `http.post_json(url, payload)` (src/job_scraper/http.py:698) — which already sets Sec-Fetch-Mode: cors and the JSON content type, so it fits without changes. Signature `fetch(http, country_codes, esco_uris, *, max_pages=200)`, looping page 1..200 at resultsPerPage=50 per (country, esco) shard. Map employer.name -> JobListing.company, locationMap key -> country (feeding src/leadgen/geo.py country filtering directly, no string parsing), employer.website -> website. Register it in cli.py next to `search_all` (line 397) behind `--eures-countries`, and let the results flow into `companies_from_listings` at line 410 exactly as the Workable/SmartRecruiters seed does.


## Brønnøysund Enhetsregisteret — roller (Norwegian officer register) — adopt

https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller — docs https://data.brreg.no/enhetsregisteret/api/docs/index.html · NLOD 2.0 (Norsk lisens for offentlige data), https://data.norge.no/nlod/no/2.0 — free reuse including commercial, attribution required. Free of charge, no key, no registration. · HTTP/JSON

**What.** Returns every registered role for a Norwegian legal entity, grouped by role type: DAGL (daglig leder / managing director), STYR (styre / board, with styrets leder flagged), REVI (auditor), and others. Each role carries the person's `fornavn`/`etternavn`. A companion /api/enheter?navn=<name> search resolves company name -> organisasjonsnummer, and /api/enheter/lastned offers full bulk download.

**Fit.** HarvestKit already treats Norway as a first-class region — four configs under configs/regions/ (norway-big, norway-it, norway-top5, germany) and three Norwegian adapters (finn.py, nav.py, karrierestart.py) — yet src/leadgen/person/register.py implements the commercial register for Switzerland only. Norway has the same public-record officer data, and unlike Zefix it needs no credentials at all. I verified live: /enheter/982463718/roller (Telenor ASA) returned 200 and named 'Benedicte Fasmer' as Daglig leder and 'Jens Petter Olsen' as Styrets leder plus four named Styremedlem. That is exactly the executive tier src/leadgen/person/roles.py targets, delivered in two requests for companies whose own website names nobody — the ~20% gap register.py was written to close.

**Maintenance.** Live, verified 200 on 2026-09-21. Operated by the Brønnøysund Register Centre, a Norwegian government agency; the open-data programme is long-standing and stable. Contact opendata@brreg.no. An authenticated tier (/autorisert-api/, Maskinporten OAuth) exists for personal ID numbers — you do not want that and should not request it.

**Safety.** No supply-chain risk, no credential on the laptop. But be careful with one field: the roller response includes each officer's `fodselsdato` (date of birth) — I saw '1965-11-06' for the Telenor daglig leder. Date of birth is personal data with no business purpose in a sales lead and materially raises the sensitivity of your CSV if it leaks. Drop it at parse time rather than storing it. Beyond that this is deliberately-published public-record data about people in their professional capacity, which is the most defensible category of B2B personal data under GDPR — but the Art. 14 notice duty still applies, and NLOD requires you to credit Brønnøysundregistrene as the source.

**Integration.** Generalise the existing src/leadgen/person/register.py. It is currently Swiss-only with a module-level `is_configured()` credential gate; refactor to a small registry keyed by country: `{'CH': zefix_lookup, 'NO': brreg_lookup}`. brreg_lookup(name, http) does `http.get_json('https://data.brreg.no/enhetsregisteret/api/enheter?navn=' + quote(name))`, takes the exact-name match, then `http.get_json(f'.../enheter/{orgnr}/roller')`, and emits PersonHit(name=f'{fornavn} {etternavn}', role=<beskrivelse>, source_url=<brreg detail URL>) — dropping fodselsdato. src/leadgen/pipeline.py already calls `people_from_register(company.name, http)` only when `register and not hits and company.name` (the 'register_people' funnel counter), so the call site needs no change beyond passing company.country through. Unlike Zefix this needs no ZEFIX_USER/ZEFIX_PASSWORD, so --register stops being inert for Norwegian runs.


## Annuaire des Entreprises — recherche-entreprises.api.gouv.fr (France) — adopt

https://recherche-entreprises.api.gouv.fr/search?q=<name> — docs https://annuaire-entreprises.data.gouv.fr/donnees/api-entreprises · Licence Ouverte / Open Licence 2.0 (Etalab). Free reuse including commercial, attribution to the source. No API key, no registration, no quota gate observed. · HTTP/JSON

**What.** DINUM's public company search, fusing INSEE Sirene with INPI's RNE. One GET returns `dirigeants` — an array of officers with `nom`, `prenoms`, `qualite` (Directeur Général, Président, Administrateur, Gérant), `type_dirigeant` — plus `siren`, `tva`, `siege` (full address with lat/lon), `activite_principale` (NAF), `tranche_effectif_salarie` (headcount band), `finances`, and `etat_administratif`.

**Fit.** France is in EU_COUNTRIES in src/leadgen/geo.py and EURES shows 471,469 French vacancies, but HarvestKit has no French person source at all, and French company sites are exactly the kind that serve a bot wall. This closes that with zero credentials. Verified live: q=capgemini returned 200 with named dirigeants including 'EZZAT, AIMAN — Directeur Général' and 'CHÉRY, JEAN-MARC — Administrateur'. Note this supersedes the INSEE Sirene API you might otherwise reach for: Sirene requires a portail-api.insee.fr account AND contains no dirigeants at all (officers live in INPI's separate RNE) — I confirmed Sirene returns 401 without a key. This endpoint gives you the merged result for free. `tranche_effectif_salarie` is also a genuinely useful qualification filter that the current pipeline has no equivalent of.

**Maintenance.** Live, verified 200 on 2026-09-21, data freshness fields `date_mise_a_jour_insee` and `date_mise_a_jour_rne` both present. Operated by DINUM (French government digital service); the annuaire-entreprises product is well funded and heavily used.

**Safety.** No supply-chain risk, no credentials. One field to drop: `dirigeants[].date_de_naissance` (month precision, e.g. '1961-05') — same reasoning as the Norwegian birth dates, strip it. Also respect `statut_diffusion`: French law lets a natural person opt out of Sirene diffusion, and honouring that flag is both a legal and an ethical requirement — never emit a lead for a record whose diffusion is restricted. The API is documented at roughly 7 requests/second; pace with the existing per-host throttle rather than bursting.

**Integration.** Same registry as the Norwegian one above: add `'FR': recherche_entreprises_lookup` to the refactored src/leadgen/person/register.py. `http.get_json('https://recherche-entreprises.api.gouv.fr/search?q=' + quote(name) + '&per_page=1')`, take results[0], skip if `statut_diffusion` is restricted, then emit one PersonHit per dirigeant with `type_dirigeant == 'personne physique'` (skip corporate officers), name=f"{prenoms.title()} {nom.title()}", role=qualite, source_url=f'https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}'. `qualite` maps cleanly onto the executive family in src/leadgen/person/roles.py. Optionally also use `siege.adresse` + `tranche_effectif_salarie` to enrich CompanyContext in src/leadgen/assemble.py.


## UK Companies House public data API — trial

https://api.company-information.service.gov.uk — register at https://developer.company-information.service.gov.uk/ · Crown copyright, published under the Open Government Licence v3.0. Free of charge, commercial reuse permitted with attribution. · HTTP/JSON, HTTP Basic auth with the API key as username

**What.** The live UK register as JSON: /search/companies, /company/{number}, /company/{number}/officers (named directors and secretaries with appointment dates and roles), /company/{number}/persons-with-significant-control, filing history, registered address.

**Fit.** src/leadgen/geo.py's EFTA_AND_UK set already puts the UK in scope for `--countries europe`, and Arbeitnow launched UK coverage in 2026 — so UK companies are already entering the funnel with no officer source behind them. The /officers endpoint is a direct analogue of the Zefix and BRREG paths, and the 600-requests-per-5-minutes budget (2 req/s sustained) is generous enough to serve as a primary rather than last-resort source for UK rows.

**Maintenance.** Live and long-established. Verified the auth boundary on 2026-09-21: an unauthenticated call returns a clean 401 'Empty Authorization header', so the key requirement is real. Rate-limit increases above 600/5min are granted case-by-case and remain free.

**Safety.** Low supply-chain risk (plain REST, no SDK needed). The meaningful risk here is the credential: this is the first source in this list that puts a secret on the machine, and the brief says HarvestKit gets deployed on employees' laptops. Do not ship the key in a config file — read it from an environment variable exactly as src/leadgen/person/register.py already does with ZEFIX_USER/ZEFIX_PASSWORD, and issue a per-operator key so one leaked laptop is revocable without stopping everyone. Officer data is public record about people acting in a professional capacity — the most defensible category — but Art. 14 notice still applies, and note that officers' residential addresses are suppressed by Companies House for good reason: never attempt to reconstruct them.

**Integration.** Add `'GB': companies_house_lookup` to the same country registry in src/leadgen/person/register.py, and mirror register.py's existing credential-gate pattern (USER_ENV/PASSWORD_ENV module constants plus `is_configured()`, so the source stays inert and logs a clear reason rather than silently yielding nothing — cli.py:~300 already has that warning path for Zefix). Auth is Basic with the key as username and an empty password, i.e. `http.get_json(url, headers={'Authorization': 'Basic ' + base64(key + ':')})` — the same base64 construction register.py already uses for Zefix. Pace to 2 req/s via the existing per-host throttle.


## GLEIF LEI data (Global Legal Entity Identifier Foundation) — trial

https://api.gleif.org/api/v1/lei-records — docs https://api.gleif.org/docs, bulk https://www.gleif.org/en/lei-data/gleif-golden-copy/download-the-golden-copy · CC0 1.0 — public domain. Free commercial use, no attribution required, no registration. · HTTP/JSON:API; also daily bulk Golden Copy / Concatenated files

**What.** Authoritative legal-entity reference data: canonical legal name, legal and headquarters address, jurisdiction, registration status, mapped identifiers (BIC, ISIN), and direct/ultimate parent relationships. Searchable by name, LEI, or BIC.

**Fit.** This is not a person source and should not be sold as one — it is the normalisation layer the pipeline currently lacks. src/leadgen/person/register.py's own docstring records the real failure it fixes: fuzzy name matching turned 'FREITAG' into 'FREITAGS AG' and matched 'Kistler' to a sole trader called Andy Kistler. Resolving a messy job-ad employer string to a canonical legal name and jurisdiction BEFORE hitting a register makes every downstream register lookup in this list more precise. The parent/child relationships also let you collapse 'Siemens Healthineers' and 'Siemens AG' into one account rather than billing them as two leads. Free, CC0, no key — near-zero cost to add.

**Maintenance.** Live, verified 200 on 2026-09-21 with a Golden Copy publish date of that same morning (2026-09-21T08:00:00Z), i.e. updated daily. GLEIF is a permanent not-for-profit established by the Financial Stability Board.

**Safety.** Essentially zero risk. CC0 means no licence entanglement, no attribution obligation, no share-alike. It contains no personal data at all, so it adds nothing to your GDPR surface — which is unusual and valuable in this list. No credential on the laptop. Documented rate limit is 60 requests/minute/user.

**Integration.** A small helper in src/leadgen/company/ (alongside domain.py), e.g. `canonical_name(raw_name, country, http) -> tuple[str, str] | None` calling `http.get_json('https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=' + quote(raw) + '&page[size]=5')` and filtering on entity.legalAddress.country to disambiguate. Call it from src/leadgen/assemble.py when building CompanyContext, so the normalised name is what reaches `people_from_register(company.name, http)` in pipeline.py. Given the 60/min ceiling and the fact that names repeat heavily across ads, gate it behind the existing SQLite response cache (cache_ttl_seconds is already 604800 in configs/leads/eu-it.yaml) — legal names change far more slowly than a week.


## Certificate Transparency logs via SSLMate Cert Spotter (domain discovery) — trial

https://api.certspotter.com/v1/issuances?domain=<d>&include_subdomains=true&expand=dns_names — docs https://sslmate.com/ct_search_api/ · Free tier usable without an account; higher volume requires a paid SSLMate plan. The underlying CT log data is public by design (RFC 6962). Check SSLMate's terms before high-volume commercial use. · HTTP/JSON

**What.** Returns every publicly logged TLS certificate for a domain and its subdomains, expanded to the DNS names each certificate covers. In practice this enumerates a company's real hostnames — including careers, jobs, hr, and corporate subdomains — plus every sibling brand domain the same company operates.

**Fit.** This speaks directly to the stated root cause. The investigator's probe found www.sap.com, www.hellofresh.de, www.getyourguide.com and www.zalando.de all serving 403 bot walls to a plain request — roughly 4 of 6 EU company domains. CT logs route around that entirely, because you are querying a public append-only log rather than the defended origin. Verified live on one of the exact blocked domains: hellofresh.de returned 100 certificates covering 145 distinct DNS names, and those names revealed the whole corporate family — factormeals.de, factormeals.ca, everyplate.com, chefsplate.ca — plus infrastructure subdomains. A careers or jobs subdomain surfaced this way is very often served by an ATS with no bot wall at all, which turns a hard 403 into an easy fetch. It also feeds src/leadgen/company/domain.py far better evidence than the TLD-guessing loop in seed/atsboards.py (DOMAIN_TLDS = com, de, ch, io, co …), which is explicitly a guess.

**Maintenance.** Live and fast, verified 200 on 2026-09-21. Prefer it over crt.sh: I probed crt.sh three times during this research and it returned 502 Bad Gateway every time, including the documented ?output=json form. crt.sh is the better-known name and the less reliable service.

**Safety.** Low risk. No credential needed for the free tier, so nothing to leak from a laptop. No personal data is involved at all — certificates name hosts, not humans — so this adds nothing to your GDPR surface. The real constraint is throughput, and it is tight: the response carried `X-Ratelimit-Limit: 10` with `X-Ratelimit-Remaining: 9`, i.e. about 10 queries per hour unauthenticated. That makes it a targeted fallback for companies the crawl has already failed on, not a bulk enumeration tool. An SSLMate account raises it.

**Integration.** Best placed as a recovery path rather than a seed. In src/leadgen/person/cascade.py, `resolve_people` currently gives up when `_harvest` on the candidate paths yields nothing; add a step before that return: if every fetch came back 403/blocked, call a new src/leadgen/company/ct.py `sibling_hosts(domain, http)` and retry the candidate paths from src/leadgen/person/paths.py against the most promising hostnames (prefer those matching ^(careers|jobs|hr|about|corporate)\.). Given the ~10/hour ceiling, guard it with a per-run counter and the existing SQLite cache, and only spend a call on a company that has already failed — which is exactly the population that matters here.


## Danish CVR (Det Centrale Virksomhedsregister) — via cvrapi.dk or the official distribution agreement — assess

https://cvrapi.dk/api?search=<name>&country=dk (free gateway) — official system-to-system: http://distribution.virk.dk/cvr-permanent/virksomhed/_search, apply via https://datacvr.virk.dk · CVR base data is Danish open public data, free to reuse commercially. The official distribution endpoint requires a signed system-to-system agreement with Erhvervsstyrelsen; cvrapi.dk is an independent free gateway with its own fair-use terms. · HTTP/JSON

**What.** Company lookup by name or CVR number returning legal name, CVR/VAT number, address, industry (NACE) code and description, employee count band, phone, email, status, start date, and production units. The official Elasticsearch distribution endpoint additionally exposes `deltagere` — participants, i.e. management, board and owners.

**Fit.** Denmark is in EU_COUNTRIES and EURES lists Danish vacancies, so Danish companies reach the funnel already. The free gateway gave me a usable company record immediately: Novo Nordisk A/S returned 200 with phone 44448888, NACE description, and 217 production units. But be clear-eyed about the officer question, because that is what actually matters for leads: the free gateway returned `owners: null` for an A/S, and the official distribution endpoint returned HTTP 401 Authorization Required when I probed it. So Denmark gives you good company enrichment for free and officer names only after paperwork.

**Maintenance.** cvrapi.dk live and healthy (verified 200, 80 KB response, 2026-09-21). The official virk distribution service is stable but gated. Denmark's register is genuinely one of the most open in Europe — the friction is procedural, not commercial.

**Safety.** Low supply-chain risk. Two cautions. First, cvrapi.dk is a third party sitting between you and the state register — if you depend on it for production volume you inherit its uptime and its fair-use limits, and you should identify yourself honestly in the User-Agent as its terms expect. Second, the record includes a company `email` field: for a sole trader (enkeltmandsvirksomhed) that address is often a natural person's, so treat CVR email as personal data in the small-company tail even though it is corporate for an A/S. The `protected` boolean in the response flags records with advertising protection (Robinsonliste-style) — honour it and exclude those from outreach entirely.

**Integration.** Company enrichment rather than a person source, so it belongs in src/leadgen/company/ next to domain.py, not in person/register.py. `def enrich_dk(name, http) -> dict | None` via `http.get_json('https://cvrapi.dk/api?search=' + quote(name) + '&country=dk')`, skipping any record where `protected` is true, and feeding phone/NACE/employee-band into CompanyContext in src/leadgen/assemble.py — the same slot the GLEIF and French enrichment would use. If the volume justifies the paperwork later, apply for the virk system-to-system agreement and swap the backend behind the same function signature to gain `deltagere` officer names.


## NAV Arbeidsplassen search API (Norway) — trial

https://arbeidsplassen.nav.no/stillinger/api/search?q=<term>&size=50 · NAV publishes its job-ad data as Norwegian open data (NLOD) via arbeidsplassen.nav.no. This particular search path is the site's own backend rather than the separately-documented public feed; treat terms as the site's. · HTTP/JSON (Elasticsearch-shaped response)

**What.** Returns Norwegian vacancies as JSON with `businessName`, `employer.name`, `title`, `locationList`, `occupationList`, `published`, `expires`, `uuid`, and a `properties` bag.

**Fit.** src/job_scraper/adapters/nav.py currently scrapes the HTML listing page and then regexes `/stillinger/stilling/([a-f0-9-]{20,})` out of the markup to find detail URLs. That is the fragile, block-prone path the whole brief is complaining about. The JSON endpoint behind the same page returns the same data structured — verified 200 on 2026-09-21, 214 hits for q=utvikler — and is immune to markup changes. Straight reliability upgrade to an adapter you already ship, at low effort. Be realistic about the ceiling though: I checked specifically and `contactList` was null on all 25 sampled rows and the `properties` bag carried no employerhomepage, so this gives you companies, not people. Pair it with the Brønnøysund roller lookup above, which is where Norwegian names actually come from.

**Maintenance.** Live and current. Note the separately-documented /public-feed/api/v1/ads path returned 404 in my probe, so target the /stillinger/api/search path. Because it is the site's own backend it can change shape without notice — keep the existing HTML adapter as a fallback rather than deleting it.

**Safety.** Low risk; no credential, no SDK, no personal data in the search response (which is also why it yields no contacts). As it is the site's own XHR backend rather than a published API, request it at a human pace via the existing per-host throttle and honour robots.txt as the client already does — there is no 403-robots complication here, unlike the Arbeitsagentur host.

**Integration.** Rewrite the body of `fetch_jobs` in src/job_scraper/adapters/nav.py to call `http.get_json(SEARCH_BASE + '/api/search', ...)` instead of fetching HTML and applying DETAIL_URL_RX (nav.py:26). Map `_source.businessName` or `_source.employer.name` -> JobListing.company, `_source.title` -> title, `_source.locationList` -> city/country, and build job_url from `_source.uuid` using the existing detail-URL template at nav.py:61. The adapter interface in src/job_scraper/adapters/base.py is unchanged, so configs/sites/nav.yaml and configs/regions/norway-*.yaml need no edits and the existing tests keep their contract.


## Bolagsverket — API för värdefulla datamängder (Sweden) — assess

https://bolagsverket.se/apierochoppnadata/hamtaforetagsinformation/vardefulladatamangder/apiforvardefulladatamangder.5513.html · Free of charge and with no contract requirement since February 2025, under the EU Open Data Directive's 'high-value datasets' regime. Registration/connection still required. · HTTP/JSON, plus full-dataset file download

**What.** Company information for Swedish registered organisations — names, addresses, legal form, status — jointly with SCB, including digitally filed annual reports and a downloadable full dataset.

**Fit.** It is the natural partner to the JobTech recommendation above: JobTech hands you `employer.organization_number` on 99% of Swedish ads, and this is the register that number resolves against, for free. Together they give Sweden a complete company + named-contact picture with no paid dependency. The 'high-value datasets' designation is the reason this became free, and it is worth knowing that the same EU directive is progressively forcing other national registers in the same direction — this is a category that gets cheaper over time, unlike the commercial aggregators.

**Maintenance.** Actively developed — Bolagsverket shipped version 4.6 of the company-information API in February 2026. My unauthenticated probe of the portal path returned 403, which is consistent with the documented need to connect first rather than with the service being down.

**Safety.** Low risk, and notably this dataset is about organisations rather than people, so it does not enlarge your GDPR exposure. Two things to keep separate: the free 'värdefulla datamängder' API and the older fee-based 'API för att hämta företagsinformation', which still charges a monthly transaction fee — make sure you connect to the free one. Any credential issued should live in an environment variable, not a config file, for the same laptop-deployment reason as Companies House.

**Integration.** Company enrichment, same slot as the Danish and French enrichment: a function in src/leadgen/company/ keyed by organisationsnummer rather than by fuzzy name — which is the whole point, since exact-key lookup avoids the 'FREITAG' -> 'FREITAGS AG' class of error documented in src/leadgen/person/register.py. Call it from src/leadgen/assemble.py when CompanyContext.country == 'SE' and the JobTech seed supplied an org number. Because there is a full-dataset download, the higher-leverage option is to fetch the bulk file periodically into a local SQLite table and join offline — zero per-lookup latency and no rate limit at all during a run.


## VIES — EU VAT number validation (European Commission) — assess

POST https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number · Free public service of the European Commission, fronting 27 national tax authority databases. No key, no registration. No SLA. · HTTP/JSON (REST) or SOAP

**What.** Confirms whether an EU VAT number is currently valid and, when the caller supplies their own requester VAT number, returns the registered trader name and address for consultation purposes.

**Fit.** Useful as a cheap liveness and identity check — a company whose VAT number no longer validates is a dead lead, and filtering those out raises list quality before you ever send anything. But set expectations low, because I tested it rather than assuming: without a `requesterMemberStateCode`/`requesterNumber` the response returns `"name": "---"` and `"address": "---"` — validity only, no company data. So it is a filter, not an enrichment source, unless HarvestKit's operator supplies their own VAT number on every call.

**Maintenance.** Live, verified 200 on 2026-09-21. But fragility showed up immediately in testing: the GET form returned `"userError": "MS_MAX_CONCURRENT_REQ"` on my very first sequential call. That is the documented global concurrency limit, shared across every caller in the world per member state — when Germany's backend is busy, your request fails regardless of how politely you are behaving. Any integration must treat MS_MAX_CONCURRENT_REQ as 'retry later', never as 'invalid'.

**Safety.** Low supply-chain risk, no credential (unless you supply your own VAT number, which is business data rather than a secret, though it does identify your company to the Commission on every call). No personal data for incorporated entities. One caveat for the small-company tail: a sole trader's VAT registration name is a natural person's name, so a validated 'company name' in that segment is personal data. The bigger practical risk is misreading the error surface — treating a MS_MAX_CONCURRENT_REQ as an invalid VAT number would silently and wrongly delete good leads, which is precisely the class of bug the RUNBOOK's 'anything other than 000 is fine' guidance already encourages.

**Integration.** A validation filter in src/leadgen/score/completeness.py or as a late gate in src/leadgen/export.py, not a seed. `http.post_json(VIES_URL, {'countryCode': cc, 'vatNumber': num})`, and treat the result as three-valued — valid / invalid / unknown — where `userError` of MS_MAX_CONCURRENT_REQ or any timeout maps to unknown and leaves the lead untouched. Never let unknown collapse into invalid. Source the VAT numbers from the enrichment sources above (`tva` from recherche-entreprises, `vat` from cvrapi.dk) rather than guessing them.


## Common Crawl (index + WARC archives) — assess

https://index.commoncrawl.org/collinfo.json then https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=*.example.de&output=json · Common Crawl Terms of Use — a limited, non-transferable licence. Commercial use is not prohibited outright but CC 'strongly recommends' legal advice before it. Crucially the terms forbid 'collecting or harvesting any personally identifiable information or personal information for use separately from the Crawled Content'. · HTTP/JSON (CDX index API) + WARC files on S3

**What.** A public archive of billions of crawled web pages with a queryable CDX index. You can ask which URLs exist under a domain, and fetch the archived HTML of any of them by byte range — without ever touching the live origin server.

**Fit.** The appeal is obvious given the 403 problem: an archived copy of www.zalando.de or www.hellofresh.de can be read even though the live site blocks you, and no proxy is involved because Common Crawl did the fetching. I verified the index is live and current — collinfo.json lists CC-MAIN-2026-39 covering 2026-09-04 onward, and a query for *.hellofresh.de returned an indexed 200 capture of the homepage from 2026-09-11. But I have to flag the licence honestly, because it cuts against the primary use case: extracting names and email addresses from archived pages for a lead list is exactly 'harvesting personally identifiable information for use separately from the Crawled Content', which the Terms of Use prohibit. That rules out the Impressum-mining application people reach for first.

**Maintenance.** Live, current, well funded, monthly crawls. No key required. Data is inherently 1–8 weeks stale, which for officer names is usually acceptable and for job ads is not.

**Safety.** No supply-chain risk and no credential. The risk here is licence compliance, and it is the reason for the split verdict: use Common Crawl for DOMAIN AND URL DISCOVERY — which pages exist, does this company have a /karriere or /impressum path, which subdomains are live — because that is structural metadata, not personal data, and stays inside the terms. Do NOT use it as the source you extract people from. Doing the discovery here and the person-extraction against the live site (or a lawful register from this list) keeps you compliant and is also better data. Also note the terms pass third-party rights through: the original site's own terms still apply to its content.

**Integration.** A discovery helper, not a fetch path. In src/leadgen/person/paths.py, `candidate_paths` currently proposes localised guesses (/impressum, /ueber-uns, /team …). Add an optional `known_paths(domain, http)` that queries the CDX index for that domain and returns the paths that actually exist, so src/leadgen/person/cascade.py spends its max_pages budget (default 8) on real URLs instead of guesses — a direct efficiency win on the same request budget. Keep `_harvest` in cascade.py fetching from the live origin as it does today; only the candidate list comes from Common Crawl. Cache the index response under the existing SQLite cache since a monthly crawl does not change within a run.


## KVK Handelsregister API (Netherlands) — avoid

https://developers.kvk.nl/ — Basisprofiel and Zoeken APIs · Commercial terms: €6.40 per API key per month plus €0.02 per query (the Zoeken/search API is free). Free test environment with a published test key. · HTTP/JSON, API key

**What.** Dutch chamber-of-commerce register lookup: KVK number, trade names, legal form, SBI activity codes, registered and branch addresses.

**Fit.** It mostly does not, and I want to be direct about that rather than list it for completeness. The Netherlands is a real market — EURES shows 235,404 Dutch vacancies — but the KVK API does not expose officer names (functionarissen) in its public products, so it cannot do the job that the Norwegian, French, Swiss and UK registers do here. You would pay per query for company metadata you can largely get free elsewhere, and still have no person. If Dutch coverage matters, the better spend is effort on the existing Impressum-equivalent crawl and on Dutch ATS boards, not on this API.

**Maintenance.** Live and professionally run; my unauthenticated probe returned a clean 401, confirming the key gate. KVK is the Dutch statutory register, so continuity is not a concern.

**Safety.** Low technical risk. The notable point is commercial rather than technical: per-query billing on a pipeline that crawls thousands of companies is an uncapped cost attached to a background job, and a runaway loop becomes an invoice rather than an error. If you ever adopt it, put a hard per-run query ceiling in code. Note also that the Netherlands restricts bulk reuse of KVK data more tightly than the Nordic registers — read the reuse terms before any bulk pull, not after.

**Integration.** Not recommended as a person source. If you want Dutch company metadata only, it slots into the same src/leadgen/company/ enrichment position as the Danish and Swedish helpers, gated behind an env-var key and a per-run counter enforced in src/leadgen/pipeline.py. Do not wire it into src/leadgen/person/register.py — it has no officer data to contribute there.


## OpenCorporates — avoid

https://api.opencorporates.com/ — reference https://api.opencorporates.com/documentation/API-Reference · Data under the Open Database License (ODbL), which is copyleft/share-alike. Commercial API plans start around £2,250/yr for 500 calls/month, rising to £12,000/yr for 5,000/month; free or discounted access is available to journalists, academics, NGOs and open-data projects. · HTTP/JSON or XML, API key

**What.** Aggregates company and officer records across 140+ jurisdictions into one schema with provenance links back to the source register.

**Fit.** On paper it is the obvious answer — one API instead of the eight national ones above. In practice two things disqualify it for HarvestKit. First the cost shape is backwards for this workload: £2,250/yr buys 500 calls a month, and this pipeline processes thousands of companies per run, so you would exhaust a year's Essentials quota in a single evening. Second, and more important, ODbL is share-alike: building a commercial lead database on an ODbL-licensed source raises a genuine obligation question about the derived database you then sell. Given that the Norwegian, French, Swiss and UK registers above are free and carry permissive or open-government licences, paying four figures to inherit a copyleft obligation is the worst of both trades.

**Maintenance.** Live and well established, with real provenance discipline — the underlying project is genuinely good, which is why it is worth explaining precisely why it does not fit rather than dismissing it.

**Safety.** Low technical risk; the risk is legal and commercial. The ODbL share-alike question on a derived commercial database is the one to get advice on before spending anything. There is also concentration risk in routing all jurisdictions through one paid vendor versus going direct to the registers, which cannot deprecate you off a plan.

**Integration.** Not recommended. If it were adopted it would sit behind the same country-keyed registry in src/leadgen/person/register.py as a fallback for jurisdictions with no direct free API — which, after adopting the Norwegian, French, Swiss and UK sources above, is a small enough remainder that the effort is better spent elsewhere.


## Adzuna job search API — avoid

https://developer.adzuna.com/ — terms at https://developer.adzuna.com/docs/terms_of_service · Restrictive. Commercial use is permitted only 'subject to a 14 day trial period', after which a licence agreement may be required. Attribution mandated for salary and research use. · HTTP/JSON, app_id + app_key

**What.** Aggregated job search across 16+ countries including much of Europe, with salary analytics.

**Fit.** It does not, and this one is worth an explicit warning rather than a quiet omission — Adzuna is the first result everyone finds when searching for a free European jobs API, so someone on your team will propose it. I read the actual Terms of Service, and they prohibit the exact use HarvestKit would make: data 'may not be used in its original format or in aggregation… to deliver any ongoing work or research… without written consent', and using the data 'to extract Confidential Information for commercial reuse will immediately be considered a breach'. Building and selling a lead list from it is a breach on the plain wording. The quotas make it academic anyway: 25 hits/minute, 250/day, 1,000/week, 2,500/month — at 50 results a page that is a few thousand ads a month against a pipeline that needs hundreds of thousands.

**Maintenance.** Live service, though my unauthenticated probe returned a 503 from their error page rather than a clean 401, which is a small signal about operational polish. Higher limits are available on request for qualifying applications — lead generation is unlikely to qualify.

**Safety.** The risk is contractual, not technical. Deploying a key on employee laptops to pull data in breach of the provider's terms puts the company on the wrong side of an agreement it actively accepted by registering — a materially worse position than the grey area of crawling a public site, because here there is a signed-up-to contract with an explicit prohibition. Avoid.

**Integration.** None — do not integrate. If the goal is broad European job-ad coverage, EURES above delivers 2.07 million vacancies with no key, no quota of this kind, and no clause forbidding the use. It is a strictly better answer to the same need.


## German Handelsregister / Unternehmensregister and the OffeneRegister dataset — avoid

https://www.handelsregister.de, https://www.unternehmensregister.de/ureg/, https://offeneregister.de/daten/, https://www.opensanctions.org/datasets/de_offeneregister/ · handelsregister.de: registration required, per-document fees (typically ~€1). OffeneRegister: open data, but see the freshness problem below. · HTML portals (no official public API); OffeneRegister ships bulk SQLite/JSON

**What.** Germany's statutory company register, holding the Geschäftsführer and Vorstand of every registered company, plus filed annual accounts via Bundesanzeiger.

**Fit.** It is the obvious place to look for German officer names, and the honest answer is that there is no good programmatic route, which is worth knowing so nobody spends a sprint discovering it. Germany publishes no official public API comparable to Norway's or France's. handelsregister.de did not even complete a TCP connection from this machine during testing (ConnectTimeout), and it is fee-gated and registration-gated in any case. The community OffeneRegister dataset looks like the answer until you check the dates: the OpenSanctions mirror reports 13,037,647 entities but `last_change` of 2019-02-01 — it has not been updated in over seven years, so its Geschäftsführer are whoever held the role in 2019. For a lead list that is worse than useless, because a confidently wrong name gets emailed. The commercial resellers (OpenRegister, handelsregister.ai) are the only current programmatic route and are paid.

**Maintenance.** unternehmensregister.de and bundesanzeiger.de are live (both returned 200); handelsregister.de timed out. OffeneRegister is effectively abandoned as a current dataset, as the 2019 last-change date shows.

**Safety.** The danger here is silent staleness rather than any technical exposure. A seven-year-old officer list produces plausible, well-formatted, wrong leads — the failure mode that damages sender reputation and is hardest to notice from inside the pipeline, because every row looks fine. If OffeneRegister is ever used, stamp each derived row with the dataset date and never let it override a name found on the live site.

**Integration.** Deliberately none — and the conclusion is that HarvestKit's existing approach for Germany is already the right one. §5 DDG (see landscape notes; it replaced §5 TMG on 14 May 2024) makes the Impressum legally mandatory and requires it to name the Vertretungsberechtigter, so src/leadgen/person/strategies/impressum.py is genuinely the best German officer source available and should be invested in rather than replaced. Pair it with the Arbeitsagentur seed above for reach and the job-ad contact parser in src/leadgen/person/jobad.py for HR names. One small correction worth making while you are in there: the comments in jobad.py:4, name.py:107, roles.py:62, impressum.py:3, atsboards.py:30 and docs/RUNBOOK-leads.md:148 all cite '§5 TMG', which has been repealed.


---

## The headline finding reframes the assignment

I was asked to research proxy infrastructure because EU company domains return 403. Before recommending any, I tested whether IP reputation is actually the cause. It mostly is not.

From this machine, same IP, same Chrome UA string, back to back, plain `requests` vs `curl_cffi` with `impersonate="chrome"`:

| host | requests | curl_cffi |
|---|---|---|
| www.sap.com | 403 (379 B) | **200 (59 KB)** |
| www.getyourguide.com | 403 (5,666 B) | **200 (290 KB)** |
| www.zalando.de | ReadTimeout | **200 (565 KB)** |
| www.personio.com | 429 (34 KB) | **200 (1.75 MB)** |
| www.hellofresh.de | 403 (5,685 B) | 403 (`server: cloudflare`, `cf-ray` present) |
| www.siemens.com / www.n26.com / www.adyen.com | 200 | 200 (identical bytes) |

Four of the five failing hosts served full content to the *same IP* the moment the TLS ClientHello and HTTP/2 fingerprint looked like a real Chrome. These hosts were never blocking the IP; they were blocking the `requests`/urllib3 handshake, which happens before a single header of yours is read — which is why the elaborate `Sec-CH-UA` and `Sec-Fetch-*` header work in `_stealth_headers` (http.py:463) cannot help, and why the UA rotation pool (http.py:105, pinned to Chrome 120/121 and Firefox 121 from 2023) now actively hurts: it advertises a browser version that contradicts the handshake underneath it.

`curl_cffi` **0.15.0 is already installed in this environment**, absent from requirements.txt, and unreferenced by any source file. The fix that recovers most of the lead famine is already on disk and switched off.

Only `hellofresh.de` needed more; it is a Cloudflare managed challenge (403 with `cf-ray` under chrome/chrome124/chrome131/safari/firefox impersonation alike) and needs real JS execution. Call that the residual tier — on this sample, roughly 1 host in 6, not 4 in 6.

**Practical consequence for spend:** had you bought residential proxies first, you would have paid per-GB for traffic that was failing for a reason proxies do not address, concluded the proxies were bad, and escalated. Fix the client fingerprint first, *then* size the proxy problem against what is still failing.

## Two live IP leaks that defeat any egress design

Whatever architecture you pick, these must be fixed first or the egress is decorative:

1. **`src/job_scraper/robots.py:58-62`** fetches robots.txt with a bare `urllib.request.urlopen`. It never consults `_ProxyPool`. Because robots is checked before the first request to every host (`_robots_allow`, called at the top of `get`), **the operator's real IP touches every target domain in the run**, proxies configured or not. On a thousands-of-domains lead run that is a complete disclosure of your prospect list from an employee's home address.
2. **`src/job_scraper/http.py:583`** — `head()` is the only request path that omits `proxies=self._proxies_dict(...)`. `get()` (line 526) and `post_json()` (line 641) both pass it; `head()` does not, and it also skips the per-proxy cookie jar.

A third, subtler one: `get()` at http.py:545 calls `self._proxies.report_failure()` on **every** 403. With `proxy_max_failures: 3` (config.py:61), three EU bot walls in a row — which is a completely normal minute of this workload — will cool down a perfectly healthy gateway for 300 s, and `_ProxyPool.acquire()` then falls back to a direct connection. It does warn (http.py:184), but the effect is that the system drops to the real IP precisely when targets are hostile. The transport failed and the origin said no are different events and should be booked differently.

And a documentation bug worth naming: the RUNBOOK's "anything other than 000 is fine — 301, 403 and 429 all mean the host is reachable" gives a green light on exactly the condition that yields zero leads. A lead-run health check should assert *extractable content*, not TCP reachability.

## Architecture comparison

**(a) Each device scrapes direct from its own residential IP.** Blocking: best raw IP reputation you can get — real ISP addresses with real history, free. Cost: zero. But it fails on everything else. GDPR: the employee's home IP becomes an identifier logged by thousands of third parties, and their household's address is tied to company data collection — you are processing the employee's data to do it. Consent: asking an employee to route company scraping through their private connection is a genuine imposition (bandwidth, ISP AUP exposure, possible blacklisting of their home address, and if a target's abuse desk complains it lands on them personally). Operationally you get no central rate control, no shared cache, no way to stop a run gone wrong, and a fleet of thousands of behavioural footprints you cannot audit. The user's own instinct — "I can't use my own device" — is correct and should be respected as policy, not just preference. **Reject.**

**(b) Company-run central egress gateway (WireGuard tunnel → gost or 3proxy on EU VPSs).** Blocking: good, not perfect — datacentre IPs carry less trust than residential, but with the fingerprint fixed you now know that matters for a minority of hosts, and you can buy a handful of ISP-proxy upstreams behind the same gateway for the stubborn ones. Cost: about EUR 4-6/month per exit at Hetzner-class pricing with 20 TB included; five exits is roughly EUR 25-30/month, amortised across the whole team rather than per seat. GDPR: much the strongest — no third party in the path, you are sole controller of the transport, you can log lawfully, apply retention, and answer a data-subject or supervisory-authority question about where data went. Consent: the employee's own connection is no longer involved; split-tunnel it so only HarvestKit's traffic enters the tunnel and write that scope down, because a full-device tunnel would sweep up personal browsing and turn a scraping tool into employee monitoring. Cost of ownership is real: you now operate infrastructure, handle abuse mail, and need an on-call answer when a gateway IP is blocked. **This is the recommendation.**

**(c) Each device uses a commercial proxy provider's endpoint.** Blocking: best available if you buy residential, and that is the honest place where open source cannot compete — reputation is issued by registries, not by software. Cost: per-IP for ISP/datacentre (roughly USD 0.30-2.40 per proxy per month at Webshare/IPRoyal-class vendors) or per-GB for residential (roughly USD 1.50-12/GB across Decodo, Oxylabs, Bright Data). GDPR: workable *with* a signed DPA and an EU-appropriate transfer basis — the vendor is your processor and sees destination hosts and TLS SNI. Strongly prefer **static ISP/datacentre** over **rotating residential**: residential pools are supplied by consumer devices recruited through bandwidth-sharing SDKs, the consent chain is unverifiable from your side, and buying that supply chain while processing EU personal data is an argument you do not want to be having with a regulator. Consent/practical: putting provider credentials on every employee laptop is a credential-sprawl problem with no revocation story. **Use as an upstream behind (b), not as the per-device answer.**

**(d) Central server does all scraping; devices are thin clients.** Blocking: identical to (b), plus better — one process means coherent per-host rate limiting, one shared `_ResponseCache` instead of N cold caches, and one place to apply the browser tier. Cost: one server plus a thin UI or CLI; no per-seat proxy spend. GDPR: cleanest of all — personal data never lands on employee laptops, so scope, retention, deletion and breach response all live in one auditable place, and the Article 15/17 requests the Kaspr decision turned on become answerable. Consent: nothing runs on the employee's machine, so the whole employee-device question evaporates. Downside: it is the largest engineering change from where this repo is today, and it is a single point of failure.

**Recommendation: (b) now, with (d) as the destination.** Concretely, in order:

1. Switch `HttpClient` to `curl_cffi` with impersonation, and realign the header/UA layer to match it. Zero infrastructure, largest measured gain. Add it to requirements.txt, pinned.
2. Fix the robots.txt and `head()` leaks, and stop penalising the proxy pool for origin 403s. Without these, step 3 is theatre.
3. Stand up one EU gateway box: WireGuard for employee laptops, gost or 3proxy behind it with a handful of additional IPs. Put its URL in `run.proxies` — the plumbing at `src/leadgen/cli.py:32` → `HttpClient` → `_ProxyPool` already exists and has simply never had anything to rotate.
4. Measure again. Whatever still fails after 1-3 is the real proxy/browser problem, and it will be a much smaller and better-characterised list than "4 of 6 EU domains".
5. For that residue, add a narrow browser tier (Patchright first — Playwright is already a dependency; Camoufox if you need Firefox-grade fingerprint spoofing and country-aligned locale) and, if needed, buy a small block of static ISP proxies as gateway upstreams under a signed DPA.
6. Migrate toward (d) as the pipeline stabilises, so personal data stops landing on laptops at all.

## Free proxies, specifically

Delete `tools/fetch_free_proxies.py`. The operator of a free plaintext HTTP proxy can read and rewrite every non-TLS request and response — including fabricating the names and emails your pipeline then sells — and for HTTPS sees the CONNECT target and TLS SNI, giving them a timestamped log of every company you are prospecting: your target list, which is the most commercially sensitive thing this system holds. The 2024 peer-reviewed study *Free Proxies Unmasked* documents proxies in the wild injecting ads and manipulating certificates, and finds most listed free proxies do not handle HTTPS properly at all; a meaningful share sit on networks with critical vulnerabilities or were placed there deliberately. Under GDPR you would be engaging a processor with no Article 28 contract, no Article 32 assessment, no known Chapter V transfer destination, and no ability to detect — let alone report — a breach. Add the employee-laptop dimension and you are instructing staff to install software that routes company traffic through unknown machines from their home networks. The script's own docstring already concedes free proxies are "unreliable... slow, or hostile" and points at paid providers; it exists purely as the path of least resistance, and it should not exist. I would also add an allow-list check in `_load_proxies` (`src/job_scraper/config.py:173`) so a future operator cannot quietly paste a harvested list back in.

## Two things that are true regardless of the proxy decision

**Scrapoxy is dead.** It was discontinued on 6 February 2026 after eleven years; Docker images are pulled from public registries, the docs site is offline, and the shared backend that self-hosted instances relied on for GeoIP and proxy status checks has been shut down for non-paying users. Its EOL FAQ is explicit that the source will not be open-sourced, that the commercial licence continues to prohibit forking and derivative works, and that there is no successor. Every guide still recommending it — and there are many — is stale. The self-hostable stand-ins are gost (MIT) for endpoint consolidation and load balancing, HAProxy for health-checking and metrics, and 3proxy for plain multi-IP egress.

**The proxy choice does not touch your GDPR exposure as a controller.** The CNIL fined Kaspr EUR 240,000 in December 2024 for scraping professional contact details from LinkedIn — findings under Article 5(1)(e) (retention), Articles 12 and 14 (transparency and informing data subjects), and Article 15 (right of access), including that collecting details users had restricted to their connections exceeded what those people could reasonably expect, and that informing them in English four years later was not adequate transparency. That is this business model, this regulator, this decade. Whatever egress you build, the controller-side obligations — a lawful basis (legitimate interest with a documented balancing test), Article 14 notice to the people whose data you collect, a defined retention period, and a working access/erasure path — are separate work, and they are the part that actually carries the fine. Two things in this repo make that harder than it needs to be: personal data landing on employee laptops (argues for architecture (d)), and credentials embedded in proxy URLs, which `logging.info` at http.py:207 will write `user:pass` and all into your run logs on cooldown.

**Licence hazards for a proprietary product.** `LICENSE` is now proprietary and grants installation to authorised individuals on machines they control. Against that, `requests-ip-rotator` is GPL-3.0 and `nodriver` is AGPL-3.0 — both are genuine questions for counsel rather than config decisions. The stack I am recommending is clean on this point: curl_cffi MIT, gluetun MIT, gost MIT, 3proxy BSD-3, mubeng Apache-2.0, Patchright Apache-2.0, Camoufox MPL-2.0 (file-level copyleft only — you publish changes to *its* files, not yours).

Sources: [curl_cffi](https://github.com/lexiforest/curl_cffi) · [JA3/JA4 fingerprinting guide](https://scrapfly.io/blog/posts/ja3-ja4-tls-fingerprinting-guide-to-detection-and-evasion) · [Scrapoxy EOL FAQ](https://scrapoxy.io/qna) · [scrapoxy/scrapoxy](https://github.com/scrapoxy/scrapoxy) · [gluetun](https://github.com/qdm12/gluetun) · [go-gost/gost](https://github.com/go-gost/gost) · [3proxy](https://github.com/3proxy/3proxy) · [mubeng](https://github.com/mubeng/mubeng) · [Squid RotatingIPs](https://wiki.squid-cache.org/ConfigExamples/Strange/RotatingIPs) · [requests-ip-rotator](https://github.com/Ge0rg3/requests-ip-rotator) · [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) · [Free Proxies Unmasked (arXiv 2403.02445)](https://arxiv.org/html/2403.02445v1) · [Cloudflare: The Trouble with Tor](https://blog.cloudflare.com/the-trouble-with-tor/) · [Tor for scraping (2026)](https://scrapfly.io/blog/posts/how-to-use-tor-for-web-scraping) · [CNIL: Kaspr fined EUR 240,000](https://www.cnil.fr/en/data-scraping-kaspr-fined-eu240000) · [EDPB on the Kaspr decision](https://www.edpb.europa.eu/news/data-scraping-french-supervisory-authority-fined-kaspr-eu240-000_en) · [Camoufox](https://github.com/daijro/camoufox) · [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) · [nodriver](https://github.com/ultrafunkamsterdam/nodriver) · [Anti-detect browser benchmark 2026](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/) · [Bright Data Proxy Manager](https://github.com/luminati-io/luminati-proxy) · [Webshare pricing](https://www.webshare.io/pricing) · [Residential proxy sourcing and consent](https://www.peakhour.io/blog/bandwidth-sharing-residential-proxy-supply-chain/) · [SOC 2 / GDPR questions for proxy vendors](https://www.joinmassive.com/blog/proxy-vendor-compliance-soc2-gdpr)


---

I did not just read about this — curl_cffi 0.15.0 turned out to be already importable on this machine (dragged in transitively by `scrapling`/`yfinance`, not by HarvestKit), so I ran a live read-only A/B instead of estimating. Everything below is measured, today, from this box.

MEASURED UPLIFT — this is the headline number you asked for.
On the lead investigator's original 6 domains: plain `requests` + Chrome UA scored 2/6 × 200. curl_cffi `impersonate="chrome136"` scored 5/6 × 200. sap.com 403→200, getyourguide.com 403→200, zalando.de ReadTimeout→200 (565 KB of real page). chrome124 and chrome136 gave identical results, so this is not profile-lottery.
On a wider 35-domain EU sample (DAX/CAC/AEX corporates + EU scale-ups — adyen, klarna, n26, personio, celonis, mollie, asml, ing, allianz, bmwgroup, datev, teamviewer, wise, revolut, doctolib, criteo, ovhcloud, contentsquare, backmarket, payfit, sumup, forto, infarm, delivery-hero, trivago, booking, bol...):
  plain requests : 22/35 = 63% × 200, 8 hard 403s, 2 timeouts
  curl_cffi      : 27/35 = 77% × 200, 5 hard 403s, 1 timeout
That is **five domains recovered, hard 403s cut from 8 to 5 (−37%)**, from a change to one constructor and three error handlers. Across a lead pipeline crawling thousands of EU company domains, a jump from 63% to 77% reachable is not a tuning gain — it is roughly a fifth more raw discovery surface, and it compounds because person-discovery needs multiple pages per domain.
Be honest about the ceiling, though: uplift is bounded by how many of your targets are Cloudflare/Akamai *static* walls versus JS challenges. On my sample the recoverable fraction was ~62% of failures (5 of 8 403s). Expect similar, not 100%.

WHY IT WORKS, AT THE LAYER THAT ACTUALLY DECIDES.
Against a live JA3/JA4 echo (tls.peet.ws): plain requests fingerprints as JA4 `t13d2812h2_257f3020b3a2_cbb9361b6bf9` — 28 ciphers, the unmistakable Python/OpenSSL hello. curl_cffi chrome136 produces `t13d1516h2_8daaf6152771_d8a2da3f94cd`, a genuine Chrome JA4, with Chrome's exact HTTP/2 pseudo-header ordering (`:method :authority :scheme :path sec-ch-ua sec-ch-ua-mobile sec-ch-ua-platform upgrade-insecure-requests user-agent accept ...`). The decision is made during the TLS handshake, before your first header is parsed. **The 403s in this codebase were never a headers problem**, which means the careful `_stealth_headers()` work at http.py:463 has been optimising a layer that does not get consulted. (JA3 hash varies per connection because Chrome uses GREASE; JA4 was byte-stable across runs. Judge by JA4.)

WHAT STAYS BROKEN, AND WHY THAT IS USEFUL.
I inspected the 5 survivors rather than shrugging at them:
  hellofresh.de, doctolib.fr, backmarket.fr → `cf-mitigated: challenge`, `server: cloudflare`, "Just a moment..." body. Cloudflare managed challenge; needs a JS runtime.
  bol.com → `server: AkamaiNetStorage`, `Set-Cookie: ak_bmsc=...`. Akamai Bot Manager sensor-data tier; also needs a browser.
  infarm.com → cloudflare, plain firewall/geo deny.
These are cheap, unambiguous, header-level signals. Build the tier-3 escalation trigger on them rather than on body heuristics — and note that `_looks_like_block()` (http.py:66) **cannot currently see any of them**: hellofresh's challenge body is 5962 bytes and the keyword branch only fires under 5000, so today a Cloudflare challenge looks to HarvestKit like an ordinary nothing. That is an independent bug worth fixing whichever client you choose.

AN UNEXPECTED FINDING ABOUT YOUR EGRESS — relevant to the proxy question.
Cloudflare's `cf-ray` on the blocked responses ended in **`-DEL`** (Delhi) and `-OTP`. Your requests are entering the EU from Indian IP space. Several EU consumer brands score non-EU datacentre/residential origin as a bot signal on its own, independent of fingerprint. So TLS impersonation and an EU-geolocated exit are complementary, not alternatives: impersonation fixes *what you look like*, an EU exit fixes *where you appear from*. Do impersonation first — it is free, it is a one-file change, and it recovered 5 domains with no proxy at all — then measure again before paying for proxies. You may need far fewer than you think.

THE PROXY PLUMBING IS FINE; THE POLICY IS NOT.
`_ProxyPool` with health tracking, cooldown, per-proxy cookie jars, and a `(host, proxy)` throttle key is good work — and it survives the curl_cffi swap intact, because curl_cffi accepts `proxies=` **per request** (I confirmed `proxies`, `proxy`, `proxy_auth` in its per-request param set, and that `socks5://` URLs parse). Two things to fix around it: (1) `trust_env` defaults to True in curl_cffi, so on a corporate laptop it will silently honour `http_proxy`/`https_proxy` and route your scrapes through IT's proxy — set `trust_env=False`; (2) `docs/RUNBOOK-leads.md` telling operators "anything other than 000 is fine — 301, 403 and 429 all mean the host is reachable" is precisely backwards for a lead run, where a 403 yields zero leads. curl_cffi hands you the right primitive to fix it: `response.primary_ip` is the actual egress IP libcurl used, so a real health check asserts `status == 200` AND `primary_ip == expected exit`, not "not zero".

ON THE FREE-PROXY HARVESTER — say this plainly to the user.
`tools/fetch_free_proxies.py` pulls anonymous public proxies off GitHub raw lists. A free plaintext HTTP proxy operator can read and rewrite every non-TLS request and sees the SNI of every TLS one. On **employee laptops**, that is an unknown third party positioned in the middle of company traffic. It is a materially worse risk than any library in this report, and it is the one piece of the current setup I would remove before adding anything. If proxies are needed after the impersonation fix, buy EU residential/ISP egress from a contracted provider with a DPA — under GDPR you are also processing EU personal data through whatever that proxy is, which makes "free and anonymous" a compliance problem as well as a security one.

SUPPLY CHAIN, STATED HONESTLY.
Every library that defeats TLS fingerprinting ships a native binary, because that is the only place the ClientHello can be rewritten. There is no pure-Python option; that trade-off is inherent, not a shortcut anyone took. curl_cffi is the smallest version of that bet: MIT, ~3.9 MB installed, one 3.2 MB `_wrapper.pyd`, no post-install script, no telemetry, public CI-built wheels, and a large enough dependent base (yfinance, scrapling) that a poisoned release gets noticed quickly.
One CVE matters to you specifically: **CVE-2026-33752 (CVSS 8.6), redirect-based SSRF, all versions < 0.15.0** — curl_cffi followed redirects into private IP ranges, and its own impersonation made those requests look like browser traffic to network controls. That threat model *is* HarvestKit's: crawling thousands of third-party domains with `allow_redirects=True` from employee laptops means any hostile careers page can 302 you at `169.254.169.254` or an internal 10.x service. This box has 0.15.0 — the minimum fix, four releases stale. Pin `curl_cffi>=0.16.3`, hash-pin the wheel, and pass `allow_redirects=CurlFollow.SAFE` (present and verified in the installed build). Because the fork does not track upstream curl's patch cadence automatically (curl 8.21.0 alone fixed 18 CVEs in 2026), put a dependency-update alert on it rather than trusting a `>=` range.

THREAD SAFETY — tested, not assumed.
HarvestKit shares one `HttpClient` (one session) across a `ThreadPoolExecutor` in `deep_scrape.py`, so this was the risk I most wanted to disprove. I ran a single shared `curl_cffi.requests.Session` across 12 threads, 24 requests to 6 hosts: **24/24 × 200, no errors, 8s**. curl_cffi keeps a thread-local curl handle (`use_thread_local_curl=True` by default), so your existing pattern is safe as-is. Upstream still suggests one session per thread as best practice; your `HostThrottle` already bounds concurrency, so I would ship the shared session and only revisit if you see intermittent failures. Worth noting primp yanked 1.2.0-1.2.2 for a parallel-client-init deadlock — the same test on primp would be mandatory, which is another reason it is not the pick.

BOTTOM LINE.
**Pick curl_cffi.** Not because it is the most sophisticated — wreq's structured protocol emulation is arguably better engineering, and primp is faster — but because it is the only one that (a) is MIT with no licence trap, (b) accepts per-request `proxies=`, which your `_ProxyPool` design requires and primp does not offer, (c) has a per-request kwarg surface identical to what `HttpClient.get()` already passes, so the change is a constructor and three exception handlers rather than a transport rewrite, (d) ships Windows wheels for 3.10-3.14, (e) released 19 days ago with Chrome 150/152 profiles, and (f) I measured it fixing 5 of your 8 EU 403s from this machine an hour ago.
Three things must land in the same commit or you will get *worse* results than today: kill the Firefox/Chrome-120 entries in `DEFAULT_UA_POOL` (a Firefox UA over a Chrome handshake is a louder bot signal than plain requests), widen `except requests.RequestException` to also catch `curl_cffi.requests.RequestsError` (verified **not** a subclass — it descends from OSError, so today's handlers would let it escape and kill a worker silently), and drop `response.apparent_encoding` (does not exist; use `default_encoding="utf-8"`). Details and line numbers are in the curl_cffi integration sketch.
Then re-measure before buying proxies. The honest sequence is: impersonation (free, today, +14 points of reach) → re-measure → EU residential exit for what is left → browser tier for the `cf-mitigated: challenge` remainder. Doing it in that order tells you how much proxy you actually need instead of guessing.

Sources: [curl_cffi](https://github.com/lexiforest/curl_cffi), [curl-cffi on PyPI](https://pypi.org/project/curl-cffi/), [impersonation targets](https://curl-cffi.readthedocs.io/en/latest/impersonate/targets.html), [CVE-2026-33752](https://advisories.gitlab.com/pypi/curl-cffi/CVE-2026-33752/), [primp](https://github.com/deedy5/primp), [primp on PyPI](https://pypi.org/project/primp/), [wreq-python](https://github.com/0x676e67/wreq-python), [rnet on PyPI](https://pypi.org/project/rnet/), [bogdanfinn/tls-client](https://github.com/bogdanfinn/tls-client), [hrequests](https://github.com/daijro/hrequests), [Scrapling](https://github.com/D4Vinci/Scrapling), [JA3/JA4 fingerprinting guide](https://scrapfly.io/blog/posts/ja3-ja4-tls-fingerprinting-guide-to-detection-and-evasion), [best anti-bot bypass tools 2026](https://scrapfly.io/blog/posts/best-anti-bot-bypass-tools)


---

## What I measured, before what I read

All figures below are live probes run from this machine during this task, read-only, no repo changes.

**38 mid-market EU company homepages** (personio.de, celonis.com, n26.com, contentful.com, sennder.com, raisin.com, mollie.com, adyen.com, bunq.com, doctolib.fr, qonto.com, alan.com, payfit.com, spendesk.com, klarna.com, tink.com, kry.se, sonarsource.com, nexthink.com, frontify.com, docplanner.com, brainly.com, booksy.com, packhelp.com, ...) — i.e. the actual lead population, not the six giants from the earlier probe.

| Tier | Client | Failures |
|---|---|---|
| A | `requests` + HarvestKit's current `_stealth_headers` | **8/38 (21%)** |
| D | `curl_cffi` `impersonate="chrome"` | **3/38 (8%)** |

Flips attributable to TLS impersonation alone: personio.de `429 -> 200` (1.78 MB), contentful.com `429 -> 200` (814 KB), raisin.com `403 -> 200` (211 KB), messagebird.com `502 -> 200` (878 KB), brainly.com `403 -> 200` (262 KB).

On the lead investigator's six-domain set, split by cause:

| Domain | requests + HK headers | requests + corrected headers | httpx HTTP/2 + corrected | curl_cffi chrome |
|---|---|---|---|---|
| sap.com | 200 | 200 | 200 | 200 |
| siemens.com | 200 | 200 | 200 | 200 |
| arbeitsagentur.de | 200 | 200 | 200 | 200 |
| **zalando.de** | 403 (161 KB wall) | **200 (575 KB)** | 200 | 200 |
| **getyourguide.com** | 403 (6 KB) | 403 (98 KB) | 403 | **200 (293 KB)** |
| **hellofresh.de** | 403 | 403 | 403 | **403 — still blocked** |

Three conclusions fall straight out, and they set the whole architecture:

1. **Headers alone are worth real money.** zalando.de was recovered by nothing but a coherent Chrome header set with a country-matched `Accept-Language`, still over plain `requests` on HTTP/1.1.
2. **HTTP/2 is not the lever.** `httpx(http2=True)` with identical corrected headers scored *exactly the same* as `requests` on every domain. What recovers getyourguide is the TLS ClientHello, which neither `requests` nor `httpx` can alter. Do not spend effort migrating to an HTTP/2 client *for* HTTP/2.
3. **A residual needs a real browser.** hellofresh.de answers 403 with a 5,962-byte Cloudflare `Just a moment...` interstitial (markers `cloudflare`, `/cdn-cgi/challenge-platform`, `enable javascript`) at every HTTP rung. doctolib.fr answers 403 with a **135,095-byte** body, an empty `<title>`, and captcha/challenge markers.

That last number is the most important single finding in this report, and I will come back to it.

---

## 1. Header correctness — how much of the 403 rate it explains

Roughly a third of the recoverable failures, for about 40 lines of work. Everything wrong in `_stealth_headers()` is wrong in a way no real browser can be:

- **`Sec-Fetch-Site: same-origin` hardcoded on every request**, including the first navigation to a domain the client has never contacted and holds no cookie for. Chrome sends `none` for a typed/bookmarked navigation, `cross-site` when arriving from another origin, `same-origin` only when the referer really is the same origin. Anti-bot vendors check the four `Sec-Fetch-*` values for internal coherence and against the URL shape; asserting `same-origin` on a cold first hit is self-refuting.
- **`DNT: 1`.** Chrome removed the DNT setting. A Chrome-141 UA carrying DNT is a contradiction.
- **`Cache-Control: no-cache` + `Pragma: no-cache` on every request.** Chrome sends that pair only on a hard reload. Every navigation being a hard reload is a signature, not a browser.
- **`Sec-CH-UA` pinned to Chromium 120 while `_pick_ua()` draws randomly per request** from a pool containing Firefox 121 and Safari 17 UAs. One host sees a Firefox UA carrying Chromium client hints, over one connection, sharing one cookie jar. Client hints are hard to spoof *consistently*, which is precisely why vendors weight them heavily — sending them wrong is worse than not sending them.
- **A stale UA pool.** Chrome 120/121 and Safari 17.1 are ~2 years old against Chrome 141+ in September 2026. Version age is itself a reputation input.
- **No `priority: u=0, i`.** Chrome has sent it since v117.
- **One five-language `Accept-Language`** (`en-US,en;q=0.9,de;q=0.8,fr;q=0.7,it;q=0.6`) sent identically to a Norwegian and a Portuguese site. The already-landed `identity.py` `ACCEPT_LANGUAGE_BY_COUNTRY` table fixes this and is the right design.
- **`head()` sends `{"User-Agent": ...}` and nothing else** — no Accept, no Sec-Fetch, no hints. It is used for probing, so the probe path is more obviously robotic than the fetch path.

Chrome's navigation header order, for reference: `sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, upgrade-insecure-requests, user-agent, accept, sec-fetch-site, sec-fetch-mode, sec-fetch-user, sec-fetch-dest, accept-encoding, accept-language, cookie, priority`. `requests`/urllib3 cannot guarantee that order (it injects its own `Connection: keep-alive` and session defaults first), which is a second, independent reason rung 1 exists rather than a reason to abandon rung 0.

---

## 2. Soft-block detection — the biggest actual gap

`_looks_like_block()` in `http.py` only examines bodies **under 5,000 characters**. Measured block pages from this session: doctolib.fr **135,095 bytes**, getyourguide.com **98,243 bytes**, zalando.de **160,907 bytes**. Every one of them sails past the heuristic. When such a page arrives with HTTP 200 — which is how Cloudflare, DataDome and Akamai routinely serve challenges — HarvestKit scores it as success, caches it for the configured **7 days** (`cache_ttl_seconds: 604800` in `configs/leads/eu-it.yaml`), extracts zero people from it, and reports the company as "named nobody". That is the lead famine, expressed as a one-line heuristic.

Two more defects in the same function: it lists `"404 not found"` as a block keyword, conflating a genuinely missing `/team` page — extremely common, and the signal to try the next candidate path — with a bot wall, though the two demand opposite responses; and `_ResponseCache.get()` calls it on read, so the cap also governs cache hygiene.

The replacement is a **three-way outcome** — the `Outcome` enum now in `transport.py` (`OK` / `BLOCKED` / `RATE_LIMITED` / `NOT_FOUND` / `SERVER_ERROR` / `ERROR`) is exactly right, and `BLOCKED` vs `EMPTY` is the distinction that earns its keep: the first is ours to fix by escalating, the second is the site's answer and escalating it only burns requests. Signals worth combining, size-independent:

- **Specific vendor product strings**, never generic words: `just a moment...`, `/cdn-cgi/challenge-platform`, `cf_chl_opt`, `attention required! | cloudflare`, `_incapsula_resource`, `incapsula incident id`, `captcha-delivery.com`, `geo.captcha-delivery`, `px-captcha`, `perimeterx`. ("access denied" appears in real cookie banners; that generic-keyword approach is what forced the 5,000-char cap in the first place.)
- **Empty or challenge `<title>`.** doctolib.fr's 135 KB block page has `<title></title>` — a 135 KB document with no title is not a real page.
- **A per-domain rolling median body size**, persisted. A response at <20% of a domain's median is suspect regardless of status.
- **A content contract per page class.** For HarvestKit a `/team` or `/impressum` fetch should carry *some* of: a `mailto:`, an `<h1>`, ≥20 anchors, a JSON-LD block. Zero of all four on a 2xx is worth exactly one escalation attempt — and the result trains `TransportMemory`.
- **Redirect-target inspection.** A 200 whose final URL landed on `/consent`, `/challenge`, `/blocked` or a country-gate is a block wearing a 200.

And the counter-example that keeps this honest: **gorillas.io returns 200 with 2,394 bytes** — a genuine tombstone page for a dead company. Thin but real. A classifier that escalates on size alone will launch a browser for every defunct company in the seed list. Size is a *prior*, never a verdict.

---

## 3. Escalation ladders and per-domain memory

Rungs, cheapest first: **(0)** plain `requests` with a coherent header identity — fine for ATS APIs and most SSR HTML, and it is most of the traffic; **(1)** `curl_cffi` with a real Chrome TLS + HTTP/2 fingerprint — recovers the 403/429/502 class my measurements isolate; **(2)** a stealth browser (Patchright) — for JS-challenge and client-rendered pages only. `transport.py` already implements this shape with SQLite-backed per-domain memory. Four refinements it still needs:

**a) The memory must be able to walk back down.** `TransportMemory.preferred_rung()` currently only ratchets upward, so a domain that was briefly angry pays browser cost forever. Crawlee's `AdaptivePlaywrightCrawler` is the published prior art for the fix: its `RenderingTypePrediction` carries a `detection_probability_recommendation` (0-1) that makes it re-run both tiers some fraction of the time and compare via a `result_comparator`. Port the epsilon: with p≈0.1, try one rung below the remembered one and record the outcome.

**b) Key on the registrable domain (eTLD+1), not the netloc.** `HttpClient._hostname()` returns `urlparse(url).netloc`, so `www.acme.de`, `jobs.acme.de` and `careers.acme.de` each get separate throttle budgets and separate rung memory against a single origin behind a single WAF. `tldextract` is already a dependency; `transport.py` already has `registrable_domain()` — make `HostThrottle` and the proxy jar use it too.

**c) The cf_clearance handoff is the payoff of having a browser rung.** A Cloudflare clearance cookie is bound to **IP + User-Agent + TLS/JA3**. So: let rung 2 solve the challenge once, export the cookie jar and the exact UA the context used, hand them to rung 1 pinned to the *same* egress IP and the *same* `impersonate` profile, and finish the company's remaining 5-14 pages on the cheap rung. Rotate the proxy or change the impersonate profile mid-company and the cookie is dead on first reuse — which is the single most common way this pattern is implemented wrongly.

**d) Budget rung 2 hard.** ~80-150 MB and 1-2 s per page. A global semaphore plus a per-run cap, or a laptop crawling thousands of companies will OOM.

**Deployment bug worth flagging on its own:** `curl_cffi` (0.15.0) and `httpx` are importable on this machine but appear in **neither `requirements.txt` nor `pyproject.toml`**. `ImpersonateTransport.available()` will therefore return True here and False on every employee laptop — rung 1 silently disabled in production while every local test passes. Same for `patchright`. Declare both, pinned.

---

## 4. Per-domain adaptive concurrency and politeness

`HostThrottle` is documented as a token bucket but is actually a fixed concurrency semaphore plus a fixed `min_delay`. Nothing in it responds to what the host is saying back.

- **Token bucket vs leaky bucket.** For politeness to an origin, use a **token bucket** (capacity ~3-5, slow refill) — a real page load is a burst of requests followed by a pause, and a token bucket reproduces that shape. A leaky bucket smooths to a perfectly constant rate, which is itself a machine signature. Keep a leaky/GCRA limiter for the *global* egress rate if you want one.
- **AIMD per registrable domain.** Start at 1-2 in flight; additive increase (+1) after N consecutive `OK`; multiplicative decrease (x0.5, floor 1) on any `RATE_LIMITED`, `BLOCKED` or timeout. Standard, cheap, and it is what every mature system converges on.
- **Steal Scrapy AutoThrottle's one non-obvious rule.** Target delay = `latency / target_concurrency`, averaged with the previous delay — *and never let a non-200's latency decrease the delay.* Error and block pages return fast, so a naive latency controller accelerates exactly when a host starts refusing you. HarvestKit has that hole today.
- **Circuit breaker, persisted.** After K consecutive `BLOCKED` at the top available rung, open the domain for `min(300 * 2**consecutive_blocks, 86400)` seconds; half-open with a single probe. This is where the tarpit case lives: after repeated probes from one IP, zalando.de stopped returning 403 and started **read-timing-out at 20 s**. With `leadgen/cli.py`'s 20 s timeout and an 8-wide pool, an unbroken circuit burns minutes of wall-clock per tarpitting domain, silently.
- **Never open the breaker on `NOT_FOUND` or `EMPTY`.** Those are honest answers and say nothing about welcome.

---

## 5. Session and identity coherence

Rotating the UA per request is not camouflage; it is a signal, because no browser changes what it is between two page loads over one connection. `_pick_ua()` is called *inside* `get()`, so today a single host sees Chrome, then Safari, then Firefox, sharing one cookie jar. The already-landed `identity.py` — deriving a stable identity per host — is the correct fix.

The unit to bind is **{UA, matching sec-ch-ua triple, platform, country-matched Accept-Language, TLS/impersonate profile, cookie jar, egress IP}**, held for the life of a session. Crawlee's `SessionPool` is the reference semantics and is worth copying in ~40 lines rather than adopting: retire on `max_usage_count`, on `max_age`, on `max_error_score`, and immediately on `blocked_status_codes` (403). For HarvestKit's shape — 6-14 requests against a domain, then never again — **one identity per (registrable domain, run)** is both simplest and most correct; add a ~20-minute age cap and an error-score retire for long runs.

Two concrete follow-ons:

- **Warm up the origin.** Fetch `https://<domain>/` first with `Sec-Fetch-Site: none`, keep the cookies, then fetch `/impressum` with `Sec-Fetch-Site: same-origin` **and** `Referer: https://<domain>/`. That makes the Sec-Fetch story *true* rather than asserted, and it collects the consent/session cookies most EU sites set before serving real content. Cost: one extra request against the 6-10 the pipeline already spends per company.
- **Stop rotating the proxy mid-session.** `_ProxyPool.acquire()` rotates per request. The per-proxy cookie jar partially compensates, but an identity must not move between IPs mid-company — and it categorically cannot if a cf_clearance cookie is in play. Acquire once per (domain, session) and hold.

**On the free-proxy path** (`tools/fetch_free_proxies.py`): beyond the plaintext-MITM problem already noted, free public proxies actively *break* this architecture. Per-request rotation through anonymous IPs destroys the identity binding that makes clearance cookies and session reputation work, and those IPs are already on every commercial blocklist — so they raise the 403 rate rather than lowering it. This routing design assumes a **small number of stable, trustworthy egress IPs held for the life of a session**. That is the requirement to hand to whoever is evaluating proxies.

---

## 6. Retry and backoff that does not amplify

- **Never retry a `BLOCKED` at the same rung with the same identity.** That is the amplification case in its purest form. Escalate the rung, or retire the identity — do not repeat yourself.
- **Stop attributing WAF responses to the proxy.** `get()` calls `self._proxies.report_failure(proxy_entry)` on both 429 and 403. A 429 is the *host's* rate limit and a 403 is usually about the *fingerprint*; neither is the proxy's fault. With `proxy_max_failures: 3`, three 403s from three unrelated domains retire a perfectly healthy proxy for 300 s. On a small pool that cascades into the `acquire()` exhaustion path, which logs a warning and **falls back to a direct connection, exposing the operator's real IP** — on an employee laptop, that is a privacy incident triggered by an unrelated site's bot wall.
- **Retry budgets, not per-request attempt counts.** Cap retries as a fraction (~10%, the Google SRE starting point) of a domain's successful throughput, enforced by a token bucket. One failing request may still retry; a whole thread pool hitting a browned-out host must not collectively multiply the load exactly when it is least welcome. Envoy retry budgets, gRPC `retryThrottling` and Hystrix all converged on this.
- **Full jitter** on exponential backoff, capped. The 30 s `Retry-After` ceiling already in `_retry_after_seconds()` is good and the reasoning in its comment is right — keep it; also halve the domain's AIMD concurrency on the same event.
- **Persist per-domain block state across runs.** Extend the existing `transport_memory` table from `(domain, rung, successes, failures, updated_at)` to carry `consecutive_blocks` and `blocked_until`. Same SQLite file, so it survives runs for free. Today every block is re-discovered from scratch on every run, at full price.
- **Never cache a `BLOCKED` or `EMPTY` outcome**, and add short-TTL *negative* caching so one run does not re-probe a dead domain ten times in an hour. The current 7-day positive TTL over an unreliable success test is what turns one bad afternoon into a bad week.

---

## 7. Framework verdict: keep `http.py`, improve it

**Clear answer: do not adopt Scrapy or Crawlee. Keep the bespoke client and give it a routing brain.** Three reasons, in order of weight:

1. **Concurrency model.** HarvestKit is synchronous throughout — `pipeline.py` and `deep_scrape.py` use `ThreadPoolExecutor`, every one of the 14 adapters takes a sync `http` object, and 453 tests are written against that. Crawlee is asyncio-first; Scrapy is Twisted. Either means rewriting the pipeline, the CLI, all adapters and the test suite in order to replace ~600 lines of `http.py`. That is the whole product, traded for machinery you would still have to customise.
2. **Workload shape.** Both frameworks' crown jewels are frontiers — schedulers, dupefilters, depth/priority queues — tuned for crawling *one site deeply*. HarvestKit does the inverse: ~6-14 targeted requests against each of thousands of *distinct* origins. Almost none of that machinery earns its keep here, while **per-domain identity, rung memory and block state — the things that actually matter — are exactly what you would be hand-building inside either framework anyway.**
3. **The valuable parts are portable as ideas, and the one indispensable part is a standalone library.** Take Crawlee's `SessionPool` semantics (~40 lines), its adaptive re-check epsilon (~10 lines), and Scrapy AutoThrottle's non-200-never-lowers-the-delay rule (~20 lines). Take BrowserForge as an actual dependency. Leave both runtimes.

Adopt the *libraries*, not the frameworks: **curl_cffi** (rung 1) and **Patchright** (rung 2), both already wired into `transport.py` and both currently undeclared in the dependency files. Revisit the framework question only if HarvestKit's shape changes to millions of pages/day with distributed scheduling — that is genuinely Scrapy's home ground, and it is not today's shape.

---

## 8. One more thing: the runbook's health check is a false green

`docs/RUNBOOK-leads.md` tells the operator that "Anything other than 000 is fine — 301, 403 and 429 all mean the host is reachable." For a lead run, 403 and 429 are *precisely the failure modes*, and on my 38-domain sample they were 8 of 8 failures at tier A. That line should be deleted, not softened.

Replace it with a **canary check that measures what the pipeline actually needs**: a fixed list of ~20 EU domains spanning the observed difficulty classes (siemens.com / arbeitsagentur.de as easy, zalando.de as header-sensitive, getyourguide.com as TLS-sensitive, hellofresh.de and doctolib.fr as browser-only, gorillas.io as the thin-but-real control), fetched through each rung, asserting `classify() == Outcome.OK` **and** that the body contains at least one `<a href>` and a non-empty `<title>`. Report per-rung success and the chosen rung per domain. That turns "is the network up" — a question whose answer was never in doubt — into "can this build still reach Europe", which is the question the operator actually has, and gives you the harness to measure every change above.

---

### Sources

- [curl_cffi](https://github.com/lexiforest/curl_cffi) · [impersonate targets](https://curl-cffi.readthedocs.io/en/latest/impersonate/targets.html) · [curl-impersonate fork](https://github.com/lexiforest/curl-impersonate)
- [scrapy-impersonate](https://github.com/jxlil/scrapy-impersonate) · [Scrapy AutoThrottle](https://docs.scrapy.org/en/latest/topics/autothrottle.html)
- [Crawlee for Python](https://crawlee.dev/python/) · [Session management](https://crawlee.dev/python/docs/guides/session-management) · [AdaptivePlaywrightCrawler](https://crawlee.dev/python/api/class/AdaptivePlaywrightCrawler) · [Migrating from Scrapy](https://crawlee.dev/python/docs/guides/scrapy-migration) · [Crawlee for Python v1 / ImpitHttpClient](https://crawlee.dev/blog/crawlee-for-python-v1)
- [impit](https://github.com/apify/impit) · [Apify: impit, browser impersonation made simple](https://blog.apify.com/impit-browser-impersonation-made-simple/)
- [BrowserForge](https://github.com/daijro/browserforge) · [Crawlee BrowserforgeHeaderGenerator](https://crawlee.dev/python/api/class/BrowserforgeHeaderGenerator)
- [Patchright (Python)](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) · [Camoufox](https://github.com/daijro/camoufox) · [zendriver](https://github.com/cdpdriver/zendriver) · [Botasaurus](https://github.com/omkarcloud/botasaurus) · [katana](https://github.com/projectdiscovery/katana)
- [Akamai: Passive Fingerprinting of HTTP/2 Clients (Black Hat EU 2017)](https://blackhat.com/docs/eu-17/materials/eu-17-Shuster-Passive-Fingerprinting-Of-HTTP2-Clients-wp.pdf) · [Scrapfly: HTTP/2 and HTTP/3 fingerprinting](https://scrapfly.io/blog/posts/http2-http3-fingerprinting-guide) · [Sec-Fetch and Client Hints against headless browsers](https://blog.sicuranext.com/sec-fetch-and-client-hints-a-powerful-tool-against-automation/) · [httpcloak: programmatic headers](https://httpcloak.dev/fingerprinting/programmatic-headers)
- [Google SRE: Handling Overload](https://sre.google/sre-book/handling-overload/) · [Marc Brooker: Fixing retries with token buckets and circuit breakers](https://brooker.co.za/blog/2022/02/28/retries.html) · [pybreaker](https://github.com/danielfm/pybreaker) · [tenacity](https://github.com/jd/tenacity)
- [Cloudflare cf_clearance: why it expires and how to stop the re-challenge loop](https://dev.to/bshahin/cloudflare-cfclearance-why-it-expires-and-how-to-stop-the-re-challenge-loop-do9) · [Scrapfly: How to bypass Cloudflare](https://scrapfly.io/blog/posts/how-to-bypass-cloudflare-anti-scraping) · [Scrapfly: Best stealth browsers 2026](https://scrapfly.io/blog/posts/best-stealth-browsers)
- [Web Scraper: 200 OK but no data](https://webscraper.io/blog/200-ok-but-no-data-diagnosing-incorrect-page-responses) · [HTTP 200 Is Not Success: Write a Contract Per Host](https://dev.to/roamproxy/http-200-is-not-success-write-a-contract-per-host-192i)


---

## Important: the working tree changed under me mid-task

At session start git status was clean. By the time I read src/job_scraper/http.py in detail, a sibling agent had created **src/job_scraper/transport.py** (a 3-rung ladder: requests -> curl_cffi -> stealth browser, with per-domain SQLite memory) and **src/job_scraper/identity.py**, and rewired HttpClient.fetch to use them (http.py:406 build_ladder, http.py:570 self._ladder.fetch). That work already implements the routing design I was going to recommend and already picks curl_cffi and Patchright. So I re-aimed the second half of this research at validating it against live EU targets. My findings below are partly a review of that in-flight code. I edited nothing.

## 1. What actually gives HarvestKit's headless Playwright away — measured on this box, not quoted

Environment: playwright 1.58.0, bundled Chromium **145.0.7632.6**, and `chromium_headless_shell-1243` present. `src/job_scraper/crawl.py:122` calls `chromium.launch(headless=True)` with no channel, which on Playwright >=1.49 runs the **headless shell binary**, the single most detectable way to start Chromium.

I ran crawl.py's exact context options and captured the real wire headers to https://www.siemens.com:

```
sec-ch-ua      = "Not:A-Brand";v="99", "HeadlessChrome";v="145", "Chromium";v="145"
user-agent     = Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/120.0.0.0 Safari/537.36
accept-language = en-US
```

Three fatal tells in the request line alone, before a byte of JS runs:
- **The literal string `HeadlessChrome` is in a client hint on every single request.** That is a one-line WAF rule, and no in-page stealth can touch it.
- **UA says Chrome/120, `sec-ch-ua` says 145.** Impossible for a real browser.
- **The `extra_http_headers` Accept-Language was silently clobbered.** crawl.py:127 sets `"en-US,en;q=0.9,de;q=0.8,fr;q=0.7"` but `locale="en-US"` wins and the wire header is a bare `en-US` with no q-values — which real Chrome never sends. **The new transport.py:652 has the identical bug** (it sets both `locale=` and `extra_http_headers["Accept-Language"]`).

JS-side, same context:

| Signal | HarvestKit today | Real Chrome |
|---|---|---|
| `navigator.webdriver` | **true** | false |
| `navigator.plugins.length` / `mimeTypes.length` | **0 / 0** | 5 / 2 |
| `window.chrome` | **undefined** | object (runtime, loadTimes, csi) |
| `navigator.pdfViewerEnabled` | **false** | true |
| WebGL UNMASKED_RENDERER | **ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)** | ANGLE (Intel/NVIDIA/AMD ...) |
| `Intl...timeZone` | **Asia/Calcutta** (host TZ, never set) | must match exit IP |
| screen / avail / outer / inner | **1366x900 / 1366x900 / 1366x900 / 1366x900**, dpr 1 | availHeight < height (taskbar); outerHeight > innerHeight by ~85-120px (chrome) |
| `navigator.languages` | **["en-US"]** (single entry) | ["en-US","en"] |

The screen triple-equality is a classic: a real window has browser chrome and the desktop has a taskbar, so those four numbers are never identical. SwiftShader means "no GPU", which on a claimed Windows desktop is a contradiction.

**Signal -> what fixes it:**

| Signal | Fixed by |
|---|---|
| TLS ClientHello / JA3-JA4, HTTP2 SETTINGS | curl_cffi only (or a real browser). No header change, ever. |
| `Runtime.enable` CDP handshake | Patchright, nodriver/Zendriver, SeleniumBase CDP Mode. **Not** by any JS-injection stealth — it fires before page JS exists. |
| `HeadlessChrome` in UA + sec-ch-ua | Not launching the headless shell: `channel="chromium"` or `channel="chrome"`. |
| `navigator.webdriver`, plugins, window.chrome, pdfViewerEnabled | Patchright (verified: reads false under it), Camoufox, playwright-stealth (badly, and it stops there). |
| WebGL/SwiftShader, canvas, audio, fonts | Camoufox (C++, below the JS boundary); headful real Chrome with a GPU. Not Patchright. |
| Timezone/locale vs exit-IP coherence | Camoufox `geoip=True`, or setting `timezone_id` + locale from the proxy's country yourself. |
| IP reputation / ASN | **Nothing on this list.** |

## 2. Live EU evidence: the HTTP rung recovers most of it, cheaply

30 EU company domains, plain `requests` + Chrome UA vs `curl_cffi(impersonate="chrome")`:

- plain: **19/30 reachable (63%)**
- curl_cffi: **24/30 (80%)**
- recovered: sap.com 403->200 (59 KB), getyourguide.com 403->200 (295 KB), zalando.de ReadTimeout->200 (557 KB), personio.de 429->200, criteo.com 403->200

That is +26% relative domain coverage for one MIT dependency, no browser, no display, no RAM. And for HarvestKit's actual job — person discovery on Impressum/imprint/team pages — the HTTP rung is usually *sufficient*: 6 of 8 German/Swiss/French imprint pages returned 2–7.6 KB of full static text over it. A German Impressum is legally mandated server-rendered text; it does not need a browser.

## 3. The hard tail is not a fingerprint problem — it is an IP problem

Six domains resisted. I identified each vendor from response headers:

- **hellofresh.de** — `cf-mitigated: challenge`, `server: cloudflare`, title "Just a moment...", `cf-ray: ...-DEL`
- **doctolib.fr** — `cf-mitigated: challenge`, `cf-ray: ...-VIE`
- **blablacar.fr** — `x-datadome: protected`, captcha-delivery.com
- **booking.com** — HTTP **202** with a 7 KB empty-`<title>` XHR bootstrap (soft wall via CloudFront `DEL51-P4`)
- **delivery-hero.com, about-you.de** — connection-level refusal

Against hellofresh.de / doctolib.fr / blablacar.fr I then tried, from this machine:
- curl_cffi with **8 different TLS profiles** (chrome, chrome124, chrome131, chrome133a, chrome136, safari18_0, firefox135, edge101) — identical 403 every time
- **Patchright headless** — 403 "Nur einen Moment..."
- **Patchright + real Chrome channel + headful** — 403
- **Camoufox headless with humanize + locale=de-DE** — 403 on all three

Every fingerprint layer clean, still refused. The `cf-ray` PoPs (`-DEL` Delhi, `-VIE` Vienna) say why: a German grocery brand and a French medical-booking site under a Cloudflare managed challenge are not going to hand pages to an Indian consumer ASN. **Caveat on confidence:** this was one IP, one shot per config, fresh profiles, no warm-up, no proxy — so I cannot fully separate IP reputation from residual profile/behavioural factors. But the direction is unambiguous and it matches undetected-chromedriver's own README ("DOES NOT hide your IP address... if your ip reputation at home is low, you won't pass").

**The practical consequence for the parent agent's plan: buying a stealth browser before buying EU egress buys nothing on this tail.** Order of investment should be (1) curl_cffi — free, +26%; (2) EU residential/ISP egress — unlocks the tail; (3) stealth browser — needed for JS-rendered content regardless, and only *then* effective on challenges. Note also that free public proxy lists (tools/fetch_free_proxies.py) make this strictly worse: those ASNs are already scored, and a plaintext HTTP proxy reads and rewrites all non-TLS traffic.

## 4. Two concrete bugs in the code, both verified live

**(a) The old detector was blind.** `src/job_scraper/http.py::_looks_like_block()` gates its keyword scan on `len(lo) < 5000`. A 2026 Cloudflare interstitial is 5.7–6.0 KB. I ran it against live bodies: hellofresh.de 403 (5685 bytes plain / 5962 via curl_cffi) -> **False**, getyourguide.com 403 (5666 bytes) -> **False**. Every Cloudflare challenge was being classified as legitimate content, handed to the person-extraction strategies, and recorded as "company names nobody". That is the lead-famine mechanism, and it also made any routing layer impossible — you cannot escalate what you cannot detect.

**(b) The new detector is much better but still has one hole.** transport.py's `classify()` correctly flags hellofresh.de, doctolib.fr and blablacar.fr as BLOCKED. It classifies **booking.com's HTTP 202 soft wall as OK**. No normal HTML page is served 202; the body is 7 KB with `<title></title>` and nothing but an XHR bootstrap. Suggested tightening in `_body_is_challenge` / `classify`: treat 202 as suspicious by default, and treat any 2xx under ~12 KB with an empty-or-missing `<title>` and no `_CONTENT_MARKERS` as BLOCKED rather than OK.

## 5. Three more issues in the new BrowserTransport (transport.py:551-700)

- **transport.py:614 `--no-sandbox` and `--disable-features=IsolateOrigins,site-per-process`.** These disable the Chromium renderer sandbox and Site Isolation while the browser visits attacker-controlled scraped URLs, on employee laptops. That is the most dangerous line in the new code — and it is a double loss, because real Chrome has Site Isolation on, so turning it off is *also* a detection signal. `--no-sandbox` is a Linux-container workaround; it has no business on Windows endpoints.
- **`new_context()` per fetch (transport.py:648).** Every `cf_clearance` / `__cf_bm` cookie is thrown away, so each page re-pays the challenge. Use one context per (registrable domain, proxy) and persist the jar — HarvestKit already has per-proxy jars in `_ProxyPool.jar_for`.
- **`proxy={"server": proxy}` (transport.py:652).** Playwright/Chromium ignore inline `user:pass@` credentials in the proxy server URL; they must be split into `username`/`password`. Since `_ProxyPool` entries are documented as `http://user:pass@host:port`, authenticated proxies will silently fail to authenticate on the browser rung — exactly the feature the user is asking for.

## 6. Windows, displays, memory, and concurrency realism

- **Windows:** curl_cffi (prebuilt wheels x64+ARM64), Patchright, Camoufox, SeleniumBase and Botasaurus all support Windows. Xvfb is Linux-only — and this matters *in HarvestKit's favour*: on employee laptops a real display already exists, so `headless=False` is available for free, which is the mode every stealth tool performs best in. The Xvfb discussion only applies if you later move runs to a Linux VPS.
- **Memory, per instance:** curl_cffi ~58 MB peak RSS in the published benchmark; Camoufox ~200 MB (its docs) — roughly 4x cheaper than Chromium's ~800 MB; Chromium-based rungs realistically 80–400 MB depending on contexts. One benchmark recorded a 13 GB peak RSS for Patchright over a long sweep, which reads like a context leak rather than a steady-state figure — if you adopt it, watch RSS across a long run, and the per-fetch `context.close()` already in transport.py:686 is the right instinct.
- **Disk:** `~/AppData/Local/ms-playwright` is **1.7 GB** on this machine; the Camoufox install is **959 MB**. Shipping both to every employee laptop is a ~2.7 GB endpoint footprint of third-party browser binaries outside your patch cycle.
- **Speed, measured here (warm, domcontentloaded only):** curl_cffi **0.68 s/page**, Patchright **1.09 s/page**. The real gap is far wider in production, because crawl.py adds a 6 s `wait_for_function` and the browser rung adds an 8 s `networkidle` wait — call it ~0.7 s vs 8–20 s per page once challenges and banners are included.
- **Concurrency for thousands of domains:** the HTTP rung scales to hundreds of concurrent requests on one box. The browser rung does not — practical ceilings are roughly 10 concurrent Chromium contexts on a laptop, ~30 Camoufox instances on an 8 GB server. So the browser rung must be a *minority path*: budget it to ~10–15% of domains, enforced by a counter, or a 5,000-domain run will not finish. The per-domain `TransportMemory` in transport.py:231 is exactly the right mechanism for this — make sure it caches on the **registrable domain**, with a TTL and a negative-result entry, so a domain pays the escalation once rather than once per page.

## 7. The routing decision, stated as a rule

Escalate on evidence, never speculatively:

- **Rung 0 — `requests`:** ATS APIs, JSON endpoints, robots/sitemaps, anything already returning 200.
- **Rung 1 — curl_cffi (default for company domains):** escalate here on 403/406/429, a TLS-level reset or timeout, or `cf-mitigated`/`x-datadome` headers. Recovers TLS-scored walls, which is most of them.
- **Rung 2 — stealth browser:** escalate only when the body proves JS is required — a challenge body that survived rung 1, or a 2xx whose extracted text is under ~500 chars / whose anchor count is under ~5 while the raw HTML is large (an SPA shell). Never escalate a clean 404 or a genuinely empty page; that just burns the browser budget on nothing.
- **Rung 3 (optional) — Camoufox:** only for domains where rung 2 is recorded as failed.
- **Cache the winning rung per registrable domain, with TTL and negative caching.**
- **And gate the whole ladder on egress:** if the exit IP's country does not match the target's, expect the tail to fail no matter which rung you are on.

One documentation fix falls straight out of this: docs/RUNBOOK-leads.md tells the operator "Anything other than 000 is fine - 301, 403 and 429 all mean the host is reachable." For a lead run, a 403 *is* the failure. The health check should assert a 2xx with real extractable text, per rung, or it will keep giving a green light to a run that harvests nothing.


---

EVIDENCE BASE. Everything below that is stated as a measurement was probed live from this machine on 2026-09-21 with plain `requests` and a Chrome UA. I did not modify any file.

THE STRATEGIC REFRAME. The lead famine is being treated as a blocking problem ("4 of 6 EU company domains serve a bot wall"), and the instinct is to answer it with proxies. But look at what the pipeline is actually trying to extract from those 403-ing pages: a person's name, their role, and an email. For Sweden, France, Norway, Switzerland and the UK, that exact data is published as structured JSON by the state, free, with no bot wall at all. HarvestKit is fighting Cloudflare for information that is sitting in an open register. Every proxy you add buys a percentage point against a WAF; every register you add buys a whole country. The ranking above reflects that: the top five recommendations need no proxy, no browser, no credential except one, and between them cover SE/DE/FR/NO plus the existing CH.

WHAT APOLLO, OUTSCRAPER, LUSHA AND COGNISM ACTUALLY DO — AND WHICH PARTS ARE LAWFUL IN THE EU. They do five things. (1) A register/firmographic base layer — exactly the sources ranked above, and the fully lawful part. (2) Waterfall enrichment: run a contact through many providers in sequence until one returns an email, then verify by SMTP probe. Lawful in mechanism; HarvestKit already does the tail of this in src/leadgen/email/. (3) Email permutation: generate first.last@domain, f.last@domain, etc. and probe which resolves. Lawful, and note src/leadgen/person/jobad.py's existing rule — only attribute an address to a person when the local part echoes their name — is a better precision discipline than most vendors apply. (4) Contributory networks: a browser extension or email plugin that harvests the user's own contacts and address book and pools them. This is the part that quietly powers the big databases, and it is the part with the worst GDPR standing, because the people in those address books never consented and are never told. (5) LinkedIn profile scraping. Not lawful in the EU as practised, and there is now a decision to point at: on 5 December 2024 the CNIL fined KASPR €240,000 (KASPR is owned by Cognism) for a Chrome extension that collected LinkedIn users' contact details — including details users had explicitly restricted the visibility of — holding ~160 million contacts, retaining them five years, and failing to inform data subjects (and from 2022 informing them only in English). The CNIL held that collecting details whose visibility the user had limited "exceeded what could reasonably be expected". LinkedIn separately removed Apollo.io's and Seamless.ai's pages over scraping. The operational conclusion for HarvestKit: methods 1–3 are available to you and are where the top five recommendations sit; methods 4 and 5 are how the incumbents got their volume and are the two you must not copy — and, usefully, not copying them is also HarvestKit's most credible differentiator in Europe, where "every row traceable to a named public source" is a sellable property that Apollo cannot claim.

GDPR ARTICLE 14 IS THE OBLIGATION THIS PRODUCT IS MOST LIKELY TO BREACH. Every person source in this list — registers included — delivers personal data obtained from someone other than the data subject. Art. 14 therefore requires notifying each person within one month of collection, naming the source and the purpose. It was a central ground in the KASPR decision. The Art. 14(5)(b) "disproportionate effort" exemption exists but regulators reject it when it is asserted as a blanket pass without a documented assessment, and CNIL has sanctioned several B2B vendors on exactly that point. Two concrete implications: (a) the per-row `source_url` that src/leadgen/person/hit.py already carries is not just an audit nicety, it is the field that makes an Art. 14 notice possible — never emit a lead without it; (b) the EDPB has named Articles 12–14 as its coordinated-enforcement topic for 2026, so this is the live risk area during exactly the period this product ships. Also honour the per-source suppression flags I found: `statut_diffusion` (France) and `protected` (Denmark) are the data subject's own opt-out, and ignoring them converts a defensible legitimate-interest position into an indefensible one.

BRIS — WHY IT IS NOT ON THE LIST. The Business Registers Interconnection System is the thing people expect to be the single EU-wide company API, and it is not one. BRIS connects member-state registers to a European Central Platform for cross-border checks; the public face is the e-justice portal search, which returned HTTP 403 to my probe, and there is no developer API or key. It also only carries basic harmonised existence data — no officers, no financials. There is a helpdesk (just-bris-helpdesk@ec.europa.eu) but no self-serve integration. Its sibling BORIS (beneficial-ownership interconnection) has been substantially curtailed since the CJEU struck down public access to beneficial-ownership registers in 2022. Go direct to national registers; that is what the top recommendations do.

SOURCES THAT LOOK FREE AND ARE NOT, OR LOOK ALIVE AND ARE NOT. Worth recording so nobody re-derives them: INSEE Sirene requires an account AND contains no dirigeants at all (401 unauthenticated; officers live in INPI's RNE) — recherche-entreprises.api.gouv.fr gives you the merged free result instead. Danish official distribution.virk.dk returns 401 and needs a signed agreement. Bolagsverket's older company-information API still charges monthly; only the "värdefulla datamängder" API is free. Jooble's API root sits behind a Cloudflare interstitial. crt.sh returned 502 on all three attempts including the documented ?output=json form — use Cert Spotter. OffeneRegister's German data has a last_change of 2019-02-01. handelsregister.de would not complete a TCP connection at all.

TWO DIRECT CORRECTIONS TO THE CODEBASE FROM THIS RESEARCH. First: configs/leads/eu-it.yaml declares, at length and as settled fact, that the Bundesagentur für Arbeit is unreachable and therefore sets `targets: []`. That was true of /pc/v4 and /pc/v5 (both 403 today) but /pc/v6/jobs returns HTTP 200 right now with `X-API-Key: jobboerse-jobsuche`. The largest job board in Germany is reachable and the config tells the operator it is not. Whether to use it is a separate decision — rest.arbeitsagentur.de/robots.txt returns 403, and the client is correct to refuse it until someone deliberately adds that host to the existing `robots_exempt_hosts` mechanism at src/leadgen/cli.py:297 — but the factual claim in the config should not stand as written. Second: six places in the tree (src/leadgen/person/jobad.py:4, name.py:107, roles.py:62, strategies/impressum.py:3, seed/atsboards.py:30, docs/RUNBOOK-leads.md:148) rest the DACH Impressum strategy on "§5 TMG". The Telemediengesetz was repealed on 14 May 2024 and replaced by the Digitale-Dienste-Gesetz; the obligation is now §5 DDG. The substance is unchanged and still requires the Vertretungsberechtigter to be named — so the strategy is as sound as ever and is, per the German recommendation above, still the best German officer source available — but the citation is to a repealed statute, which is a bad look in a document that argues its legal footing.

SUGGESTED ORDER OF WORK, BY YIELD PER UNIT OF EFFORT. (1) JobTech/Sweden — one new seed module, CC0, delivers named contacts with emails at ~27% of ads and kills the domain-resolution step for 65% of Swedish rows. Highest return of anything here. (2) EURES — one new seed module using the existing post_json helper, 2.07M vacancies across 31 countries, no key, replaces the keyword-translation approach with ESCO codes. (3) Brønnøysund roller — extends the register.py you already built for Switzerland to Norway, which you already target with four configs and three adapters, and needs no credentials where Zefix does. (4) recherche-entreprises.api.gouv.fr — same registry refactor, adds France with zero credentials. (5) The Arbeitsagentur v6 decision — largest German unlock, but make it an explicit, documented policy choice about that 403 robots.txt rather than a silent flag flip. Items 1–4 require no proxy, no browser automation, no paid service and no robots override, and together they address the lead famine in four countries by not fighting the bot walls at all.
