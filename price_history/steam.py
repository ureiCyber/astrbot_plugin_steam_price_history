"""Optional edition discovery from Steam's public storefront JSON.

These storefront endpoints are not a stable, supported Web API contract.
Failure is non-fatal; exact administrator mappings remain available.
"""
from __future__ import annotations
import asyncio
import html
import re
import time
from datetime import datetime, timezone
import aiohttp

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


class SteamStore:
    def __init__(self, session=None):
        self.session = session
        self.owned = session is None
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
                    return None
                raw = await response.json(content_type=None)
        if not isinstance(raw, dict):
            return None
        record = raw.get(str(ident), {})
        if not isinstance(record, dict):
            return None
        data = record.get('data') if record.get('success') else None
        if not isinstance(data, dict):
            data = None
        self.cache[key] = (time.time() + 86400, data)
        return data

    async def discover(self, appid: int, country: str) -> list[dict]:
        """Only named editions whose package contains this app and no unrelated game.

        Bundles, ordinary collections, commercial licenses and ambiguous packages
        are omitted rather than inferred from a fuzzy title search.
        """
        root = await self._get('appdetails', appid, country)
        if not root:
            return []
        result = []
        packages = root.get('packages', [])[:20]
        for packageid in packages:
            pkg = await self._get('packagedetails', packageid, country)
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
                info = await self._get('appdetails', ident, country)
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
