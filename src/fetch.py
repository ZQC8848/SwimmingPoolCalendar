"""抓取 USC RecSports 页面。默认 curl UA 会被 Cloudflare 挡，必须伪装浏览器。"""
from __future__ import annotations

import time
import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(RuntimeError):
    pass


def _get(url: str, *, tries: int = 3, timeout: int = 30) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            # 429/5xx 值得退避重试；403 说明 UA 被识破，重试也没用
            if r.status_code in (429, 500, 502, 503, 504):
                last = FetchError(f"HTTP {r.status_code} from {url}")
            else:
                raise FetchError(f"HTTP {r.status_code} from {url}")
        except requests.RequestException as e:
            last = FetchError(f"{type(e).__name__}: {e}")
        if i < tries - 1:
            time.sleep(2 ** i * 2)
    raise last or FetchError(f"failed to fetch {url}")


def page_last_modified(wp_api: str) -> str | None:
    """走 WordPress REST API 拿 modified 时间戳，用于跳过无变化的运行。

    这是省钱的关键：页面没动就不下载正文、不调 LLM、不写日历。
    拿不到就返回 None，调用方应降级为"照常抓取"。
    """
    try:
        data = _get(wp_api, tries=2, timeout=15).json()
    except Exception:
        return None
    return data.get("modified_gmt") or data.get("modified")


def fetch_html(url: str) -> str:
    return _get(url).text
