from collections.abc import Iterable

from .models import JobListing
from .normalize import job_fingerprint


def dedupe_jobs(jobs: Iterable[JobListing]) -> list[JobListing]:
    seen: set[str] = set()
    unique: list[JobListing] = []
    for job in jobs:
        key = job_fingerprint(job)
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique
