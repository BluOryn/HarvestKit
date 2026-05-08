from abc import ABC, abstractmethod
from typing import List

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing


class BaseAdapter(ABC):
    @abstractmethod
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        raise NotImplementedError
