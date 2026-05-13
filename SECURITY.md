# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| 0.3.x | yes |
| < 0.3 | no |

## Reporting a Vulnerability

**Do not open a public issue.** Email reports to: hriday.vig@bluoryn.com
(or use [GitHub's private security advisory](https://github.com/BluOryn/HarvestKit/security/advisories/new)).

Include:
- A description of the vulnerability
- Steps to reproduce
- Affected version / commit
- Impact assessment (what an attacker could achieve)
- Suggested fix, if any

Expect acknowledgement within 48 hours and a status update within 7 days.

## In scope
- Command injection / path traversal in the CLI or extension build
- Credential leaks (API keys, OAuth tokens) in cache, logs, or CSV output
- XSS / content injection in the extension UI
- Supply-chain risks in dependencies
- Insecure default config that exposes credentials

## Out of scope
- Rate limits / WAF blocks on third-party sites we scrape
- Bot-detection bypass techniques (we won't accept these regardless of report)
- Issues that require an already-compromised local machine
- Findings from automated scanners without proof of exploitability

## Coordinated disclosure

We follow a 90-day disclosure window. After we ship a fix, we will:
1. Publish a security advisory with credit
2. Tag the patched release
3. Update CHANGELOG.md
