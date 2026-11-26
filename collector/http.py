"""재시도·백오프가 붙은 HTTP 세션. 429/503을 만나면 Retry-After를 지켜 자동 재시도."""
from __future__ import annotations

UA = "promo-collector/0.1 (+https://github.com/chocoding0615/promo-collector) non-commercial image sourcing"


def make_session():
    import requests
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:  # 매우 오래된 urllib3
        from requests.packages.urllib3.util.retry import Retry  # type: ignore
    s = requests.Session()
    retry = Retry(
        total=5, connect=3, read=3,
        backoff_factor=1.5,                       # 1.5s, 3s, 6s ... 대기
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": UA})
    return s
