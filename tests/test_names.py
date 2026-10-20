import asyncio
import tempfile
import unittest
from pathlib import Path

from price_history.names import normalize_name, alias_map
from price_history.service import PriceService
from price_history.query import Query, QueryError
from price_history.storage import Store
from price_history.itad import ITADError
from price_history.rendering import format_candidates


class Client:
    def __init__(self):
        self.search_calls = []
        self.lookups = []
        self.results = []
        self.search_failure = False
        self.bad = set()
        self.mismatch = set()
        self.kind = 'game'
    async def search(self, title):
        self.search_calls.append(title)
        if self.search_failure:
            raise ITADError('429')
        return self.results
    async def lookup_appid(self, appid):
        self.lookups.append(appid)
        if appid in self.bad:
            raise ITADError('429')
        return {'id': str(appid), 'title': 'your wife', 'type': self.kind}
    async def info(self, ident):
        return {'id': ident, 'title': 'your wife', 'appid': 9 if int(ident) in self.mismatch else int(ident), 'type': self.kind}
    async def close(self): pass


class Steam:
    def __init__(self):
        self.calls = []
        self.failure = False
        self.results = [{'appid': 3339420, 'title': '你的老婆', 'type': 'game'},
                        {'appid': 3616650, 'title': '你的老婆', 'type': 'game'}]
    async def search(self, title):
        self.calls.append(title)
        await asyncio.sleep(0)
        if self.failure: raise OSError('Steam unavailable')
        return self.results
    async def close(self): pass


class NameTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_name('  ＡＢＣ\u3000 Game\t NAME '), 'abc game name')
        self.assertEqual(normalize_name('你的老婆'), '你的老婆')
    def test_mapping_normalized_and_multiple(self):
        self.assertEqual(alias_map({' ＡＢＣ ': ['10', 20, 10]})['abc'], [10, 20])
    def test_invalid_mapping(self):
        for value in [True, 0, -1, 'sub/1', 1.2, [1]*9, {}, None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                alias_map({'test': value})
    def test_disable_curated(self):
        self.assertEqual(alias_map({'老头环': []})['老头环'], [])
    def test_duplicate_names_distinct_apps_displayed(self):
        text = format_candidates([{'id':'a', 'title':'你的老婆', 'appid':3339420},
                                  {'id':'b', 'title':'你的老婆', 'appid':3616650}])
        self.assertIn('3339420', text)
        self.assertIn('3616650', text)


class ResolveNamesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'db')
        self.client, self.steam = Client(), Steam()
        self.now = 1000
        self.service = PriceService(self.client,self.steam,self.store,self.tmp.name,
                                    clock=lambda:self.now)
    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()
    async def test_localized_name_and_duplicate_order(self):
        base, others = await self.service.resolve(Query('JP','你的老婆'))
        self.assertEqual(base['appid'],3339420)
        self.assertEqual(base['title'],'你的老婆')
        self.assertEqual(others[0]['appid'],3616650)
        self.assertEqual(self.client.lookups,[3339420,3616650])
    async def test_exact_title_beats_partial(self):
        self.steam.results.insert(0,{'appid':100,'title':'你的老婆和你妈'})
        base, others = await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(base['appid'],3339420)
        self.assertEqual([c['appid'] for c in others],[3616650,100])
    async def test_english_backend_retained(self):
        self.client.results=[{'id':'42','title':'your wife','type':'game'}]
        base,_ = await self.service.resolve(Query('CN','YOUR WIFE'))
        self.assertEqual(base['appid'],42)
    async def test_configured_alias_without_search(self):
        self.service.game_aliases=alias_map({' 我的神作 ': [10,20]})
        base, others=await self.service.resolve(Query('JP','我的神作'))
        self.assertEqual(base['appid'],10)
        self.assertEqual(others[0]['appid'],20)
        self.assertFalse(self.steam.calls)
        self.assertFalse(self.client.search_calls)
    async def test_curated_alias(self):
        base,_=await self.service.resolve(Query('CN','老头环'))
        self.assertEqual(base['appid'],1245620)
    async def test_alias_change_invalidates_cache(self):
        self.service.game_aliases=alias_map({'foo':10})
        await self.service.resolve(Query('CN','foo'))
        self.service.game_aliases=alias_map({'foo':20})
        base,_=await self.service.resolve(Query('CN','foo'))
        self.assertEqual(base['appid'],20)
    async def test_normalized_queries_share_cache(self):
        await self.service.resolve(Query('CN','ＹＯＵＲ  WIFE'))
        await self.service.resolve(Query('US','your wife'))
        self.assertEqual(len(self.steam.calls),1)
    async def test_queries_coalesced(self):
        await asyncio.gather(*(self.service.resolve(Query('CN','你的老婆')) for _ in range(10)))
        self.assertEqual(len(self.steam.calls),1)
    async def test_ttl_research_keeps_ambiguity(self):
        await self.service.resolve(Query('CN','你的老婆'))
        self.now += 86401
        self.steam.results.reverse()
        base,_=await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(base['appid'],3616650)
        self.assertNotIn('你的老婆',self.service.game_aliases)
    async def test_itad_search_error_steam_success(self):
        self.client.search_failure=True
        base,_=await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(base['appid'],3339420)
    async def test_steam_error_itad_success(self):
        self.steam.failure=True
        self.client.results=[{'id':'42','title':'your wife','type':'game'}]
        base,_=await self.service.resolve(Query('CN','your wife'))
        self.assertEqual(base['appid'],42)
    async def test_error_not_negative_cached(self):
        self.client.search_failure=self.steam.failure=True
        with self.assertRaisesRegex(QueryError,'暂不可用'):
            await self.service.resolve(Query('CN','你的老婆'))
        self.client.search_failure=self.steam.failure=False
        base,_=await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(base['appid'],3339420)
    async def test_partial_alias_failure_retains_other(self):
        self.service.game_aliases=alias_map({'foo':[10,20]})
        self.client.bad.add(10)
        base,_=await self.service.resolve(Query('CN','foo'))
        self.assertEqual(base['appid'],20)
    async def test_mismatched_or_non_game_alias_rejected(self):
        self.service.game_aliases=alias_map({'foo':10})
        self.client.mismatch.add(10)
        with self.assertRaises(QueryError): await self.service.resolve(Query('CN','foo'))
        self.client.mismatch.clear()
        self.service.store.db.execute('DELETE FROM cache')
        self.client.kind='software'
        with self.assertRaises(QueryError): await self.service.resolve(Query('CN','foo'))
    async def test_dlc_identity_preserved(self):
        self.service.game_aliases=alias_map({'foo':10})
        self.client.kind='dlc'
        base,_=await self.service.resolve(Query('CN','foo'))
        self.assertEqual(base['type'],'dlc')
        self.assertEqual(base['appid'],10)
    async def test_old_cache_ignored(self):
        self.store.cache_put('resolve:v2:你的老婆',[{'id':'42','type':'game'}],999999)
        base,_=await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(base['appid'],3339420)
    async def test_bounded_metadata_cache_preserves_history(self):
        self.store.put('42','JP',{'value':1})
        for n in range(2050): self.store.cache_put(str(n),{},2000+n)
        self.store.prune_cache(self.now)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0],2048)
        self.assertEqual(self.store.get('42','JP'),{'value':1})
    async def test_itad_only_homonyms_include_alternative_appids(self):
        self.steam.results=[]
        self.client.results=[{'id':str(n),'title':'Twin','type':'game'} for n in (10,20)]
        base,others=await self.service.resolve(Query('CN','Twin'))
        self.assertEqual(base['appid'],10)
        self.assertEqual(others[0]['appid'],20)
        self.assertIn('AppID 20',format_candidates(others))
    async def test_partial_steam_results_get_short_ttl(self):
        from price_history.steam import SearchResults
        self.steam.results=SearchResults(self.steam.results,incomplete=True)
        await self.service.resolve(Query('CN','你的老婆'))
        self.now += 301
        await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(len(self.steam.calls),2)
    async def test_pruning_preserves_scheduler_backoff_and_daily_run(self):
        self.store.cache_put('daily-run','today',self.now+3600)
        self.store.cache_put('daily-backoff',True,self.now+3600)
        for n in range(2050): self.store.cache_put('info:'+str(n),{},self.now+86400)
        self.store.prune_cache(self.now)
        self.assertEqual(self.store.cache_get('daily-run',self.now),'today')
        self.assertTrue(self.store.cache_get('daily-backoff',self.now))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0],2050)
    async def test_null_itad_type_accepts_verified_steam_game(self):
        self.client.kind=None
        base,_=await self.service.resolve(Query('CN','你的老婆'))
        self.assertEqual(base['appid'],3339420)
    async def test_null_type_alias_requires_steam_verification(self):
        self.client.kind=None
        self.service.game_aliases=alias_map({'foo':10})
        async def details(appid): return {'type':'demo'}
        self.steam.app_details=details
        with self.assertRaises(QueryError): await self.service.resolve(Query('CN','foo'))
        async def game(appid): return {'type':'game'}
        self.steam.app_details=game
        base,_=await self.service.resolve(Query('CN','foo'))
        self.assertEqual(base['appid'],10)
    async def test_alias_unknown_type_unavailable_is_retriable_error(self):
        self.client.kind=None
        self.service.game_aliases=alias_map({'foo':10})
        async def missing(appid): return None
        self.steam.app_details=missing
        with self.assertRaisesRegex(QueryError,'暂不可用'):
            await self.service.resolve(Query('CN','foo'))
        async def game(appid): return {'type':'game'}
        self.steam.app_details=game
        base,_=await self.service.resolve(Query('CN','foo'))
        self.assertEqual(base['appid'],10)
    async def test_series_alias_preserves_multiple_appids(self):
        base,others=await self.service.resolve(Query('CN','空轨'))
        self.assertEqual(base['appid'],251150)
        self.assertEqual([r['appid'] for r in others][:2],[251290,436670])
        self.assertEqual((await self.service.resolve(Query('CN','空轨ＳＣ')))[0]['appid'],251290)
