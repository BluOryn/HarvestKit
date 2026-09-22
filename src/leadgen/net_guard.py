"""Re-export of the SSRF guard, which now lives in `job_scraper`.

It moved because the transport layer needs it too: a URL that passes the guard
can still redirect into internal address space, and only the code performing
the fetch can check each hop. `leadgen` already depends on `job_scraper`, so
the implementation belongs on that side and this keeps every existing import
working.
"""

from __future__ import annotations

from job_scraper.net_guard import (  # noqa: F401
    ALLOWED_SCHEMES,
    BLOCKED_HOSTS,
    _is_public,
    _resolves_to_public,
    guard,
    is_safe_url,
)

__all__ = ["ALLOWED_SCHEMES", "BLOCKED_HOSTS", "guard", "is_safe_url"]
