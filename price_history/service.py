"""Persistent query orchestration and bounded, query-driven refresh scheduling."""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .itad import ITADError
from .query import parse_query, QueryError
from .rendering import render_history_chart, format_price_summary, format_candidates
from .storage import Store

UTC = timezone.utc


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def timestamp(value) -> float | None:
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def signature(deal):
    if deal is None:
        return None
    return (deal.get('price'), deal.get('regular'), deal.get('cut'))


class PriceService:
    def __init__(self, client, steam, store: Store, data_dir, config=None, clock=time.time):
        self.client, self.steam, self.store = client, steam, store
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config = config or {}
        self.clock = clock
        self.active_seconds = max(1, int(self.config.get('active_days', 3))) * 86400
        self.cache_seconds = max(1, int(self.config.get('cache_hours', 24))) * 3600
        self.flights = {}
        self.plot_lock = asyncio.Lock()
        self.refresh_lock = asyncio.Lock()
        self.daily_hour, self.daily_minute = self._daily_time(self.config.get('daily_time', '09:00'))
        self.mappings = self._mapping('edition_mappings')
        self.aliases = self._mapping('edition_aliases')
        if any(not isinstance(v, dict) for v in self.mappings.values()):
            raise ValueError('edition_mappings 中每个 AppID 的值必须是版本对象。')

    def _mapping(self, key):
        raw = self.config.get(key, {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except ValueError:
                raise ValueError(f'{key} 必须是有效 JSON 对象') from None
        if not isinstance(raw, dict):
            raise ValueError(f'{key} 必须是 JSON 对象')
        return raw

    @staticmethod
    def _daily_time(text):
        try:
            hour, minute = (int(i) for i in str(text).split(':'))
            if not 0 <= hour < 24 or not 0 <= minute < 60:
                raise ValueError
            return hour, minute
        except (ValueError, TypeError):
            raise ValueError('daily_time 必须是 HH:MM，例如 09:00（北京时间）') from None

    async def _once(self, key, factory):
        task = self.flights.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            self.flights[key] = task
            task.add_done_callback(lambda done: self.flights.pop(key, None))
        return await asyncio.shield(task)

    async def info(self, ident):
        cached = self.store.cache_get('info:' + ident, self.clock())
        if cached is not None:
            return cached
        info = await self.client.info(ident)
        self.store.cache_put('info:' + ident, info, self.clock() + 86400)
        return info

    async def resolve(self, query):
        key = f'resolve:{query.appid or query.title}'
        cached = self.store.cache_get(key, self.clock())
        if cached is None:
            if query.appid:
                item = await self.client.lookup_appid(query.appid)
                candidates = [item] if item else []
            else:
                candidates = await self.client.search(query.title)
            # Do not silently show bundles as games.
            candidates = [c for c in candidates if c.get('type') != 'bundle']
            if not candidates:
                raise QueryError('未找到该游戏，请检查名称或输入 Steam 商店链接。')
            cached = candidates
            self.store.cache_put(key, cached, self.clock() + 86400)
        return await self.info(cached[0]['id']), cached[1:]

    async def editions(self, base, country):
        appid = base.get('appid')
        if not appid:
            return [], ['数据源未提供 Steam AppID，无法自动核对购买版本。']
        warnings = []
        cachekey = f'editions:{appid}:{country}'
        found = self.store.cache_get(cachekey, self.clock())
        if found is None:
            try:
                found = await self.steam.discover(int(appid), country)
                self.store.cache_put(cachekey, found, self.clock() + 86400)
            except (TimeoutError, OSError, ValueError, ITADError):
                found = []
                warnings.append('Steam 版本识别暂时失败，本次仅显示已确认的数据。')
            except Exception as exc:
                # aiohttp errors should degrade edition enrichment, not base queries.
                if exc.__class__.__module__.startswith('aiohttp'):
                    found = []
                    warnings.append('Steam 版本识别暂时失败，本次仅显示已确认的数据。')
                else:
                    raise
        found = [dict(row) for row in found]
        # Exact administrator-verified relation, never a fuzzy title equivalence.
        for edition, entry in self.mappings.get(str(appid), {}).items():
            if not isinstance(entry, dict):
                continue
            shop_id = entry.get('shop_id', '')
            if not shop_id.startswith(('app/', 'sub/')):
                continue
            found = [f for f in found if f['edition'] != edition]
            found.append({'edition': edition, 'title': entry.get('title', edition),
                'shop_id': shop_id, 'upgrade': bool(entry.get('upgrade')), 'current': None})
        if found:
            map_key = 'edition-map:' + hashlib.sha256(json.dumps(sorted(row['shop_id'] for row in found)).encode()).hexdigest()
            try:
                ids = self.store.cache_get(map_key, self.clock())
                if ids is None:
                    ids = await self.client.lookup_shop_ids([row['shop_id'] for row in found])
                    self.store.cache_put(map_key, ids, self.clock() + 86400)
            except ITADError:
                ids = {}
                warnings.append('版本历史映射暂不可用，仅显示已核实的 Steam 购买项现价。')
            for row in found:
                row['id'] = ids.get(row['shop_id'])
            containers = {}
            for row in found:
                if row.get('id'):
                    containers.setdefault(row['id'], set()).add(row['shop_id'])
            for row in found:
                if row.get('id') == base['id'] or len(containers.get(row.get('id'), set())) > 1:
                    row['id'] = None
                    row['history_note'] = '数据源将不同购买项合并，不能作为独立版本历史。'
        # Collapse exact same shop ID only. Two different products sharing a label
        # remain ambiguous and are never chosen by price or fuzzy name.
        unique = {}
        for row in found:
            unique.setdefault(row['shop_id'], row)
        return list(unique.values()), warnings

    async def refresh(self, ident, country, *, touch=False, force=False, current_marker=False, current=None):
        async def action():
            now = self.clock()
            state = self.store.get(ident, country)
            old_query = state.get('last_query', 0)
            reactivated = touch and now - old_query > self.active_seconds and bool(state)
            if touch:
                state['last_query'] = now
            stale = now - state.get('checked_at', 0) >= self.cache_seconds
            expiry = timestamp((state.get('current') or {}).get('expiry'))
            expiry_due = expiry is not None and now >= expiry and state.get('expiry_checked') != expiry
            retry_due = state.get('pending') and now >= state.get('retry_at', 0)
            need_current = force or stale or reactivated or expiry_due or not state.get('checked_at')
            error = None
            changed = False
            if (need_current and now >= state.get('current_retry_at', 0)) or current_marker:
                try:
                    fresh = current if current_marker else await self.client.prices(ident, country)
                    changed = signature(fresh) != signature(state.get('current'))
                    state['current'] = fresh
                    state['checked_at'] = now
                    if expiry_due:
                        state['expiry_checked'] = expiry
                    state.pop('current_error', None)
                    state.pop('current_retry_at', None)
                except ITADError as exc:
                    error = str(exc)
                    state['current_error'] = error
                    state['current_retry_at'] = now + max(300, getattr(exc, 'retry_after', 0) or 0)
            weekly_due = now - state.get('history_checked_at', 0) >= 7 * 86400
            needs_history = not state.get('initialized') or changed or reactivated or weekly_due or retry_due
            history_allowed = not state.get('pending') or retry_due or changed or reactivated
            if needs_history and history_allowed and error is None:
                try:
                    since = None
                    if state.get('initialized'):
                        anchor = state.get('pending_since', state.get('history_checked_at', now))
                        since = iso(max(0, anchor - 48 * 3600))
                    entries = (await self.client.history(ident, country, since=since)
                        if state.get('initialized') else await self.client.history_full(ident, country))
                    for row in entries:
                        parsed = timestamp(row.get('timestamp'))
                        if parsed is None:
                            raise ITADError('历史数据时间格式无效')
                        row['timestamp'] = iso(parsed)
                    self.store.merge_history(ident, country, entries)
                    previous_history_check = state.get('history_checked_at', now)
                    state['initialized'] = True
                    state['history_checked_at'] = now
                    rows = self.store.history(ident, country)
                    last = rows[-1].get('deal') if rows else None
                    pending = signature(last) != signature(state.get('current'))
                    state['pending'] = pending
                    if pending:
                        state.setdefault('pending_since', previous_history_check)
                        state['retry_at'] = now + 3600
                    else:
                        state.pop('pending_since', None)
                        state.pop('retry_at', None)
                    state.pop('history_error', None)
                except ITADError as exc:
                    state['pending'] = True
                    state.setdefault('pending_since', state.get('history_checked_at', now))
                    state['retry_at'] = now + 3600
                    state['history_error'] = str(exc)
            self.store.put(ident, country, state)
            return state
        result = await self._once(('refresh', ident, country), action)
        if touch:
            result = self.store.get(ident, country)
            result['last_query'] = self.clock()
            self.store.put(ident, country, result)
        return result

    @staticmethod
    def lowest(state, rows):
        current = state.get('current') or {}
        low = current.get('storeLow')
        if (low and low.get('amount') is not None
            and low.get('currency') == (current.get('price') or {}).get('currency')):
            matched = next((r for r in reversed(rows) if (r.get('deal') or {}).get('price') == low), None)
            result = {'price': low, 'shop': {'id': 61, 'name': 'Steam'}}
            if matched:
                result.update({'cut': matched['deal'].get('cut'), 'regular': matched['deal'].get('regular')})
            return result
        # No all-time marker from current API: historical fallback is explicitly
        # limited to the records ITAD supplied, not invented global coverage.
        currency = (current.get('price') or {}).get('currency')
        if not currency:
            currency = next(((r.get('deal') or {}).get('price', {}).get('currency')
                for r in reversed(rows) if (r.get('deal') or {}).get('price')), None)
        valid = [r for r in rows if (r.get('deal') or {}).get('price') is not None
                 and (not currency or r['deal']['price'].get('currency') == currency)]
        if not valid:
            return None
        winner = min(valid, key=lambda r: r['deal']['price']['amount'])
        return {'price': winner['deal']['price'], 'timestamp': winner['timestamp'],
            'cut': winner['deal'].get('cut'), 'regular': winner['deal'].get('regular'), 'shop': {'name': 'Steam'}}

    async def query(self, text):
        query = parse_query(text, edition_aliases=self.aliases)
        return await self._once(('query', query.country, query.edition, query.title), lambda: self._query(query))

    async def _query(self, query):
        if not self.store.cache_get('country:' + query.country, self.clock()):
            await self.client.validate_country(query.country)
            self.store.cache_put('country:' + query.country, True, self.clock() + 7 * 86400)
        base, candidates = await self.resolve(query)
        editions, warnings = await self.editions(base, query.country)
        selected = {'id': base['id'], 'title': base['title'], 'edition': 'standard'}
        if query.edition != 'standard':
            matches = [r for r in editions if r['edition'] == query.edition]
            if not matches:
                raise QueryError('未找到已确认的该版本；不会自动改查本体。可用版本：' +
                    ('、'.join(r['edition'] for r in editions) or '仅本体（也可能是数据源未收录该版本）'))
            if len(matches) > 1:
                raise QueryError('该版本存在多个不同购买项，请通过管理员精确版本映射消除歧义。')
            selected = matches[0]
        if selected.get('id'):
            state = await self.refresh(selected['id'], query.country, touch=True)
            rows = self.store.history(selected['id'], query.country)
        else:
            state = {'current': selected.get('current'),
                'checked_at': timestamp((selected.get('current') or {}).get('timestamp'))}
            rows = []
            warnings.append(selected.get('history_note') or '该购买版本暂无对应 ITAD 历史数据，无法生成曲线。')
        if state.get('current_error'):
            warnings.append(state['current_error'])
        if state.get('history_error'):
            warnings.append(state['history_error'])
        if state.get('pending'):
            warnings.append('历史记录尚未追上当前价格，已保留待补齐状态；曲线不虚构最新数据。')
        if not rows:
            warnings.append('暂无历史数据，不能生成价格曲线。')
        if selected.get('upgrade'):
            warnings.append('升级内容，需要本体。')
        versions = []
        display_editions = list(editions)
        if query.edition != 'standard':
            display_editions.insert(0, {'id': base['id'], 'title': base['title'], 'edition': 'standard'})
        for item in display_editions:
            if ((item.get('id') and item.get('id') == selected.get('id'))
                or (item.get('shop_id') and item.get('shop_id') == selected.get('shop_id'))):
                continue
            version_state = await self.refresh(item['id'], query.country, touch=True) if item.get('id') else {}
            deal = version_state.get('current') or item.get('current') or {}
            versions.append({'title': item['title'] + ('（升级内容，需要本体）' if item.get('upgrade') else ''),
                'edition': item['edition'], 'price': deal.get('price'), 'cut': deal.get('cut'),
                'lowest': self.lowest(version_state, self.store.history(item['id'], query.country)) if item.get('id') else None})
        rating = next((r.get('score') for r in base.get('reviews', [])
            if str(r.get('source', '')).casefold() == 'steam'), None)
        store_url = ((selected.get('current') or {}).get('url') if not selected.get('id') else None)
        if not store_url and base.get('appid'):
            store_url = f"https://store.steampowered.com/{selected.get('shop_id') or ('app/' + str(base['appid']))}/"
        summary = {'title': selected['title'], 'country': query.country, 'edition': query.edition,
            'rating': rating, 'store_url': store_url,
            'current': state.get('current'), 'lowest': self.lowest(state, rows), 'versions': versions,
            'fetched_at': iso(state['checked_at']) if state.get('checked_at') else None,
            'stale': bool(state.get('current_error')), 'warnings': warnings}
        message = format_price_summary(summary)
        extra = format_candidates(candidates) if candidates else ''
        if extra:
            message += '\n\n' + extra
        chart = None
        if rows:
            cover = (await self.steam.cover(int(base['appid']), query.country, self.directory / 'covers')
                if base.get('appid') and hasattr(self.steam, 'cover') else None)
            cover_stamp = cover.stat().st_mtime_ns if cover else None
            identity = json.dumps([rows, selected['title'], query.country, iso(self.clock())[:10],
                cover_stamp, self.config.get('chart_fonts', '')], sort_keys=True)
            digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
            chart = self.directory / f'chart_{digest}.png'
            async with self.plot_lock:
                if not chart.exists():
                    await asyncio.to_thread(render_history_chart, rows, chart, title=selected['title'],
                        country=query.country, now=datetime.fromtimestamp(self.clock(), UTC),
                        font_families=[s.strip() for s in self.config.get('chart_fonts', '').split(',') if s.strip()] or None,
                        background_path=cover)
        return chart, message

    async def tick(self):
        """Run per minute. Poll only active products; daily batch + expiry/retry.

        This only refreshes caches and never sends unsolicited bot messages.
        """
        async with self.refresh_lock:
            now = self.clock()
            local = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai'))
            day = local.date().isoformat()
            active = [(g, c, s) for g, c, s in self.store.all_states()
                      if now - s.get('last_query', 0) <= self.active_seconds]
            daily_done = self.store.cache_get('daily-run', now)
            daily_due = ((local.hour, local.minute) >= (self.daily_hour, self.daily_minute)
                and daily_done != day and not self.store.cache_get('daily-backoff', now))
            if daily_due:
                by_country = {}
                for ident, country, state in active:
                    by_country.setdefault(country, []).append(ident)
                success = True
                for country, ids in by_country.items():
                    try:
                        prices = await self.client.prices_many(ids, country)
                        for ident in ids:
                            await self.refresh(ident, country, current_marker=True, current=prices.get(ident))
                    except ITADError:
                        success = False
                if not success:
                    self.store.cache_put('daily-backoff', True, now + 3600)
                if success:
                    self.store.cache_put('daily-run', day, now + 2 * 86400)
            for ident, country, state in active:
                expiry = timestamp((state.get('current') or {}).get('expiry'))
                due = state.get('pending') and now >= state.get('retry_at', 0)
                due = due or (state.get('current_error') and now >= state.get('current_retry_at', 0))
                due = due or (expiry is not None and expiry <= now and state.get('expiry_checked') != expiry)
                due = due or now - state.get('history_checked_at', 0) >= 7 * 86400
                if due:
                    await self.refresh(ident, country)
            # Images are disposable; historical data is not. Bound disk usage.
            for path in self.directory.glob('chart_*.png'):
                if now - path.stat().st_mtime > 7 * 86400:
                    path.unlink(missing_ok=True)

    async def close(self):
        tasks = list(self.flights.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()
        await self.steam.close()
        self.store.close()
