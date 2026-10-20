"""Name failures expose useful metadata, never request/response contents."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import aiohttp

from price_history.itad import ITADAuthError, ITADRateLimitError, ITADResponseError
from price_history.query import Query, QueryError
from price_history.service import PriceService
from price_history.steam import SteamHTTPError, SteamResponseError, SteamStore, _log_name_failure
from price_history.storage import Store


LOGGER = 'astrbot_plugin_steam_price_history.name_lookup'
SECRET = 'unsafe-api-key=SECRET\nhttps://private.invalid/?query=PRIVATE response-body=PRIVATE'
GAME = {'id': '1', 'title': 'Example', 'type': 'game', 'appid': 1}


class Client:
    def __init__(self):
        self.search = AsyncMock(return_value=[])
        self.lookup_appid = AsyncMock(return_value=dict(GAME))
        self.info = AsyncMock(return_value=dict(GAME))
        self.shop_ids = AsyncMock(return_value={})

    async def close(self):
        pass


class Steam:
    def __init__(self):
        self.search = AsyncMock(return_value=[])
        self.app_details = AsyncMock(return_value={'type': 'game'})

    async def close(self):
        pass


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload, self.status, self.headers = payload, status, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self, **kwargs):
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SafeLogAssertions:
    def assert_diagnostics(self, capture, *expected):
        messages = [record.getMessage() for record in capture.records]
        self.assertEqual(messages, [
            f'Name lookup failed: stage={stage} error={kind} status={status}'
            for stage, kind, status in expected
        ])
        # No lazy arguments or exception/stack metadata may retain the object.
        for record in capture.records:
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)
            self.assertEqual(record.args, ())
        output = '\n'.join(capture.output)
        for forbidden in ('SECRET', 'PRIVATE', 'unsafe-api-key', 'https://', 'response-body', 'Traceback'):
            self.assertNotIn(forbidden, output)


class NameDiagnosticsTests(SafeLogAssertions, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.client, self.steam = Client(), Steam()
        self.now = 1000
        self.service = PriceService(self.client, self.steam, self.store, self.tmp.name,
                                    clock=lambda: self.now)
        self.query = Query('CN', SECRET)

    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()

    def resolution_ttl(self):
        return self.store.db.execute(
            "SELECT expires FROM cache WHERE key LIKE 'resolve:v3:%'"
        ).fetchone()[0] - self.now

    async def test_itad_auth_rate_limit_schema_timeout_and_http_errors_are_redacted(self):
        cases = [
            (ITADAuthError(SECRET, status=401), 'ITADAuthError', 401),
            (ITADRateLimitError(60), 'ITADRateLimitError', 429),
            (ITADResponseError(SECRET), 'ITADResponseError', 'unknown'),
            (TimeoutError(SECRET), 'TimeoutError', 'unknown'),
            (aiohttp.ClientResponseError(
                request_info=SECRET, history=(), status=503, message=SECRET,
                headers={'Authorization': SECRET}), 'ClientResponseError', 503),
        ]
        for exc, kind, status in cases:
            with self.subTest(kind=kind):
                self.client.search.side_effect = exc
                with self.assertLogs(LOGGER, 'WARNING') as capture:
                    with self.assertRaises(QueryError) as raised:
                        await self.service.resolve(self.query)
                self.assert_diagnostics(capture, ('itad_search', kind, status))
                self.assertIn('服务异常', str(raised.exception))
                self.assertNotIn('网络错误', str(raised.exception))
                self.assertNotIn('SECRET', str(raised.exception))
                self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0], 0)

    async def test_steam_search_failure_preserves_auto_first_alternatives_and_short_ttl(self):
        self.client.search.return_value = [dict(GAME), {**GAME, 'id': '2', 'appid': 2}]
        self.steam.search.side_effect = TimeoutError(SECRET)
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            base, alternatives = await self.service.resolve(self.query)
        self.assert_diagnostics(capture, ('steam_search', 'TimeoutError', 'unknown'))
        self.assertEqual(base['id'], '1')
        self.assertEqual([row['id'] for row in alternatives], ['2'])
        self.assertEqual(self.resolution_ttl(), 300)
        self.assertNotIn('stage=', str(base) + str(alternatives))
        with self.assertNoLogs(LOGGER, 'WARNING'):
            await self.service.resolve(self.query)
        self.assertEqual(self.steam.search.await_count, 1)
        self.now += 301
        with self.assertLogs(LOGGER, 'WARNING'):
            await self.service.resolve(self.query)
        self.assertEqual(self.steam.search.await_count, 2)

    async def test_appid_lookup_stage(self):
        self.client.lookup_appid.side_effect = ITADAuthError(SECRET, status=403)
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            with self.assertRaises(QueryError):
                await self.service.resolve(Query('CN', SECRET, appid=1))
        self.assert_diagnostics(capture, ('appid_lookup', 'ITADAuthError', 403))

    async def test_appid_metadata_stage(self):
        self.client.info.side_effect = ITADResponseError(SECRET)
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            with self.assertRaises(QueryError):
                await self.service.resolve(Query('CN', SECRET, appid=1))
        self.assert_diagnostics(capture, ('metadata_info', 'ITADResponseError', 'unknown'))

    async def test_steam_type_stage(self):
        self.client.lookup_appid.return_value['type'] = None
        self.client.info.return_value['type'] = None
        self.steam.app_details.side_effect = SteamHTTPError(503)
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            with self.assertRaises(QueryError):
                await self.service.resolve(Query('CN', SECRET, appid=1))
        self.assert_diagnostics(capture, ('steam_type_verify', 'SteamHTTPError', 503))

    async def test_missing_steam_type_verification_is_diagnosed(self):
        self.client.lookup_appid.return_value['type'] = None
        self.client.info.return_value['type'] = None
        self.steam.app_details.return_value = None
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            with self.assertRaises(QueryError):
                await self.service.resolve(Query('CN', SECRET, appid=1))
        self.assert_diagnostics(capture, ('steam_type_verify', 'OSError', 'unknown'))

    async def test_shop_id_stage_preserves_metadata_fallback(self):
        self.client.search.return_value = [{**GAME, 'appid': None, 'type': None}]
        self.client.shop_ids.side_effect = ITADRateLimitError(120)
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            base, _ = await self.service.resolve(self.query)
        self.assert_diagnostics(capture, ('shop_id_lookup', 'ITADRateLimitError', 429))
        self.assertEqual(base['id'], '1')
        self.assertEqual(self.resolution_ttl(), 300)

    async def test_unknown_candidate_metadata_failure_keeps_base(self):
        self.client.search.return_value = [dict(GAME), {'id': '2', 'title': 'Other'}]
        self.client.info.side_effect = lambda ident: dict(GAME) if ident == '1' else fail_schema()
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            base, alternatives = await self.service.resolve(self.query)
        self.assert_diagnostics(capture, ('metadata_info', 'ITADResponseError', 'unknown'))
        self.assertEqual(base['id'], '1')
        self.assertEqual(alternatives[0]['id'], '2')
        self.assertEqual(self.resolution_ttl(), 300)

    async def test_homonym_metadata_failure_keeps_alternative(self):
        self.client.search.return_value = [dict(GAME), {**GAME, 'id': '2', 'appid': None}]
        self.client.info.side_effect = lambda ident: dict(GAME) if ident == '1' else fail_schema()
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            base, alternatives = await self.service.resolve(self.query)
        self.assert_diagnostics(capture, ('metadata_info', 'ITADResponseError', 'unknown'))
        self.assertEqual(base['id'], '1')
        self.assertEqual(alternatives[0]['id'], '2')

    async def test_selected_metadata_failure_is_diagnosed_and_still_propagates(self):
        self.client.search.return_value = [dict(GAME)]
        self.client.info.side_effect = ITADResponseError(SECRET)
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            with self.assertRaises(ITADResponseError):
                await self.service.resolve(self.query)
        self.assert_diagnostics(capture, ('metadata_info', 'ITADResponseError', 'unknown'))

    async def test_success_and_real_no_match_are_quiet(self):
        self.client.search.return_value = [dict(GAME)]
        with self.assertNoLogs(LOGGER, 'WARNING'):
            await self.service.resolve(self.query)
        self.assertEqual(self.resolution_ttl(), 86400)
        self.client.search.return_value = []
        with self.assertNoLogs(LOGGER, 'WARNING'):
            with self.assertRaisesRegex(QueryError, '未找到该游戏'):
                await self.service.resolve(Query('CN', 'No match'))

    async def test_real_steam_rate_limit_and_schema_log_known_status(self):
        cases = [
            (Response({}, 429, {'Retry-After': '120'}), 'SteamHTTPError', 429),
            (Response({'items': SECRET}), 'SteamResponseError', 200),
        ]
        for response, kind, status in cases:
            with self.subTest(kind=kind):
                self.service.steam = SteamStore(Session(response))
                with self.assertLogs(LOGGER, 'WARNING') as capture:
                    with self.assertRaises(QueryError):
                        await self.service.resolve(self.query)
                self.assert_diagnostics(capture, ('steam_search', kind, status))

    async def test_partial_steam_validation_logs_and_retains_short_ttl(self):
        self.service.steam = SteamStore(Session(
            Response({'items': [{'type': 'app', 'id': 1}, {'type': 'app', 'id': 2}]}),
            Response({'1': {'success': True, 'data': {'type': 'game', 'name': 'Example'}}}),
            Response({'private': SECRET}, status=503),
        ))
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            base, alternatives = await self.service.resolve(self.query)
        self.assert_diagnostics(capture, ('steam_type_verify', 'SteamHTTPError', 503))
        self.assertEqual(base['id'], '1')
        self.assertFalse(alternatives)
        self.assertEqual(self.resolution_ttl(), 300)

    async def test_service_injected_logger_gets_one_safe_warning_without_fallback_duplicate(self):
        diagnostic_logger = Mock(spec=['warning'])
        self.service.diagnostic_logger = diagnostic_logger
        self.client.search.side_effect = ITADAuthError(SECRET, status=401)
        with self.assertNoLogs(LOGGER, 'WARNING'):
            with self.assertRaises(QueryError):
                await self.service.resolve(self.query)
        diagnostic_logger.warning.assert_called_once_with(
            'Name lookup failed: stage=itad_search error=ITADAuthError status=401')

    async def test_injected_runtime_loggers_capture_partial_steam_validation_once(self):
        diagnostic_logger = Mock(spec=['warning'])
        self.service.diagnostic_logger = diagnostic_logger
        self.service.steam = SteamStore(Session(
            Response({'items': [{'type': 'app', 'id': 1}, {'type': 'app', 'id': 2}]}),
            Response({'1': {'success': True, 'data': {'type': 'game', 'name': 'Example'}}}),
            TimeoutError(SECRET),
        ), diagnostic_logger=diagnostic_logger)
        with self.assertNoLogs(LOGGER, 'WARNING'):
            base, alternatives = await self.service.resolve(self.query)
        diagnostic_logger.warning.assert_called_once_with(
            'Name lookup failed: stage=steam_type_verify error=TimeoutError status=unknown')
        self.assertEqual(base['id'], '1')
        self.assertFalse(alternatives)
        self.assertEqual(self.resolution_ttl(), 300)


def fail_schema():
    raise ITADResponseError(SECRET)


class SteamDiagnosticsTests(SafeLogAssertions, unittest.IsolatedAsyncioTestCase):
    async def test_http_status_retained_in_search_and_cooldown(self):
        session = Session(Response({}, 429, {'Retry-After': '120'}))
        steam = SteamStore(session)
        for _ in range(2):
            with self.assertRaises(SteamHTTPError) as raised:
                await steam.search(SECRET)
            self.assertEqual(raised.exception.status, 429)
            self.assertNotIn('SECRET', str(raised.exception))
        self.assertEqual(session.calls, 1)

    async def test_http_status_retained_in_app_details_without_negative_cache(self):
        steam = SteamStore(Session(
            Response({'private': SECRET}, status=403),
            Response({'1': {'success': True, 'data': {'type': 'game'}}}),
        ))
        with self.assertRaises(SteamHTTPError) as raised:
            await steam.app_details(1)
        self.assertEqual(raised.exception.status, 403)
        self.assertFalse(steam.cache)
        self.assertEqual(await steam.app_details(1), {'type': 'game'})

    async def test_partial_type_failure_logs_timeout_without_secret(self):
        steam = SteamStore(Session(
            Response({'items': [{'type': 'app', 'id': 1}, {'type': 'app', 'id': 2}]}),
            Response({'1': {'success': True, 'data': {'type': 'game'}}}),
            TimeoutError(SECRET),
        ))
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            rows = await steam.search(SECRET)
        self.assert_diagnostics(capture, ('steam_type_verify', 'TimeoutError', 'unknown'))
        self.assertEqual([row['appid'] for row in rows], [1])
        self.assertTrue(rows.incomplete)

    async def test_all_type_failures_are_logged_before_safe_summary(self):
        steam = SteamStore(Session(
            Response({'items': [{'type': 'app', 'id': 1}]}),
            Response({'1': {'success': False}}),
        ))
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            with self.assertRaisesRegex(OSError, '核验暂不可用'):
                await steam.search(SECRET)
        self.assert_diagnostics(capture, ('steam_type_verify', 'OSError', 'unknown'))

    async def test_http_failure_still_skips_optional_editions(self):
        steam = SteamStore(Session(
            Response({'1': {'success': True, 'data': {'packages': [10, 20]}}}),
            Response({'private': SECRET}, status=503),
            Response({'20': {'success': True, 'data': {
                'name': 'Example Deluxe', 'apps': [{'id': 1}],
            }}}),
        ))
        with self.assertNoLogs(LOGGER, 'WARNING'):
            rows = await steam.discover(1, 'CN')
        self.assertEqual([row['shop_id'] for row in rows], ['sub/20'])

    async def test_schema_errors_remain_value_errors(self):
        steam = SteamStore(Session(Response({'items': SECRET})))
        with self.assertRaises(ValueError) as raised:
            await steam.search(SECRET)
        self.assertIsInstance(raised.exception, SteamResponseError)
        self.assertEqual(raised.exception.status, 200)

    async def test_untrusted_status_and_exception_name_not_logged(self):
        unsafe_type = type('ValueError\n' + SECRET, (ValueError,), {})
        exc = unsafe_type(SECRET)
        exc.status = SECRET
        with self.assertLogs(LOGGER, 'WARNING') as capture:
            _log_name_failure('itad_search', exc)
        self.assert_diagnostics(capture, ('itad_search', 'Exception', 'unknown'))

    async def test_injected_logger_receives_only_preformatted_safe_metadata(self):
        diagnostic_logger = Mock(spec=['warning'])
        unsafe_type = type('ValueError\n' + SECRET, (ValueError,), {})
        exc = unsafe_type(SECRET)
        exc.status = SECRET
        with self.assertNoLogs(LOGGER, 'WARNING'):
            _log_name_failure('itad_search', exc, diagnostic_logger)
        diagnostic_logger.warning.assert_called_once_with(
            'Name lookup failed: stage=itad_search error=Exception status=unknown')
