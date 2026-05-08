from typing import Iterable, List, Set

from .models import JobListing
from .normalize import job_fingerprint


def dedupe_jobs(jobs: Iterable[JobListing]) -> List[JobListing]:
    seen: Set[str] = set()
    unique: List[JobListing] = []
    for job in jobs:
        key = job_fingerprint(job)
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique
