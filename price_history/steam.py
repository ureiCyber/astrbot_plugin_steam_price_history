"""Optional edition discovery from Steam's public storefront JSON.

These storefront endpoints are not a stable, supported Web API contract.
Failure is non-fatal; exact administrator mappings remain available.
"""
from __future__ import annotations
import asyncio
import html
import logging
import math
from email.utils import parsedate_to_datetime
import re
import time
from datetime import datetime, timezone
import aiohttp

_NAME_LOGGER = logging.getLogger('astrbot_plugin_steam_price_history.name_lookup')


def _log_name_failure(stage: str, exc: Exception, diagnostic_logger=None) -> None:
    """Log only allowlisted metadata, never exception text or traceback data."""
    error_type = type(exc).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_type):
        error_type = 'Exception'
    status = getattr(exc, 'status', None)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        status = 'unknown'
    target = diagnostic_logger if diagnostic_logger is not None else _NAME_LOGGER
    # AstrBot's logger also feeds its WebUI. Pass only the preformatted safe
    # string, and do not duplicate it through the standalone logger.
    target.warning(f'Name lookup failed: stage={stage} error={error_type} status={status}')


class SteamHTTPError(OSError):
    """A safe HTTP failure that retains its status without request/response data."""
    def __init__(self, status: int):
        super().__init__('Steam 服务暂不可用。')
        self.status = status


class SteamResponseError(ValueError):
    """A safe schema failure, without the upstream response body."""
    def __init__(self):
        super().__init__('Steam 名称搜索响应无效。')
        self.status = 200


MARKERS = {
    'deluxe': ('deluxe', '豪华'), 'gold': ('gold', '黄金'),
    'ultimate': ('ultimate', '终极'), 'premium': ('premium', '高级'),
    'complete': ('complete edition', '完整版'), 'vault': ('vault', '金库'),
    'definitive': ('definitive', '决定版'), 'collectors': ('collector', '典藏'),
    'goty': ('game of the year', 'goty', '年度'), 'anniversary': ('anniversary', '周年'),
}


def edition_kind(title: str) -> str | None:
    lower = title.casefold()
    for kind, names in MARKERS.items():
        if any(n in lower for n in names):
            return kind
    return None


class SearchResults(list):
    """Verified results, with a signal not to long-cache partial failures."""
    def __init__(self, rows, incomplete=False):
        super().__init__(rows)
        self.incomplete = incomplete


class SteamStore:
    def __init__(self, session=None, diagnostic_logger=None):
        self.session = session
        self.owned = session is None
        self.diagnostic_logger = diagnostic_logger
        self.cache = {}
        self.sem = asyncio.Semaphore(3)

    async def _get(self, endpoint, ident, country):
        key = (endpoint, str(ident), country)
        cached = self.cache.get(key)
        if cached and cached[0] > time.time():
            return cached[1]
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        param = 'appids' if endpoint == 'appdetails' else 'packageids'
        async with self.sem:
            async with self.session.get('https://store.steampowered.com/api/' + endpoint,
                params={param: str(ident), 'cc': country, 'l': 'schinese'}) as response:
                if response.status != 200:
                    raise SteamHTTPError(response.status)
                raw = await response.json(content_type=None)
        if not isinstance(raw, dict):
            return None
        record = raw.get(str(ident), {})
        if not isinstance(record, dict):
            return None
        data = record.get('data') if record.get('success') else None
        if not isinstance(data, dict):
            data = None
        if data is not None:
            self.cache[key] = (time.time() + 86400, data)
        return data

    async def _optional_get(self, endpoint, ident, country):
        # Edition discovery historically skips unavailable HTTP responses.
        # Keep that behavior while name/type lookups retain status diagnostics.
        try:
            return await self._get(endpoint, ident, country)
        except SteamHTTPError:
            return None

    async def app_details(self, appid: int):
        """Identity/type lookup; prices from this response are not consumed."""
        return await self._get('appdetails', appid, 'CN')

    async def search(self, title: str) -> list[dict]:
        """Localized name discovery only. Never use search prices as region prices."""
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        # Do not retry 429 in a tight loop or hammer Steam after a rate limit.
        if getattr(self, 'search_retry_at', 0) > time.time():
            raise SteamHTTPError(429)
        async with self.sem:
            async with self.session.get('https://store.steampowered.com/api/storesearch/',
                params={'term': title, 'l': 'schinese', 'cc': 'CN'}) as response:
                if response.status == 429:
                    value = response.headers.get('Retry-After', '60')
                    try:
                        retry_after = float(value)
                        if not math.isfinite(retry_after):
                            raise ValueError()
                    except (ValueError, TypeError):
                        try:
                            retry_after = parsedate_to_datetime(value).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            retry_after = 60
                    self.search_retry_at = time.time() + max(60, retry_after)
                if response.status != 200:
                    raise SteamHTTPError(response.status)
                raw = await response.json(content_type=None)
        if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
            raise SteamResponseError()
        results, seen, failed = [], set(), False
        for item in raw['items'][:8]:
            if not isinstance(item, dict) or item.get('type') != 'app':
                continue
            appid = item.get('id')
            if isinstance(appid, bool) or not str(appid).isdecimal() or int(appid) <= 0:
                continue
            appid = int(appid)
            if appid in seen:
                continue
            seen.add(appid)
            # Store search type=app includes demos/software. Verify actual type.
            try:
                details = await self._get('appdetails', appid, 'CN')
            except (OSError, TimeoutError, ValueError, aiohttp.ClientError) as exc:
                _log_name_failure('steam_type_verify', exc, self.diagnostic_logger)
                failed = True
                continue
            if not details:
                _log_name_failure('steam_type_verify', OSError('Steam 条目核验暂不可用。'),
                                  self.diagnostic_logger)
                failed = True
                continue
            if details.get('type') not in {'game', 'dlc'}:
                continue
            results.append({'appid': appid, 'title': details.get('name') or item.get('name', ''),
                            'type': details['type']})
        if failed and not results:
            raise OSError('Steam 搜索条目核验暂不可用，请稍后重试。')
        return SearchResults(results, incomplete=failed)

    async def discover(self, appid: int, country: str) -> list[dict]:
        """Only named editions whose package contains this app and no unrelated game.

        Bundles, ordinary collections, commercial licenses and ambiguous packages
        are omitted rather than inferred from a fuzzy title search.
        """
        root = await self._optional_get('appdetails', appid, country)
        if not root:
            return []
        result = []
        packages = root.get('packages', [])[:20]
        for packageid in packages:
            pkg = await self._optional_get('packagedetails', packageid, country)
            if not pkg:
                continue
            name = html.unescape(re.sub('<[^>]+>', '', pkg.get('name', '')))
            kind = edition_kind(name)
            if not kind or any(s in name.casefold() for s in ('bundle', 'collection', 'commercial license')):
                continue
            apps = pkg.get('apps', [])
            ids = [int(a['id']) for a in apps if a.get('id')]
            if not ids or len(ids) > 30:
                continue
            contains_base = appid in ids
            valid = True
            for ident in ids:
                if ident == appid:
                    continue
                info = await self._optional_get('appdetails', ident, country)
                if not info or info.get('type') != 'dlc' or int(info.get('fullgame', {}).get('appid', 0)) != appid:
                    valid = False
                    break
            if not valid:
                continue
            # A DLC-only package is only accepted as an upgrade when Steam says so.
            upgrade = not contains_base
            if upgrade and not any(x in name.casefold() for x in ('upgrade', '升级')):
                continue
            price = pkg.get('price') or {}
            current = None
            if price.get('currency') and price.get('final') is not None:
                current = {'shop': {'id': 61, 'name': 'Steam'},
                    'price': {'amount': price['final'] / 100, 'currency': price['currency']},
                    'regular': {'amount': price.get('initial', price['final']) / 100, 'currency': price['currency']},
                    'cut': price.get('discount_percent', 0),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'url': f'https://store.steampowered.com/sub/{packageid}/'}
            result.append({'edition': kind + ('-upgrade' if upgrade else ''), 'title': name,
                'shop_id': f'sub/{packageid}', 'upgrade': upgrade, 'current': current})
        return result

    async def close(self):
        if self.owned and self.session:
            await self.session.close()

    async def cover(self, appid: int, country: str, directory):
        """Download only a bounded cover from a verified Steam CDN URL.

        No arbitrary URL is accepted from commands or configuration; redirects
        are disabled. Covers are disposable, local cache files, never published.
        """
        from pathlib import Path
        from urllib.parse import urlparse
        import io
        from PIL import Image, UnidentifiedImageError
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'{int(appid)}.jpg'
        if target.exists() and time.time() - target.stat().st_mtime < 7 * 86400:
            return target
        try:
            app = await self._get('appdetails', appid, country)
            url = (app or {}).get('header_image', '')
            parsed = urlparse(url)
            if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.steamstatic.com')
                or parsed.username or parsed.password or parsed.port not in (None,443)
                or f'/apps/{int(appid)}/' not in parsed.path):
                return target if target.exists() else None
            async with self.sem:
                async with self.session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        return target if target.exists() else None
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > 2 * 1024 * 1024:
                            return target if target.exists() else None
            def save():
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.width > 4096 or picture.height > 4096:
                        return False
                    picture.load()
                    picture = picture.convert('RGB')
                    picture.thumbnail((1600, 1600))
                    temp = target.with_suffix('.tmp.jpg')
                    picture.save(temp, format='JPEG', quality=85)
                    temp.replace(target)
                    return True
            if await asyncio.to_thread(save):
                return target
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            pass
        return target if target.exists() else None
