"""Offline catalogue integrity and bounded multi-game resolution contracts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from price_history.names import CATALOGUE_PATH, CURATED_ALIASES, MAX_ALIAS_APPIDS, alias_map, load_catalogue
from price_history.query import Query, QueryError, parse_query
from price_history.rendering import format_candidates
from price_history.service import PriceService
from price_history.storage import Store


NEKOPARA = [333600, 420110, 602520, 1406990, 385800, 899970, 3655150, 1593310]
CATALOGUE = json.loads(CATALOGUE_PATH.read_text(encoding='utf-8'))


class CatalogueTests(unittest.TestCase):
    def test_reviewed_catalogue_has_evidence_and_no_unreferenced_apps(self):
        self.assertEqual(load_catalogue(), CURATED_ALIASES)
        self.assertEqual(len(CATALOGUE['aliases']), 40)
        self.assertEqual(len(CURATED_ALIASES), 51)
        self.assertEqual(len(CATALOGUE['apps']), 47)
        for ids in CURATED_ALIASES.values():
            self.assertTrue(1 <= len(ids) <= MAX_ALIAS_APPIDS)
            self.assertEqual(len(ids), len(set(ids)))
            for ident in ids:
                app = CATALOGUE['apps'][str(ident)]
                self.assertEqual(app['type'], 'game')
                self.assertEqual(app['verified_on'], '2026-10-20')
                self.assertIn(f'appids={ident}&', app['source'])

    def test_user_spellings_are_same_series_not_single_game(self):
        aliases = alias_map({})
        for name in ('草猫', '艹猫', 'nekopara', '猫娘乐园'):
            self.assertEqual(aliases[name], NEKOPARA)

    def test_numbered_user_spellings_are_exact_volumes(self):
        aliases = alias_map({})
        for number, ident in [(0,385800), (1,333600), (2,420110), (3,602520), (4,1406990)]:
            for prefix in ('草猫', '艹猫'):
                self.assertEqual(aliases[f'{prefix}{number}'], [ident])
        self.assertEqual(aliases['草猫extra'], [899970])
        self.assertEqual(aliases['艹猫after'], [3655150])

    def test_official_titles_and_types_prevent_known_false_ids(self):
        apps = CATALOGUE['apps']
        self.assertEqual(apps['1461920']['title'], '英雄传说 碧之轨迹：改')
        self.assertEqual(apps['3418570']['title'], 'Summer Pockets REFLECTION BLUE')
        self.assertEqual(apps['3655150']['title'], 'NEKOPARA After')
        self.assertNotIn('1466860', apps)  # Age of Empires IV, not Trails to Azure.
        self.assertNotIn('1656640', apps)  # Four Color Jump, not Trails into Reverie.

    def test_key_versions_remain_distinct_and_fandiscs_are_not_implicit(self):
        aliases = alias_map({})
        self.assertEqual(aliases['夏日口袋'], [3418570, 897220])
        self.assertEqual(aliases['夏日口袋rb'], [3418570])
        self.assertEqual(aliases['星之梦'], [623080, 316720])
        self.assertEqual(aliases['罚抄'], [1680000])
        self.assertEqual(aliases['clannad'], [324160])
        self.assertNotIn(2983350, aliases['星之梦'])

    def test_trails_series_preserve_regional_releases_and_order(self):
        aliases = alias_map({})
        self.assertEqual(aliases['零轨'], [1457520, 1668510])
        self.assertEqual(aliases['碧轨'], [1461920, 1668520])
        self.assertEqual(aliases['闪轨'], [
            1450120, 538680, 1450090, 748490, 1450080, 991270, 1457510, 1198090])
        for number in range(1,5):
            self.assertEqual(aliases[f'闪轨{number}'], aliases['闪轨'][(number-1)*2:number*2])
        self.assertEqual(aliases['黎轨'], [1811950,2138610,2113920,2668430])

    def test_configuration_normalized_override_and_disable_are_independent(self):
        aliases = alias_map({' ＮＥＫＯＰＡＲＡ ': [385800], '草猫': []})
        self.assertEqual(aliases['nekopara'], [385800])
        self.assertEqual(aliases['草猫'], [])
        self.assertEqual(aliases['艹猫'], NEKOPARA)
        self.assertEqual(CURATED_ALIASES['nekopara'], NEKOPARA)

    def test_mapping_results_do_not_mutate_packaged_data(self):
        result = alias_map({})
        result['草猫'].reverse()
        self.assertEqual(alias_map({})['草猫'], NEKOPARA)

    def assert_invalid(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalogue.json'
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '内置游戏别名目录无效'):
                load_catalogue(path)

    def test_missing_invalid_or_incomplete_packaged_data_fails_closed(self):
        with self.assertRaisesRegex(ValueError, '重新安装完整插件'):
            load_catalogue('/does-not-exist/alias_catalogue.json')
        for data in (None, [], {}, {'schema_version': 1, 'apps': {}, 'aliases': []}):
            self.assert_invalid(data)
        data = copy.deepcopy(CATALOGUE)
        data['schema_version'] = True
        self.assert_invalid(data)

    def test_unknown_id_oversized_duplicate_or_empty_series_rejected(self):
        for ids in ([1], list(range(1,10)), [333600,333600], [], [True], ['333600']):
            data = copy.deepcopy(CATALOGUE)
            data['aliases'][0]['appids'] = ids
            self.assert_invalid(data)

    def test_normalized_duplicate_alias_rejected(self):
        data = copy.deepcopy(CATALOGUE)
        data['aliases'].append({'names':[' ＮＥＫＯＰＡＲＡ '], 'appids':[333600], 'basis':'test'})
        self.assert_invalid(data)

    def test_unverified_type_source_or_identity_rejected(self):
        for key, value in [('type','demo'), ('source','https://example.com'),
                           ('appid',333601), ('appid',333600.0),
                           ('title',''), ('verified_on',None)]:
            data = copy.deepcopy(CATALOGUE)
            data['apps']['333600'][key] = value
            self.assert_invalid(data)

    def test_unreferenced_record_rejected(self):
        data = copy.deepcopy(CATALOGUE)
        app = copy.deepcopy(data['apps']['333600'])
        app.update(appid=1, source='https://store.steampowered.com/api/appdetails?appids=1&l=schinese&cc=CN')
        data['apps']['1'] = app
        self.assert_invalid(data)

    def test_numeric_query_still_means_appid(self):
        query = parse_query('2077')
        self.assertEqual(query.appid, 2077)
        self.assertNotIn('2077', CURATED_ALIASES)


class CatalogueResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.missing = set()
        async def lookup(ident):
            if ident in self.missing:
                return None
            app = CATALOGUE['apps'][str(ident)]
            return {'id':str(ident), 'appid':ident, 'title':app['title'], 'type':app['type']}
        async def info(ident):
            return await lookup(int(ident))
        self.client = type('Client', (), {})()
        self.client.lookup_appid = AsyncMock(side_effect=lookup)
        self.client.info = AsyncMock(side_effect=info)
        self.client.search = AsyncMock(return_value=[])
        self.client.close = AsyncMock()
        self.steam = type('Steam', (), {})()
        self.steam.search = AsyncMock(return_value=[])
        self.steam.close = AsyncMock()
        self.service = PriceService(self.client, self.steam, self.store, self.tmp.name)

    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()

    async def test_series_auto_first_and_all_other_apps_without_name_search(self):
        base, others = await self.service.resolve(Query('JP', '草猫'))
        self.assertEqual(base['appid'], 333600)
        self.assertEqual(base['title'], 'NEKOPARA Vol. 1')
        self.assertEqual([row['appid'] for row in others], NEKOPARA[1:])
        self.assertEqual(self.client.lookup_appid.await_count, 8)
        self.client.search.assert_not_awaited()
        self.steam.search.assert_not_awaited()
        text = format_candidates(others)
        self.assertNotIn('AppID 333600', text)
        for ident in NEKOPARA[1:]:
            self.assertIn(f'AppID {ident}', text)
        self.assertNotIn('确认', text)

    async def test_fullwidth_number_and_case_normalization(self):
        base, others = await self.service.resolve(parse_query('JP 艹猫４'))
        self.assertEqual(base['appid'], 1406990)
        self.assertFalse(others)
        base, others = await self.service.resolve(parse_query('草猫ＡＦＴＥＲ'))
        self.assertEqual(base['appid'], 3655150)
        self.assertFalse(others)

    async def test_missing_first_itad_record_uses_next_without_inventing_identity(self):
        self.missing.add(333600)
        base, others = await self.service.resolve(Query('CN', '艹猫'))
        self.assertEqual(base['appid'], 420110)
        self.assertEqual([row['appid'] for row in others], NEKOPARA[2:])
        self.assertEqual(self.client.lookup_appid.await_count, 8)

    async def test_no_itad_records_is_not_fake_success(self):
        self.missing.update(NEKOPARA)
        with self.assertRaisesRegex(QueryError, '未找到该游戏'):
            await self.service.resolve(Query('CN', '艹猫'))
        self.assertEqual(self.client.lookup_appid.await_count, 8)

    async def test_disabled_builtin_falls_back_to_name_search(self):
        self.service.game_aliases = alias_map({'草猫': []})
        with self.assertRaises(QueryError):
            await self.service.resolve(Query('CN', '草猫'))
        self.client.lookup_appid.assert_not_awaited()
        self.client.search.assert_awaited_once_with('草猫')
        self.steam.search.assert_awaited_once_with('草猫')
