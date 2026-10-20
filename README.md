# Steam 价格历史 · AstrBot 插件

按游戏、地区和确认过的购买版本查询 Steam 当前价、折扣、历史最低价，使用最近一年的阶梯价格图替换封面：封面淡化为背景，横轴按月份标注，曲线仍保留真实日期的折扣起止。独立实现；正常查询只回复最终的“图片＋文字”，不发搜索中提示。

> 0.1.0 初版。已提供自动化测试；尚未使用真实 ITAD Key 和真实 AstrBot 消息适配器做端到端验证。具体地区、购买版本与历史覆盖取决于数据源。不要把“API 可提供的全部记录”理解为每款游戏从发售至今都有完整历史。

## 安装

在 AstrBot 插件管理中从仓库安装：

https://github.com/ureiCyber/astrbot_plugin_steam_price_history

使用 Python 3.12，按当前 AstrBot 4.x 官方插件接口实现。未实测各历史版本兼容性，建议使用当前稳定版。依赖见 `requirements.txt`。

1. 在 [IsThereAnyDeal Apps](https://isthereanydeal.com/apps/) 注册应用取得 API Key，确认用途符合其条款。
2. 在插件配置中填写 `itad_api_key`，保存并重载插件。无需 Steam Web API Key。
3. 发送 `/史低 220` 测试。Key 错误、网络失败、地区无报价和游戏不存在分别处理。
4. 如果图中文字使用英文降级显示，在运行 AstrBot 的机器安装中文字体，例如 Noto Sans CJK SC，或配置 `chart_fonts` 为已经安装的字体名。

请不要把 API Key 发到群聊、写进仓库或截图分享。

## 命令

- `/史低 游戏名`：CN，标准版本体。
- `/史低 JP 游戏名`：指定地区，地区缩写必须大写。
- `/史低 豪华版 游戏名`：CN，已确认的豪华版。
- `/史低 JP 豪华版 游戏名`：指定地区、指定版本。
- `/史低 1245620` 或 `/史低 https://store.steampowered.com/app/1245620/`：Steam AppID 或本体链接。
- `/史低 金库版升级 游戏名`：查询已经确认的升级购买项，明确标注需要本体。

内置黄金版、豪华版、终极版、金库版、高级版、完整版、决定版、年度版、典藏版、周年版等中英文别名，也可在配置中扩展。版本不存在或关系无法核实会明确提示，不偷偷改查本体。名称搜索按标准化后的完整名称优先排序，自动采用第一个已核实候选，不增加选项确认步骤；同名游戏保留其他 AppID，请核对返回标题与商店链接，可用 AppID 精确查询。

仅有一个候选时不再提示候选；多个候选时在最终结果末尾附“还有其他类似名字的游戏”，去重并排除已显示游戏。`IT Takes Two` 有专门的歧义保护；意大利价格请用 `/史低 IT IT Takes Two`。

## 价格与版本的边界

- 所有历史只查询 ITAD 的 Steam 商店（shop 61），不把其他平台售卖的 Steam Key 当成 Steam 商店价格。
- 不做汇率换算，不把美国价格冒充其他地区；币种按接口实际返回显示。
- 优先采用 Steam 的全时期 `storeLow`；不存在时取同币种已收录历史最低值。没有历史就是暂无数据，不是 0 元。
- 默认曲线为本体，其他已确认版本各自列现价、折扣和史低；指定版本后只画该版本。
- 自动版本识别使用 Steam 商店公开 JSON 的购买套餐与 DLC 归属，要求套餐只含本体及其 DLC，排除多游戏合集、商业许可和 bundle。该商店接口并非稳定的官方 Web API，可能暂时失效或遗漏版本。
- ITAD 对某些 app/sub 使用同一历史容器。如果豪华版和本体映射到同一个 UUID，或多个不同购买套餐共享 UUID，本插件不把本体历史贴成豪华版，只保留可核实的 Steam 套餐现价，历史显示缺失。
- Steam 不一定在本体页面列出所有升级包。无法自动确认的关系可由管理员填写精确映射，不能根据相似名字猜测。

### 可选精确版本映射

`edition_aliases` 为 JSON，例如 `{"周年版":"anniversary"}`。

`edition_mappings` 为 JSON，以本体 AppID 为键，示例仅说明格式，不是真实商品映射：

```json
{
  "123": {
    "deluxe": {
      "shop_id": "sub/456",
      "title": "某游戏豪华版",
      "upgrade": false
    },
    "vault-upgrade": {
      "shop_id": "app/789",
      "title": "某游戏金库版升级",
      "upgrade": true
    }
  }
}
```

必须先人工核对真实购买项及本体关系。只接受 app/sub，不接受 bundle。精确映射仍需 ITAD 对应记录；没有记录不伪造历史。

## 缓存与自动更新

- 每个 ITAD 产品身份＋地区首次明确从 1970 年请求全部可用历史。官方 `history/v2` 返回数组、没有分页参数；不使用默认的近三个月冒充全历史。
- 后续按时间增量同步，并保留 48 小时重叠窗口接住延迟记录。已有时间点的修订记录会更新，不重复堆积。
- 全历史长期保留；图表只是截取最近 365 天，并保留窗口前最近一个已知价格作为起点。数据中断和不可售状态画断点，不当免费。同月多次促销保留全部价格区间，不平均成单个月价；减少标签而不删除数据。封面仅从已验证的 Steam CDN 地址限量下载并缓存，失败使用纯色背景。
- 最近 72 小时有人查询的“产品＋地区”保持活跃；再次查询续期。超过此时间停止后台检查，但不删除历史。
- 默认每天北京时间 09:00 按地区批量检查现价，每批最多 200 个产品。价格、原价、折扣或可售状态变化后补历史。
- 查询现价缓存默认 24 小时；停更后再次查询会补齐期间记录。活跃产品每周增量核对一次，尽量补上短促销。
- 接口给出折扣结束时间时，到期后检查一次；没有结束时间不猜测。历史滞后时保留待补齐状态，每小时重试。
- 同一查询合并并发请求，接口限流遵守 Retry-After，失败退避；能取得旧缓存时保留并明确标注更新时间。
- 后台只维护缓存，不主动给群或个人发促销消息。

## 配置

| 配置 | 默认 | 用途 |
| --- | --- | --- |
| itad_api_key | 空 | ITAD API Key |
| active_days | 3 | 无人查询多少天后停止后台更新 |
| daily_time | 09:00 | 北京时间每日检查时刻 |
| cache_hours | 24 | 查询现价缓存时长 |
| game_aliases | {} | 游戏俗称到一个或多个 Steam AppID 的 JSON 映射，和购买版本别名分开 |
| edition_aliases | {} | 版本中文/英文别名 JSON |
| edition_mappings | {} | 精确购买版本关系 JSON |
| chart_fonts | 空 | 已安装字体名，逗号分隔；留空自动选择 |

数据保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_steam_price_history/history.sqlite3`，不是插件代码目录。升级插件不覆盖该文件。图片缓存可以重建，历史数据库请自行备份；删除 AstrBot 数据目录仍会丢失数据。

## 本地验证

```bash
python -m pip install -r requirements.txt
python -m compileall -q main.py price_history tests
python -m unittest discover -s tests -v
```

测试使用可控响应和临时数据库，不需要真实 API Key，不发送外部聊天消息。自动化测试通过不等于已验证真实国区/日区价格覆盖或所有 AstrBot 平台适配器。

## 数据来源与致谢

价格历史来自 [IsThereAnyDeal API](https://docs.isthereanydeal.com/)，可核实购买项现价来自 Steam。请遵守 [ITAD API 条款](https://docs.isthereanydeal.com/#section/Terms-of-Service)；本项目与 ITAD、Valve 或 Steam 没有官方隶属关系，不抓取 SteamDB。

感谢 [Maoer233 / 猫耳](https://github.com/Maoer233) 的 [astrbot_plugins_steam_shop_price](https://github.com/Maoer233/astrbot_plugins_steam_shop_price) 提供灵感。本项目按独立需求重新实现，未复制该项目代码或素材。

开发接口参考：
- [AstrBot 插件最小示例](https://docs.astrbot.app/dev/star/guides/simple.html)
- [AstrBot 消息发送](https://docs.astrbot.app/dev/star/guides/send-message.html)
- [AstrBot 插件存储](https://docs.astrbot.app/dev/star/guides/storage.html)
- [IsThereAnyDeal API](https://docs.isthereanydeal.com/)


## v0.1.1 图片兼容性修正

- 默认单币种曲线为 1080×600，发送前转为 RGB JPEG，文件不超过 450 KiB，保留封面背景和月度刻度。
- 使用 AstrBot 的 `Image.fromBytes` 提前固定图片内容，不再依赖插件缓存文件在延迟发送时仍存在。损坏或丢失图片时仍返回价格文字。
- 去掉回复和默认图表中的来源提示；数据来源、范围及致谢仍在本文保留。史低只针对 Steam 已收录记录，不代表跨商店最低；不同币种不换算，缺失价格不视为免费。
- 同名候选中的已确认捆绑包独立列出，不再混入其他类似名字的游戏，也不当作本体或版本价格。名称相关不代表已经确认包含当前游戏。

“图片已过期”也可能来自 QQ 协议端的上传或消息缓存，不能仅凭占位图确定原因。本次验证了图片解码、体积上限和 AstrBot 字节组件调用；未连接真实 QQ/NapCat 环境。如果更新后小图仍失败，请核对 AstrBot 与协议端版本，并查看发送当时的图片上传错误日志（分享前删除密钥和账号隐私）。


## v0.1.2 中文名与游戏俗称

- **正式中文名**：两条独立检索路径，保留 ITAD 名称检索，补充 Steam 简体中文商店搜索（`l=schinese&cc=CN`），先核验 Steam 应用类型，再用精确 AppID 查询 ITAD。无需 SteamDB 爬取或机翻猜名字。两条检索目前依次执行，单条失败可降级使用另一条成功结果。
- **价格地区不变**：中文检索使用 CN 只是找应用，不使用搜索页的价格。例如 `/史低 JP 你的老婆` 找到应用后，现价与历史仍只查询 JP，不混入人民币价格。
- **自动首个，保留歧义**：实际候选中完整名称优先于包含匹配，其次保留上游顺序；同名不同 AppID 不合并。自动结果不是唯一事实，其他候选会显示，用户可直接用 AppID 改查。搜索不会把“这次首个”永久写成别名。
- **有限、可维护的俗称**：内置一小份人工核对的 AppID 映射；不是完整俗称词典，不承诺任意错别字、拼音、简称或民间译名都能搜到。仅作 NFKC 全半角、英文大小写与连续空白标准化后精确匹配，不做冒险的相似词自动替换。
- **自行扩充不改代码**：AstrBot 插件配置中的 `game_aliases` 填 JSON，例如 `{"我的神作":1245620,"同名游戏":[3339420,3616650]}`，保存并重载插件。数组最多 8 项，按填写顺序自动采用首个已核实应用，其余列为候选。`{"老头环":[]}` 可停用一个内置别名；管理者填写的别名优先。`edition_aliases` 仍只负责豪华版等版本词，不要混填。
- **缓存与限流**：名称结果缓存最长 24 小时，部分后端失败时只缓存 5 分钟；未成功查询不写“查无结果”缓存，更新别名立即改变缓存键。并发同查询合并；一次最多核验 8 个 Steam 搜索项或别名 AppID。Steam 搜索 429 会等待至少 60 秒或服务端更长的 Retry-After，不连续重试。通用元数据缓存最多 2048 条，价格历史与地区状态不因此删除。

已在官方 Steam 简体中文搜索核实：`你的老婆` 对应 AppID `3339420`（英文 `your wife`），同一中文名还出现 `3616650`（英文 `Wife`），所以插件保留同名歧义，而不是把中文名硬编码成唯一 ID。商店搜索是公开但非稳定官方 Web API 合约；地区限制、索引缺失或上游不收录可能使某个名称仍无法解析，可回退到 AppID / 商店链接。需要有效的 ITAD API Key 才能查询价格。


内置俗称映射的核对记录（2026-10-20，官方 `appdetails?appids=…&l=schinese&cc=CN` 均返回 `success=true`、`type=game`；俗称本身是人工整理，不是 Steam 官方别名声明）：

- 老头环 → [艾尔登法环，1245620](https://store.steampowered.com/app/1245620/)
- 大表哥2 → [Red Dead Redemption 2，1174180](https://store.steampowered.com/app/1174180/)
- 博德3 → [博德之门3，1086940](https://store.steampowered.com/app/1086940/)
- 空轨FC → [Trails in the Sky，251150](https://store.steampowered.com/app/251150/)
- 空轨SC → [Trails in the Sky SC，251290](https://store.steampowered.com/app/251290/)
- 空轨3rd → [Trails in the Sky the 3rd，436670](https://store.steampowered.com/app/436670/)

- 空轨1st → [空之轨迹 the 1st 中文版，3447040](https://store.steampowered.com/app/3447040/)，另列 [国际版 Trails in the Sky 1st Chapter，3375780](https://store.steampowered.com/app/3375780/)（该国际版未列中文支持）。两者是不同 AppID，不合并价格。
- 空轨2nd → [空之轨迹 the 2nd，4225980](https://store.steampowered.com/app/4225980/)
- 空轨重制版 → 按上述顺序保留 3447040、3375780、4225980 三个候选。

“空轨”是系列简称，依次使用 251150、251290、436670、3447040、3375780、4225980 六个候选（原 FC、SC、3rd、重制首作中文版、国际版、重制第二作），不把整个系列当成一个游戏或合并价格历史。具体词条顺序可以用 `game_aliases` 覆盖；精确查某一部建议用上述带 FC / SC / 3rd / 1st / 2nd 的别名或 AppID。这些映射不保证某个地区可购买，也不代表支持中文，实际价格与地区由原查询决定。
