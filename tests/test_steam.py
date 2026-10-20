import tempfile
from pathlib import Path
import unittest
from price_history.steam import SteamStore, edition_kind

class Discovery(SteamStore):
    def __init__(self,fixtures):
        super().__init__()
        self.fixtures=fixtures
    async def _get(self,endpoint,ident,country):
        return self.fixtures.get((endpoint,ident))

class SteamTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_related_edition_accepted(self):
        fixtures={
            ('appdetails',1):{'packages':[10,11,12,13]},
            ('packagedetails',10):{'name':'Game Deluxe','apps':[{'id':1},{'id':2}], 'price':{'currency':'CNY','final':2000,'initial':4000,'discount_percent':50}},
            ('packagedetails',11):{'name':'Game Deluxe Collection','apps':[{'id':1},{'id':3}]},
            ('packagedetails',12):{'name':'Game Gold','apps':[{'id':1},{'id':3}]},
            ('packagedetails',13):{'name':'Game Gold Upgrade','apps':[{'id':2}]},
            ('appdetails',2):{'type':'dlc','fullgame':{'appid':1}},
            ('appdetails',3):{'type':'game'},
        }
        client=Discovery(fixtures)
        results=await client.discover(1,'CN')
        self.assertEqual([r['shop_id'] for r in results],['sub/10','sub/13'])
        self.assertEqual(results[0]['current']['price']['amount'],20)
        self.assertEqual(results[1]['edition'],'gold-upgrade')
        self.assertTrue(results[1]['upgrade'])
    async def test_unknown_app_relation_rejected(self):
        client=Discovery({('appdetails',1):{'packages':[10]},('packagedetails',10):{'name':'Game Deluxe','apps':[{'id':1},{'id':2}]}})
        self.assertEqual(await client.discover(1,'CN'),[])
    async def test_cover_rejects_nonsteam_and_wrong_app_urls(self):
        for url in ['https://example.com/steam/apps/1/header.jpg','http://cdn.steamstatic.com/steam/apps/1/header.jpg','https://cdn.steamstatic.com/steam/apps/2/header.jpg','https://cdn.steamstatic.com@evil.com/steam/apps/1/header.jpg']:
            with tempfile.TemporaryDirectory() as d:
                client=Discovery({('appdetails',1):{'header_image':url}})
                self.assertIsNone(await client.cover(1,'CN',d))
                self.assertEqual(list(Path(d).iterdir()),[])
    async def test_cover_cache_does_not_need_network(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'1.jpg'
            path.write_bytes(b'cached')
            client=Discovery({})
            self.assertEqual(await client.cover(1,'CN',d),path)
    async def test_collectors_canonical(self):
        self.assertEqual(edition_kind("Game Collector's Edition"),'collectors')

class SearchResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload, self.status, self.headers = payload, status, headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def json(self, **kwargs): return self.payload

class SearchSession:
    def __init__(self, response):
        self.response, self.calls = response, []
    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        return self.response

class StoreSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_locale_type_filter_and_no_price_use(self):
        store = Discovery({('appdetails',3339420):{'name':'你的老婆','type':'game'},
                           ('appdetails',2):{'name':'Demo','type':'demo'},
                           ('appdetails',3):{'name':'DLC','type':'dlc'}})
        store.session=SearchSession(SearchResponse({'items':[
            {'type':'app','id':3339420,'name':'your wife','price':{'currency':'CNY','final':10}},
            {'type':'app','id':3339420,'name':'duplicate'},
            {'type':'app','id':2,'name':'Demo'}, {'type':'app','id':3,'name':'DLC'},
            {'type':'bundle','id':4}, {'type':'app','id':True}]}))
        rows=await store.search('你的老婆')
        self.assertEqual([r['appid'] for r in rows],[3339420,3])
        self.assertNotIn('price',rows[0])
        self.assertEqual(store.session.calls[0][1]['params'],
                         {'term':'你的老婆','l':'schinese','cc':'CN'})
    async def test_rate_limit_cooldown_respects_retry_after(self):
        import time
        store=SteamStore(SearchSession(SearchResponse({},429,{'Retry-After':'120'})))
        with self.assertRaises(OSError): await store.search('x')
        self.assertGreater(store.search_retry_at,time.time()+110)
        with self.assertRaises(OSError): await store.search('x')
        self.assertEqual(len(store.session.calls),1)
    async def test_invalid_response_and_http_errors(self):
        for payload,status in [([],200),({},200),({},500)]:
            store=SteamStore(SearchSession(SearchResponse(payload,status)))
            with self.assertRaises((OSError,ValueError)):
                await store.search('x')
    async def test_results_are_bounded(self):
        store=Discovery({('appdetails',n):{'name':str(n),'type':'game'} for n in range(1,21)})
        store.session=SearchSession(SearchResponse({'items':[{'type':'app','id':n} for n in range(1,21)]}))
        self.assertEqual(len(await store.search('x')),8)
    async def test_details_transient_failure_not_cached(self):
        session=SearchSession(SearchResponse({'1':{'success':False}}))
        store=SteamStore(session)
        self.assertIsNone(await store._get('appdetails',1,'CN'))
        session.response=SearchResponse({'1':{'success':True,'data':{'type':'game'}}})
        self.assertEqual((await store._get('appdetails',1,'CN'))['type'],'game')
        self.assertEqual(len(session.calls),2)
    async def test_all_detail_failures_raise_not_no_match(self):
        store=Discovery({})
        store.session=SearchSession(SearchResponse({'items':[{'type':'app','id':1}]}))
        with self.assertRaises(OSError): await store.search('x')
    async def test_one_detail_failure_preserves_verified_results(self):
        store=Discovery({('appdetails',1):{'type':'game','name':'One'}})
        original=store._get
        async def fail_second(endpoint,ident,country):
            if ident==2: raise TimeoutError()
            return await original(endpoint,ident,country)
        store._get=fail_second
        store.session=SearchSession(SearchResponse({'items':[{'type':'app','id':n} for n in (1,2)]}))
        rows=await store.search('One')
        self.assertEqual(rows[0]['appid'],1)
        self.assertTrue(rows.incomplete)
    async def test_rate_limit_http_date(self):
        import time
        from email.utils import formatdate
        store=SteamStore(SearchSession(SearchResponse({},429,{'Retry-After':formatdate(time.time()+180,usegmt=True)})))
        with self.assertRaises(OSError): await store.search('x')
        self.assertGreater(store.search_retry_at,time.time()+170)
