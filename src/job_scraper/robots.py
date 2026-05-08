from typing import Dict
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser


class RobotsCache:
    def __init__(self) -> None:
        self._cache: Dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str, user_agent: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._cache.get(base)
        if parser is None:
            parser = RobotFileParser()
            parser.set_url(f"{base}/robots.txt")
            try:
                parser.read()
            except Exception:
                return True
            self._cache[base] = parser
        return parser.can_fetch(user_agent, url)
