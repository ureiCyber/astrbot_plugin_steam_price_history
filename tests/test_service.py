import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from price_history.service import PriceService
from price_history.storage import Store
from price_history.itad import ITADError

D = {'price': {'amount': 10, 'currency':'CNY'}, 'regular': {'amount':20,'currency':'CNY'}, 'cut':50}

class FakeClient:
    def __init__(self):
        self.calls = []
        self.deal = D.copy()
        self.records = [{'timestamp':'2026-01-01T00:00:00Z','deal':D}]
        self.fail = False
    async def prices(self, ident, country):
        self.calls.append(('price',ident,country))
        await asyncio.sleep(0)
        if self.fail: raise ITADError('网络失败')
        return self.deal
    async def prices_many(self, ids, country):
        self.calls.append(('batch',tuple(ids),country))
        return {i:self.deal for i in ids}
    async def history(self, ident, country, since=None):
        self.calls.append(('history',ident,country,since))
        return [dict(r) for r in self.records]
    async def history_full(self, ident, country):
        return await self.history(ident,country,since='1970-01-01T00:00:00Z')
    async def close(self): pass

class FakeSteam:
    async def close(self): pass

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,10,20,2,tzinfo=timezone.utc).timestamp()
        self.client=FakeClient()
        self.store=Store(Path(self.tmp.name)/'cache.db')
        self.service=PriceService(self.client,FakeSteam(),self.store,Path(self.tmp.name)/'charts',clock=lambda:self.now)
    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()
    async def test_full_then_cache_then_increment(self):
        await self.service.refresh('a','CN',touch=True)
        self.assertEqual(self.client.calls[-1],('history','a','CN','1970-01-01T00:00:00Z'))
        n=len(self.client.calls)
        await self.service.refresh('a','CN',touch=True)
        self.assertEqual(len(self.client.calls),n)
        self.now+=4*86400
        await self.service.refresh('a','CN',touch=True)
        self.assertIsNotNone(self.client.calls[-1][-1])
        self.assertEqual(len(self.store.history('a','CN')),1)
    async def test_concurrent_requests_coalesce(self):
        await asyncio.gather(*(self.service.refresh('a','CN',touch=True) for _ in range(20)))
        self.assertEqual(sum(c[0]=='price' for c in self.client.calls),1)
        self.assertEqual(sum(c[0]=='history' for c in self.client.calls),1)
    async def test_changed_price_empty_history_stays_pending(self):
        await self.service.refresh('a','CN',touch=True)
        self.client.deal={**D,'price':{'amount':8,'currency':'CNY'}}
        self.client.records=[]
        self.now+=86401
        state=await self.service.refresh('a','CN',touch=True)
        self.assertTrue(state['pending'])
        self.assertIn('retry_at',state)
        self.assertEqual(len(self.store.history('a','CN')),1)
    async def test_failed_current_preserves_cache(self):
        await self.service.refresh('a','CN',touch=True)
        self.client.fail=True
        self.now+=86401
        state=await self.service.refresh('a','CN',touch=True)
        self.assertEqual(state['current'],D)
        self.assertEqual(state['current_error'],'网络失败')
    async def test_active_expiration_and_region_batch(self):
        await self.service.refresh('a','CN',touch=True)
        await self.service.refresh('b','JP',touch=True)
        self.store.put('old','CN',{'last_query':self.now-4*86400})
        self.client.calls=[]
        await self.service.tick()
        batches=[c for c in self.client.calls if c[0]=='batch']
        self.assertEqual(len(batches),2)
        self.assertNotIn('old',[i for b in batches for i in b[1]])
        await self.service.tick()
        self.assertEqual(len([c for c in self.client.calls if c[0]=='batch']),2)
    async def test_null_is_not_free(self):
        self.assertIsNone(self.service.lowest({},[{'timestamp':'2026-01-01','deal':None}]))
    async def test_expiry_only_checked_once(self):
        self.client.deal={**D,'expiry':'2026-10-20T01:00:00Z'}
        await self.service.refresh('a','CN',touch=True)
        await self.service.refresh('a','CN')
        n=len(self.client.calls)
        await self.service.refresh('a','CN')
        self.assertEqual(len(self.client.calls),n)

class QueryClient(FakeClient):
    async def validate_country(self,country): return country
    async def search(self,title): return [{'id':'base','title':title,'type':'game'}]
    async def lookup_appid(self,appid): return {'id':'base','title':'Example Game','type':'game'}
    async def info(self,ident): return {'id':ident,'title':'Example Game','appid':123}
    async def lookup_shop_ids(self,ids): return {i:'base' for i in ids}

class QuerySteam(FakeSteam):
    async def discover(self,appid,country):
        return [{'edition':'deluxe','title':'Example Deluxe','shop_id':'sub/456','current':D}]

class QueryFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=QueryClient()
        self.client.records=[]
        self.store=Store(Path(self.tmp.name)/'db')
        self.service=PriceService(self.client,QuerySteam(),self.store,self.tmp.name)
    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()
    async def test_single_result_does_not_append_no_match(self):
        image,text=await self.service.query('123')
        self.assertIsNone(image)
        self.assertNotIn('没有找到匹配',text)
        self.assertNotIn('还有其他类似名字',text)
        self.assertIn('Example Game',text)
    async def test_alias_container_never_becomes_deluxe_history(self):
        image,text=await self.service.query('豪华版 123')
        self.assertIsNone(image)
        self.assertIn('Example Deluxe',text)
        self.assertIn('Example Game',text)
        self.assertIn('合并',text)
        # Base is refreshed for comparison only, not relabelled as deluxe.
        self.assertEqual(sum(c[0]=='history' for c in self.client.calls),1)
    async def test_missing_edition_does_not_fall_back(self):
        from price_history.query import QueryError
        with self.assertRaises(QueryError):
            await self.service.query('黄金版 123')
        self.assertEqual(sum(c[0]=='price' for c in self.client.calls),0)
    async def test_mapping_failure_keeps_base_query(self):
        async def fail(ids): raise ITADError('网络失败')
        self.client.lookup_shop_ids=fail
        _,text=await self.service.query('123')
        self.assertIn('Example Game',text)
        self.assertIn('映射暂不可用',text)
    async def test_same_timestamp_correction_does_not_retain_low(self):
        self.client.records=[{'timestamp':'2026-01-01T00:00:00Z','deal':D}]
        await self.service.refresh('base','CN',touch=True)
        corrected={**D,'price':{'amount':15,'currency':'CNY'}}
        self.client.records=[{'timestamp':'2026-01-01T00:00:00Z','deal':corrected}]
        self.client.deal=corrected
        await self.service.refresh('base','CN',force=True)
        rows=self.store.history('base','CN')
        self.assertEqual(len(rows),1)
        self.assertEqual(self.service.lowest({},rows)['price']['amount'],15)
    async def test_background_refresh_joined_by_query_touches_activity(self):
        background=asyncio.create_task(self.service.refresh('base','CN'))
        await asyncio.sleep(0)
        await self.service.refresh('base','CN',touch=True)
        await background
        self.assertGreater(self.store.get('base','CN')['last_query'],0)
    async def test_currency_low_without_current_uses_latest_currency(self):
        rows=[{'timestamp':'2025-01-01','deal':{'price':{'amount':1,'currency':'ARS'}}},
              {'timestamp':'2026-01-01','deal':{'price':{'amount':10,'currency':'USD'}}}]
        self.assertEqual(self.service.lowest({},rows)['price']['currency'],'USD')
    async def test_failed_increment_retains_original_watermark(self):
        now=2_000_000
        self.service.clock=lambda:now
        self.store.put('base','CN',{'initialized':True,'history_checked_at':1_000_000,'checked_at':1_000_000,'current':D})
        async def fail(*a,**kw): raise ITADError('历史接口暂不可用')
        original=self.client.history
        self.client.history=fail
        state=await self.service.refresh('base','CN',force=True)
        self.assertEqual(state['pending_since'],1_000_000)
        self.client.history=original
        now+=3601
        await self.service.refresh('base','CN',force=True)
        last=[c for c in self.client.calls if c[0]=='history'][-1]
        self.assertLessEqual(datetime.fromisoformat(last[-1]).timestamp(),1_000_000)
