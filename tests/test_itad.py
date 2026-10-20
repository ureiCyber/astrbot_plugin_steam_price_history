"""Network-free contracts for the independent ITAD API adapter."""

from __future__ import annotations

import asyncio
import json
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch
from uuid import UUID

import aiohttp

from price_history.itad import (
    API_BASE,
    HISTORY_START,
    ITADAuthError,
    ITADClient,
    ITADError,
    ITADNotFoundError,
    ITADRateLimitError,
    ITADRegionError,
    ITADResponseError,
    normalize_country,
)

GAME_ID = "018d937f-07fc-72ed-8517-d8e24cb1eb22"
KEY = "secret-test-key-never-print"
GAME = {"id": GAME_ID, "title": "An Original Game", "type": "game"}
PRICE = {"amount": 9.99, "amountInt": 999, "currency": "EUR"}
DEAL = {"shop": {"id": 61, "name": "Steam"}, "price": PRICE, "regular": PRICE, "cut": 0}
ROW = {"timestamp": "2024-01-02T12:00:00+01:00", "shop": {"id": 61, "name": "Steam"}, "deal": {"price": PRICE, "regular": PRICE, "cut": 0}}


class FakeContent:
    def __init__(self, body):
        self.body = body

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class Response:
    def __init__(self, payload=None, *, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(raw if raw is not None else json.dumps(payload).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *responses, **kwargs):
        session = FakeSession(*responses)
        client = ITADClient(KEY, session=session, **kwargs)
        clock = Clock()
        client._sleep = clock.sleep
        client._monotonic = lambda: clock.now
        return client, session, clock

    async def test_search_uses_header_timeout_and_no_redirects(self):
        client, session, _ = self.client(Response([GAME]), timeout=7.5)
        self.assertEqual(await client.search("  Original ", 7), [GAME])
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, API_BASE + "/games/search/v1")
        self.assertNotIn(KEY, url)
        self.assertEqual(kwargs["headers"]["ITAD-API-Key"], KEY)
        self.assertEqual(kwargs["params"], {"title": "Original", "results": 7})
        self.assertNotIn("key", kwargs["params"])
        self.assertEqual(kwargs["timeout"].total, 7.5)
        self.assertFalse(kwargs["allow_redirects"])

    async def test_lookup_found_and_missing(self):
        client, session, _ = self.client(Response({"found": True, "game": GAME}), Response({"found": False}))
        self.assertEqual(await client.lookup_appid(220), GAME)
        self.assertIsNone(await client.lookup_appid("221"))
        self.assertEqual(session.calls[0][2]["params"], {"appid": "220"})
        for value in [True, 0, -1, "app/220", "1.2"]:
            with self.assertRaises(ValueError):
                await client.lookup_appid(value)

    async def test_exact_shop_identifiers_include_packages_and_bundles(self):
        ids = ["app/220", "sub/123", "bundle/456"]
        forward = {"app/220": GAME_ID, "sub/123": GAME_ID, "bundle/456": None}
        client, session, _ = self.client(Response(forward), Response({GAME_ID: ids}))
        self.assertEqual(await client.lookup_shop_ids(ids), forward)
        self.assertEqual(await client.shop_ids([GAME_ID]), {GAME_ID: ids})
        self.assertTrue(session.calls[0][1].endswith("/lookup/id/shop/61/v1"))
        self.assertTrue(session.calls[1][1].endswith("/lookup/shop/61/id/v1"))
        self.assertEqual(session.calls[0][2]["json"], ids)
        with self.assertRaises(ValueError):
            await client.lookup_shop_ids(["https://store.steampowered.com/app/220"])

    async def test_info_uses_v2(self):
        client, session, _ = self.client(Response(GAME))
        self.assertEqual(await client.info(GAME_ID), GAME)
        self.assertTrue(session.calls[0][1].endswith("/games/info/v2"))

    async def test_info_rejects_wrong_game_identity(self):
        client, _, _ = self.client(Response({**GAME, "id": str(UUID(int=1))}))
        with self.assertRaises(ITADResponseError):
            await client.info(GAME_ID)

    async def test_prices_steam_store_not_steam_drm(self):
        other = {"shop": {"id": 35}, "drm": [{"id": 61}], "price": {"amount": 1}}
        client, session, _ = self.client(Response([{"id": GAME_ID, "historyLow": {"all": {"amount": 1}}, "deals": [other, DEAL]}]))
        self.assertEqual(await client.prices(GAME_ID, "cn"), DEAL)
        _, url, kwargs = session.calls[0]
        self.assertTrue(url.endswith("/games/prices/v3"))
        self.assertEqual(kwargs["json"], [GAME_ID])
        self.assertEqual(kwargs["params"], {"country": "CN", "shops": "61", "deals": "false", "vouchers": "false"})

    async def test_prices_empty_is_missing_not_free(self):
        client, _, _ = self.client(Response([]))
        self.assertIsNone(await client.prices(GAME_ID, "US"))

    async def test_prices_zero_is_a_valid_price_not_missing(self):
        deal = {**DEAL, "price": {"amount": 0, "amountInt": 0, "currency": "EUR"}}
        client, _, _ = self.client(Response([{"id": GAME_ID, "deals": [deal]}]))
        self.assertEqual((await client.prices(GAME_ID, "US"))["price"]["amount"], 0)

    async def test_malformed_money_never_enters_history(self):
        for price in [{}, {**PRICE, "amount": -1}, {**PRICE, "amount": "9.99"}, {**PRICE, "amount": True},
                      {**PRICE, "amountInt": -1}, {**PRICE, "currency": "euros"}]:
            bad = {**ROW, "deal": {**ROW["deal"], "price": price}}
            client, _, _ = self.client(Response([bad]))
            with self.assertRaises(ITADResponseError):
                await client.history_full(GAME_ID, "US")

    async def test_prices_many_chunks_at_200_and_deduplicates(self):
        ids = [str(UUID(int=i)) for i in range(1, 202)]
        client, session, _ = self.client(Response([]), Response([]))
        output = await client.prices_many(ids + [ids[0]], "US")
        self.assertEqual(len(output), 201)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(len(session.calls[0][2]["json"]), 200)
        self.assertEqual(len(session.calls[1][2]["json"]), 1)
        self.assertTrue(all(value is None for value in output.values()))
        self.assertEqual(await client.prices_many([], "US"), {})
        self.assertEqual(len(session.calls), 2)

    async def test_history_full_explicit_epoch_no_fake_pagination(self):
        gap = {**ROW, "deal": None}
        other_shop = {**ROW, "shop": {"id": 35}}
        client, session, _ = self.client(Response([ROW, gap, other_shop]))
        self.assertEqual(await client.history_full(GAME_ID, "CN"), [ROW, gap])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][2]["params"], {"id": GAME_ID, "country": "CN", "shops": "61", "since": HISTORY_START})
        self.assertTrue(session.calls[0][1].endswith("/games/history/v2"))

    async def test_history_since_normalizes_timezone_not_response(self):
        client, session, _ = self.client(Response([ROW]), Response([]))
        self.assertEqual(await client.history_since(GAME_ID, "US", "2024-01-01T02:00:00+02:00"), [ROW])
        self.assertEqual(session.calls[0][2]["params"]["since"], "2024-01-01T00:00:00Z")
        await client.history(GAME_ID, "US")
        self.assertNotIn("since", session.calls[1][2]["params"])
        for value in ["2024-01-01", datetime(2024, 1, 1), "wrong", 1]:
            with self.assertRaises(ValueError):
                await client.history_since(GAME_ID, "US", value)

    async def test_malformed_history_and_pagination_envelopes_rejected(self):
        for payload in [{"data": [ROW], "next": "token"}, [{**ROW, "timestamp": "bad"}], [{"shop": {"id": 61}, "timestamp": ROW["timestamp"]}]]:
            client, _, _ = self.client(Response(payload))
            with self.assertRaises(ITADResponseError):
                await client.history_full(GAME_ID, "US")

    async def test_429_retry_after_seconds_and_shared_cooldown(self):
        client, session, clock = self.client(Response(status=429, headers={"Retry-After": "3"}), Response([GAME]))
        self.assertEqual(await client.search("Original"), [GAME])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(clock.sleeps, [3.0])

    async def test_429_http_date_parsed(self):
        date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5), usegmt=True)
        wait = ITADClient._retry_after(date, 123)
        self.assertGreaterEqual(wait, 3.0)
        self.assertLessEqual(wait, 5.0)
        self.assertEqual(ITADClient._retry_after("bad", 2), 2)
        self.assertEqual(ITADClient._retry_after("NaN", 2), 2)
        self.assertEqual(ITADClient._retry_after("-1", 2), 2)

    async def test_long_retry_after_does_not_retry_too_soon(self):
        client, session, clock = self.client(Response(status=429, headers={"Retry-After": "120"}), max_retry_after=5)
        for _ in range(2):
            with self.assertRaises(ITADRateLimitError) as caught:
                await client.search("Original")
            self.assertEqual(caught.exception.retry_after, 120)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(clock.sleeps, [])

    async def test_429_retries_are_bounded(self):
        client, session, clock = self.client(*(Response(status=429, headers={"Retry-After": "1"}) for _ in range(3)))
        with self.assertRaises(ITADRateLimitError):
            await client.search("Original")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1, 1])

    async def test_transient_server_and_network_errors_retry(self):
        client, session, clock = self.client(Response(status=503), aiohttp.ClientConnectionError(KEY), Response([]))
        self.assertEqual(await client.search("Original"), [])
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1, 2])

    async def test_unsafe_upstream_error_is_not_exposed(self):
        client, session, _ = self.client(aiohttp.ClientConnectionError(KEY), max_retries=0)
        try:
            await client.search("Original")
        except ITADError as exc:
            rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.assertNotIn(KEY, rendered)
        else:
            self.fail("Expected safe network error")
        self.assertEqual(len(session.calls), 1)

    async def test_auth_not_found_validation_redirect_do_not_retry(self):
        for status, error in [(401, ITADAuthError), (403, ITADAuthError), (404, ITADNotFoundError), (400, ITADError), (302, ITADError)]:
            client, session, _ = self.client(Response({"message": KEY}, status=status))
            with self.assertRaises(error) as caught:
                await client.search("Original")
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(len(session.calls), 1)

    async def test_json_and_size_errors_are_not_silent_empty_results(self):
        for raw in [b"{broken", b"NaN", b"\xff"]:
            client, session, _ = self.client(Response(raw=raw))
            with self.assertRaises(ITADResponseError):
                await client.search("Original")
            self.assertEqual(len(session.calls), 1)
        client, _, _ = self.client(Response([GAME]), max_response_bytes=10)
        with self.assertRaises(ITADResponseError):
            await client.search("Original")

    async def test_country_probe_requires_steam_and_caches_success(self):
        client, session, _ = self.client(Response([{"id": 61, "title": "Steam"}]))
        self.assertEqual(await client.validate_country(" cn "), "CN")
        self.assertEqual(await client.validate_country("CN"), "CN")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.calls[0][1].endswith("/service/shops/v1"))
        self.assertEqual(session.calls[0][2]["params"], {"country": "CN"})
        for payload in [[], [{"id": 35, "title": "GOG"}]]:
            client, _, _ = self.client(Response(payload))
            with self.assertRaises(ITADRegionError):
                await client.validate_country("US")

    async def test_country_error_is_distinct_and_no_us_fallback(self):
        client, session, _ = self.client(Response(status=400))
        with self.assertRaises(ITADRegionError):
            await client.validate_country("ZZ")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][2]["params"], {"country": "ZZ"})
        for value in ["", "USA", "中国", "1X", None]:
            with self.assertRaises(ITADRegionError):
                normalize_country(value)

    async def test_close_external_session_and_owned_session(self):
        client, session, _ = self.client(Response([]))
        await client.close()
        self.assertFalse(session.closed)
        with self.assertRaises(ITADError):
            await client.search("Original")
        owned = FakeSession(Response([]))
        with patch("price_history.itad.aiohttp.ClientSession", return_value=owned):
            async with ITADClient(KEY) as client:
                await client.search("Original")
        self.assertTrue(owned.closed)

    async def test_cancelled_requests_are_not_retried_or_swallowed(self):
        client, session, _ = self.client(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await client.search("Original")
        self.assertEqual(len(session.calls), 1)

    async def test_semaphore_bounds_concurrency(self):
        active = 0
        peak = 0

        class WaitingResponse(Response):
            async def __aenter__(self):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                return self

            async def __aexit__(self, *_):
                nonlocal active
                active -= 1

        client, _, _ = self.client(*(WaitingResponse([]) for _ in range(8)), concurrency=2)
        await asyncio.gather(*(client.search(f"Game {i}") for i in range(8)))
        self.assertEqual(peak, 2)

    def test_constructor_validates_configuration_without_key_exposure(self):
        for key in ["", " \n ", "x\r\n" + KEY, None]:
            with self.assertRaises(ITADAuthError) as caught:
                ITADClient(key)
            self.assertNotIn(KEY, str(caught.exception))
        for kwargs in [{"timeout": 0}, {"timeout": float("inf")}, {"concurrency": 0}, {"max_retries": 6}, {"max_retry_after": -1}]:
            with self.assertRaises(ValueError):
                ITADClient(KEY, **kwargs)


if __name__ == "__main__":
    unittest.main()
