"""Small, independent client for the documented IsThereAnyDeal API.

Reference: https://docs.isthereanydeal.com/ (API 2.11, checked 2026-10-20).
Only public, stable endpoints are used. Responses retain the provider's monetary
values, currencies, timestamps and URLs. No credential is placed in a URL.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Sequence
from uuid import UUID

import aiohttp

API_BASE = "https://api.isthereanydeal.com"
STEAM_SHOP_ID = 61
HISTORY_START = "1970-01-01T00:00:00Z"
_SHOP_ID = re.compile(r"(?:app|sub|bundle)/[1-9][0-9]*\Z")


class ITADError(Exception):
    """Safe to show or log: never contains a URL, key, body or upstream error."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ITADAuthError(ITADError):
    """Missing, invalid or unauthorized API key."""


class ITADRateLimitError(ITADError):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("ITAD 请求频率受限，请稍后重试。", status=429)
        self.retry_after = retry_after


class ITADNotFoundError(ITADError):
    """The requested ITAD game does not exist."""


class ITADRegionError(ITADError):
    """The country is invalid or Steam coverage could not be verified."""


class ITADResponseError(ITADError):
    """An invalid or unexpectedly shaped upstream response."""


def normalize_country(country: str) -> str:
    """Normalize syntax only; call validate_country to probe Steam coverage.

    The public API has no country-list endpoint. Never silently substitute US,
    convert prices, or assume that the response currency follows a fixed map.
    """
    if not isinstance(country, str):
        raise ITADRegionError("请使用两位国家代码，例如 CN 或 US。")
    normalized = country.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", normalized):
        raise ITADRegionError("请使用两位国家代码，例如 CN 或 US。")
    return normalized


def _game_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("ITAD 游戏 ID 必须是有效的 UUID。") from None


def _since(value: datetime | str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError):
        raise ValueError("历史起始时间必须是含时区的 ISO 8601 日期时间。") from None


def _list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ITADResponseError("ITAD 返回的数据格式异常。")
    return payload


def _game(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("title"), str):
        raise ITADResponseError("ITAD 返回的游戏信息无效。")
    try:
        _game_id(payload.get("id"))
    except ValueError:
        raise ITADResponseError("ITAD 返回的游戏信息无效。") from None
    return payload


def _is_steam(item: dict[str, Any]) -> bool:
    shop = item.get("shop")
    if not isinstance(shop, dict) or not isinstance(shop.get("id"), int):
        raise ITADResponseError("ITAD 返回的商店记录无效。")
    return shop["id"] == STEAM_SHOP_ID


def _reject_constant(_: str) -> None:
    raise ValueError("Non-finite JSON number")


def _check_deal(deal: dict[str, Any]) -> None:
    """Reject malformed money rather than turning missing/invalid data into zero."""
    for key in ("price", "regular"):
        money = deal.get(key)
        if not isinstance(money, dict):
            raise ITADResponseError("ITAD 返回的价格记录无效。")
        amount, amount_int, currency = (money.get(k) for k in ("amount", "amountInt", "currency"))
        if (isinstance(amount, bool) or not isinstance(amount, (int, float))
                or not math.isfinite(amount) or amount < 0
                or isinstance(amount_int, bool) or not isinstance(amount_int, int) or amount_int < 0
                or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
            raise ITADResponseError("ITAD 返回的金额或币种无效。")
    cut = deal.get("cut")
    if isinstance(cut, bool) or not isinstance(cut, (int, float)) or not math.isfinite(cut) or not 0 <= cut <= 100:
        raise ITADResponseError("ITAD 返回的折扣比例无效。")


class ITADClient:
    """Asynchronous client with reusable connections and bounded retries.

    A supplied session stays owned by its caller. Otherwise a session is created
    lazily and must be closed via close() or async with. Redirects are disabled
    so a custom API-key header cannot be forwarded to another host. max_retries
    counts retries after the initial attempt. A Retry-After exceeding
    max_retry_after is surfaced, never truncated into an early retry.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20.0,
        concurrency: int = 4,
        max_retries: int = 2,
        max_retry_after: float = 30.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ITADAuthError("请先在插件配置中填写 ITAD API Key。")
        if any(char in api_key for char in "\r\n"):
            raise ITADAuthError("ITAD API Key 格式无效，请检查配置。")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("请求超时必须是有限的正数。")
        if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
            raise ValueError("并发数必须是正整数。")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or not 0 <= max_retries <= 5:
            raise ValueError("重试次数必须是 0 至 5 的整数。")
        if not math.isfinite(max_retry_after) or max_retry_after < 0:
            raise ValueError("最大重试等待时间必须是有限的非负数。")
        if not isinstance(max_response_bytes, int) or max_response_bytes < 1:
            raise ValueError("响应大小限制必须是正整数。")
        self._api_key = api_key.strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries
        self._max_retry_after = max_retry_after
        self._max_response_bytes = max_response_bytes
        self._session = session
        self._owns_session = session is None
        self._closed = False
        self._cooldown_until = 0.0
        self._sleep = asyncio.sleep
        self._monotonic = time.monotonic
        self._validated_countries: set[str] = set()

    async def __aenter__(self) -> ITADClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise ITADError("ITAD 客户端已关闭。")
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        if self._session.closed:
            raise ITADError("ITAD 网络会话已关闭。")
        return self._session

    @staticmethod
    def _retry_after(value: str | None, fallback: float) -> float:
        if value:
            try:
                seconds = float(value)
                if math.isfinite(seconds) and seconds >= 0:
                    return seconds
            except ValueError:
                pass
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        return fallback

    async def _cooldown(self) -> None:
        while (remaining := self._cooldown_until - self._monotonic()) > 0:
            if remaining > self._max_retry_after:
                raise ITADRateLimitError(remaining)
            await self._sleep(remaining)

    async def _read_json(self, response: aiohttp.ClientResponse) -> Any:
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) > self._max_response_bytes:
                raise ITADResponseError("ITAD 响应过大，本次未保存不完整历史。")
        try:
            return json.loads(body, parse_constant=_reject_constant)
        except (ValueError, UnicodeError):
            raise ITADResponseError("ITAD 返回的 JSON 数据无效。") from None

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        session = self._get_session()
        for attempt in range(self._max_retries + 1):
            delay = 0.0
            try:
                async with self._semaphore:
                    await self._cooldown()
                    async with session.request(
                        method,
                        API_BASE + path,
                        params=params,
                        json=body,
                        headers={"ITAD-API-Key": self._api_key, "Accept": "application/json"},
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as response:
                        status = response.status
                        if status == 429:
                            wait = self._retry_after(response.headers.get("Retry-After"), 2.0**attempt)
                            self._cooldown_until = max(self._cooldown_until, self._monotonic() + wait)
                            if attempt == self._max_retries or wait > self._max_retry_after:
                                raise ITADRateLimitError(wait)
                        elif status in (401, 403):
                            raise ITADAuthError("ITAD API Key 无效或权限不足，请检查配置。", status=status)
                        elif status == 404:
                            raise ITADNotFoundError("ITAD 未找到该游戏。", status=status)
                        elif status in (500, 502, 503, 504):
                            if attempt == self._max_retries:
                                raise ITADError("ITAD 服务暂时不可用，请稍后重试。", status=status)
                            delay = min(2.0**attempt, self._max_retry_after)
                        elif not 200 <= status < 300:
                            raise ITADError("ITAD 拒绝了本次请求，请检查查询参数。", status=status)
                        else:
                            return await self._read_json(response)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                # Do not expose aiohttp exceptions, request headers, response
                # bodies or exception chaining: they can contain credentials.
                if attempt == self._max_retries:
                    raise ITADError("无法连接 ITAD，请检查网络后重试。") from None
                delay = min(2.0**attempt, self._max_retry_after)
            if delay > 0:
                await self._sleep(delay)
        raise ITADError("ITAD 请求失败。")  # Defensive; loop always returns or raises.

    async def search(self, title: str, results: int = 8) -> list[dict[str, Any]]:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("游戏名称不能为空。")
        if not isinstance(results, int) or isinstance(results, bool) or not 1 <= results <= 100:
            raise ValueError("搜索结果数量必须是 1 至 100 的整数。")
        payload = await self._request("GET", "/games/search/v1", params={"title": title.strip(), "results": results})
        return [_game(item) for item in _list(payload)]

    async def lookup_appid(self, appid: int | str) -> dict[str, Any] | None:
        if isinstance(appid, bool) or not re.fullmatch(r"[1-9][0-9]*", str(appid)):
            raise ValueError("Steam AppID 必须是正整数。")
        payload = await self._request("GET", "/games/lookup/v1", params={"appid": str(appid)})
        if not isinstance(payload, dict) or not isinstance(payload.get("found"), bool):
            raise ITADResponseError("ITAD 返回的查询结果无效。")
        return _game(payload.get("game")) if payload["found"] else None

    async def lookup_shop_ids(self, shop_ids: Sequence[str]) -> dict[str, str | None]:
        """Resolve exact Steam app/sub/bundle identifiers, never fuzzy editions."""
        ids = list(dict.fromkeys(shop_ids))
        if any(not isinstance(value, str) or not _SHOP_ID.fullmatch(value) for value in ids):
            raise ValueError("Steam 标识格式应为 app/123、sub/123 或 bundle/123。")
        result: dict[str, str | None] = {value: None for value in ids}
        for offset in range(0, len(ids), 200):
            batch = ids[offset : offset + 200]
            payload = await self._request("POST", "/lookup/id/shop/61/v1", body=batch)
            if not isinstance(payload, dict):
                raise ITADResponseError("ITAD 返回的游戏标识映射无效。")
            for value in batch:
                mapped = payload.get(value)
                if mapped is not None:
                    try:
                        _game_id(mapped)
                    except ValueError:
                        raise ITADResponseError("ITAD 返回的游戏标识映射无效。") from None
                result[value] = mapped
        return result

    async def shop_ids(self, game_ids: Sequence[str]) -> dict[str, list[str]]:
        """Return exact Steam product IDs assigned to each ITAD game container."""
        ids = list(dict.fromkeys(_game_id(value) for value in game_ids))
        result: dict[str, list[str]] = {value: [] for value in ids}
        for offset in range(0, len(ids), 200):
            batch = ids[offset : offset + 200]
            payload = await self._request("POST", "/lookup/shop/61/id/v1", body=batch)
            if not isinstance(payload, dict):
                raise ITADResponseError("ITAD 返回的商店标识映射无效。")
            for value in batch:
                mapped = payload.get(value, [])
                if not isinstance(mapped, list) or any(not isinstance(item, str) or not _SHOP_ID.fullmatch(item) for item in mapped):
                    raise ITADResponseError("ITAD 返回的商店标识映射无效。")
                result[value] = mapped
        return result

    async def info(self, game_id: str) -> dict[str, Any]:
        ident = _game_id(game_id)
        game = _game(await self._request("GET", "/games/info/v2", params={"id": ident}))
        if _game_id(game["id"]) != ident:
            raise ITADResponseError("ITAD 返回了不匹配的游戏信息。")
        return game

    async def prices(self, game_id: str, country: str) -> dict[str, Any] | None:
        """Return only the raw Steam deal, not any all-store historical low."""
        normalized_id = _game_id(game_id)
        return (await self.prices_many([normalized_id], country))[normalized_id]

    async def prices_many(self, game_ids: Sequence[str], country: str) -> dict[str, dict[str, Any] | None]:
        ids = list(dict.fromkeys(_game_id(value) for value in game_ids))
        region = normalize_country(country)
        result: dict[str, dict[str, Any] | None] = {value: None for value in ids}
        for offset in range(0, len(ids), 200):
            batch = ids[offset : offset + 200]
            payload = await self._request(
                "POST", "/games/prices/v3",
                params={"country": region, "shops": "61", "deals": "false", "vouchers": "false"},
                body=batch,
            )
            for row in _list(payload):
                if row.get("id") not in batch:
                    raise ITADResponseError("ITAD 价格响应包含不匹配的游戏。")
                deals = _list(row.get("deals"))
                steam_deals = [deal for deal in deals if _is_steam(deal)]
                for deal in steam_deals:
                    _check_deal(deal)
                # API's chosen price is preserved; do not compare currencies or
                # substitute another retailer that merely sells a Steam key.
                result[row["id"]] = steam_deals[0] if steam_deals else None
        return result

    async def history(
        self, game_id: str, country: str, since: datetime | str | None = None
    ) -> list[dict[str, Any]]:
        """Raw Steam change-log entries, including deal=None availability gaps.

        The documented v2 endpoint has NO cursor, page, offset or limit.
        since is an exclusive date-time lower bound. Omitting it asks ITAD for
        only its default last three months, NOT all-time history. For initial
        backfill use history_full(); for incremental refresh use history_since.
        Preserve raw order and timestamps; callers should merge/deduplicate
        with an overlap because late updates may have earlier timestamps.
        """
        params = {"id": _game_id(game_id), "country": normalize_country(country), "shops": "61"}
        if since is not None:
            params["since"] = _since(since)
        payload = await self._request("GET", "/games/history/v2", params=params)
        result = []
        for row in _list(payload):
            if not _is_steam(row):
                continue
            try:
                _since(row.get("timestamp"))
            except ValueError:
                raise ITADResponseError("ITAD 返回的历史时间格式无效。") from None
            if "deal" not in row or (row["deal"] is not None and not isinstance(row["deal"], dict)):
                raise ITADResponseError("ITAD 返回的历史价格记录无效。")
            if row["deal"] is not None:
                _check_deal(row["deal"])
            result.append(row)
        return result

    async def history_full(self, game_id: str, country: str) -> list[dict[str, Any]]:
        """Request all provider-recorded Steam changes, not guaranteed launch coverage."""
        return await self.history(game_id, country, HISTORY_START)

    async def history_since(self, game_id: str, country: str, since: datetime | str) -> list[dict[str, Any]]:
        """Request changes after since; no state, cursor or pagination is hidden."""
        return await self.history(game_id, country, since)

    async def active_shops(self, country: str) -> list[dict[str, Any]]:
        return _list(await self._request("GET", "/service/shops/v1", params={"country": normalize_country(country)}))

    async def validate_country(self, country: str) -> str:
        """Probe current Steam coverage, caching success for this client's lifetime.

        A current shop entry does not guarantee a particular game's availability
        or historical coverage. There is no supported public countries() API.
        """
        region = normalize_country(country)
        if region not in self._validated_countries:
            try:
                shops = await self.active_shops(region)
            except ITADError as exc:
                if exc.status == 400:
                    raise ITADRegionError("ITAD 不支持该国家代码。", status=400) from None
                raise
            if not any(shop.get("id") == STEAM_SHOP_ID for shop in shops):
                raise ITADRegionError("ITAD 暂未提供该地区的 Steam 数据。")
            self._validated_countries.add(region)
        return region
