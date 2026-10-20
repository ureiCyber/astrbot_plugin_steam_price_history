"""Adapter contract tests with local AstrBot stubs, not a real bot runtime."""
import asyncio
import base64
from io import BytesIO
from PIL import Image as PILImage
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

class Star:
    def __init__(self,context): self.context=context
class Plain:
    def __init__(self,text): self.text=text
class Image:
    @classmethod
    def fromBytes(cls,data): return ('image', 'base64://' + base64.b64encode(data).decode('utf-8'))
class Event:
    def __init__(self,text): self.text=text
    def get_message_str(self): return self.text
    def plain_result(self,text): return ('text',text)
    def chain_result(self,chain): return ('chain',chain)
class Service:
    def __init__(self, path=None): self.query_text=None; self.closed=False; self.path=path
    async def query(self,text): self.query_text=text; return self.path,'最终结果'
    async def close(self): self.closed=True

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        modules={}
        for name in ['astrbot','astrbot.api','astrbot.api.event','astrbot.api.star','astrbot.api.message_components','astrbot.core','astrbot.core.utils','astrbot.core.utils.astrbot_path']:
            modules[name]=types.ModuleType(name)
        modules['astrbot.api'].AstrBotConfig=dict
        modules['astrbot.api'].logger=types.SimpleNamespace(warning=lambda *a:None,error=lambda *a:None)
        modules['astrbot.api.event'].AstrMessageEvent=Event
        modules['astrbot.api.event'].filter=types.SimpleNamespace(command=lambda name:lambda fn:fn)
        modules['astrbot.api.star'].Star=Star
        modules['astrbot.api.star'].Context=object
        modules['astrbot.api.message_components'].Plain=Plain
        modules['astrbot.api.message_components'].Image=Image
        modules['astrbot.core.utils.astrbot_path'].get_astrbot_data_path=lambda:'/tmp/astrbot-stub'
        package=types.ModuleType('plugin_adapter_test')
        package.__path__=[str(ROOT)]
        modules['plugin_adapter_test']=package
        self.patch=patch.dict(sys.modules,modules)
        self.patch.start()
        spec=importlib.util.spec_from_file_location('plugin_adapter_test.main',ROOT/'main.py')
        self.module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
    async def asyncTearDown(self): self.patch.stop()
    async def test_no_key_init_produces_useful_message(self):
        plugin=self.module.SteamPriceHistoryPlugin(object(),{})
        await plugin.initialize()
        results=[r async for r in plugin.history(Event('/史低 220'))]
        self.assertEqual(len(results),1)
        self.assertIn('API Key',results[0][1])
        await plugin.terminate()
    async def test_one_final_message_keeps_full_game_name(self):
        plugin=self.module.SteamPriceHistoryPlugin(object(),{})
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'chart.png'
        PILImage.new('RGB', (1080, 600), '#111827').save(path)
        plugin.service=Service(path)
        results=[r async for r in plugin.history(Event('史低 JP 豪华版 游戏 史低 特别篇'))]
        self.assertEqual(plugin.service.query_text,'JP 豪华版 游戏 史低 特别篇')
        self.assertEqual(len(results),1)
        self.assertEqual(results[0][0],'chain')
        self.assertEqual(len(results[0][1]),2)
        path.unlink()
        encoded = results[0][1][0][1]
        self.assertTrue(encoded.startswith('base64://'))
        with PILImage.open(BytesIO(base64.b64decode(encoded.removeprefix('base64://')))) as image:
            image.load()
            self.assertEqual(image.format, 'JPEG')
        service=plugin.service
        await plugin.terminate()
        self.assertTrue(service.closed)
    async def test_initialize_and_terminate_persistent_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.module,'get_astrbot_data_path',return_value=directory):
                plugin=self.module.SteamPriceHistoryPlugin(object(),{'itad_api_key':'test-only-key'})
                await plugin.initialize()
                self.assertIsNotNone(plugin.service)
                self.assertTrue((Path(directory)/'plugin_data/astrbot_plugin_steam_price_history/history.sqlite3').exists())
                task=plugin.poll_task
                await plugin.terminate()
                self.assertTrue(task.done())


    async def test_missing_image_keeps_price_text(self):
        plugin=self.module.SteamPriceHistoryPlugin(object(),{})
        plugin.service=Service(Path('/nonexistent/steam-test-chart.png'))
        results=[r async for r in plugin.history(Event('史低 220'))]
        self.assertEqual(len(results),1)
        self.assertEqual(len(results[0][1]),1)
        self.assertIn('最终结果',results[0][1][0].text)
        self.assertIn('曲线图片暂时无法读取',results[0][1][0].text)
