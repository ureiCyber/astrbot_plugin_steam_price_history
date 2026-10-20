import tempfile
import unittest
from pathlib import Path
from price_history.storage import Store

class StorageTests(unittest.TestCase):
    def test_persistence_dedupe_region_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'history.sqlite3'
            s = Store(path)
            row = {'timestamp':'2026-01-01T00:00:00+00:00','deal':None}
            s.merge_history('a','CN',[row,row])
            s.merge_history('b','CN',[row])
            s.put('a','CN',{'last_query':42})
            s.close()
            s = Store(path)
            self.assertEqual(len(s.history('a','CN')),1)
            self.assertEqual(s.history('a','JP'),[])
            self.assertEqual(s.get('a','CN')['last_query'],42)
            s.close()
    def test_cache_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'db')
            s.cache_put('x',{'a':1},100)
            self.assertEqual(s.cache_get('x',99),{'a':1})
            self.assertIsNone(s.cache_get('x',100))
            s.close()

    def test_corrected_timestamp_replaces_old_price(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'db')
            for amount in [10,20]:
                s.merge_history('a','CN',[{'timestamp':'2026-01-01T00:00:00+00:00','deal':{'price':{'amount':amount,'currency':'CNY'}}}])
            rows=s.history('a','CN')
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['deal']['price']['amount'],20)
            s.close()
