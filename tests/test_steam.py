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
