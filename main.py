"""AstrBot entry point: one final image-and-text response, no progress spam."""
import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .price_history.itad import ITADClient, ITADError
from .price_history.media import prepare_image
from .price_history.query import QueryError
from .price_history.service import PriceService
from .price_history.steam import SteamStore
from .price_history.storage import Store


class SteamPriceHistoryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.service = None
        self.poll_task = None
        self.init_error = None

    async def initialize(self):
        key = str(self.config.get('itad_api_key', '')).strip()
        if not key:
            self.init_error = '请先在插件配置中填写 IsThereAnyDeal API Key。'
            return
        directory = Path(get_astrbot_data_path()) / 'plugin_data' / 'astrbot_plugin_steam_price_history'
        store = None
        client = None
        steam = None
        try:
            client = ITADClient(key, timeout=20, concurrency=4)
            steam = SteamStore(diagnostic_logger=logger)
            store = Store(directory / 'history.sqlite3')
            self.service = PriceService(client, steam, store, directory / 'charts', self.config,
                                        diagnostic_logger=logger)
            self.poll_task = asyncio.create_task(self._poll(), name='steam-price-history-cache-refresh')
        except (ValueError, ITADError) as exc:
            self.init_error = str(exc)
            if store:
                store.close()
            if client:
                await client.close()
            if steam:
                await steam.close()

    async def _poll(self):
        while True:
            try:
                await self.service.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # No response body, API key, URLs or tokens in logs.
                logger.warning(f'Steam price cache refresh failed ({type(exc).__name__}); will retry later.')
            await asyncio.sleep(60)

    @filter.command('史低')
    async def history(self, event: AstrMessageEvent):
        """查询 Steam 史低与一年曲线：/史低 [地区] [版本] 游戏名或 AppID。"""
        if self.service is None:
            yield event.plain_result(self.init_error or '插件尚未初始化，请稍后再试。')
            return
        # AstrBot already recognizes/removes the configured wake prefix. Keeping
        # all remaining text preserves multi-word game names on every adapter.
        query = event.get_message_str().partition('史低')[2].strip()
        try:
            image, text = await self.service.query(query)
            chain = []
            if image:
                try:
                    # Eager encoded bytes outlive the disposable chart cache;
                    # never hand a delayed local-file read to the adapter.
                    payload = await asyncio.to_thread(prepare_image, image)
                    chain.append(Comp.Image.fromBytes(payload))
                except (OSError, ValueError) as exc:
                    logger.warning(f'Steam price chart preparation failed ({type(exc).__name__}); sending text only.')
                    text += '\n曲线图片暂时无法读取，已保留价格结果。' 
            chain.append(Comp.Plain(text))
            yield event.chain_result(chain)
        except (QueryError, ITADError) as exc:
            yield event.plain_result(str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f'Steam price query failed ({type(exc).__name__}).')
            yield event.plain_result('查询失败，暂时无法生成结果。请稍后重试；已有历史数据仍保留。')

    async def terminate(self):
        if self.poll_task:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
        if self.service:
            await self.service.close()
            self.service = None

