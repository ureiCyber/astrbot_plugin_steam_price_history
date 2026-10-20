# 游戏别名目录与核对记录

v0.1.3：40 组、51 个标准化后精确拼写，涉及 47 个独立 Steam AppID。该目录是小范围人工整理，不是完整词典，也不是 Steam 官方公布的别名表。

## 使用与顺序

- `/史低 草猫`、`/史低 艹猫`、`/史低 NEKOPARA`、`/史低 猫娘乐园`：按 Vol.1、Vol.2、Vol.3、Vol.4、Vol.0、Extra、After、Catboys Paradise 顺序核验 8 个本体。自动使用首个 ITAD 能精确核验的候选，其他候选附在最终回复中，不要求再确认；不把系列史低合并。
- `/史低 草猫0` 到 `/史低 草猫4`，以及对应 `艹猫0` 到 `艹猫4`，只查对应卷。`草猫Extra` / `艹猫Extra`、`草猫After` / `艹猫After` 同理。Catboys Paradise 是衍生作，不是 Vol.5；动画、DLC、付费补丁不混入系列本体列表。
- `/史低 闪轨` 保留四部作品各自的亚洲和国际发行 AppID，按章节顺序、每部亚洲版在前。`闪轨1` 至 `闪轨4` 只保留该部的两个 AppID。零轨、碧轨、创轨、黎轨同样不把不同发行 AppID 合并。亚洲/国际只是目录排序说明，不保证所有语言、地区均可购买。
- `夏日口袋` 先列 REFLECTION BLUE，再列原版；`夏日口袋RB` 只查 RB。`星之梦` 先列 HD，再列原版，不隐含前传 Snow Globe。`罚抄` 对应 Steam 本体 Rewrite+，不隐含独立 fan disc Harvest festa!。`CLANNAD` 不隐含 Side Stories 或智代 After。
- 只做 NFKC 全半角、英文大小写和连续空白标准化。不是拼音、错别字或相似词匹配，也不会自动删除所有空格。例如 `草猫４` 等价于 `草猫4`；未列出的 `草猫 4` 仍走一般名称搜索。
- 纯数字仍按 AppID 解析，所以没有添加会误导用户的 `2077` 别名；请用 `赛博朋克2077` 或完整标题。
- 管理员 `game_aliases` 覆盖同一个标准化拼写。`{"草猫":[]}` 仅停用 `草猫` 这一拼写，随后走一般名称搜索，不同时停用 `艹猫` 或 `NEKOPARA`。每个值最多 8 项，顺序由管理员决定。
- 这些别名只帮助识别 AppID。价格地区仍来自原查询，例如 `/史低 JP 艹猫2` 只查 JP 价格。Steam 身份核验成功不代表 ITAD 已收录，也不保证有史低、价格、中文或可购买状态。

## 维护与证据边界

数据文件为 [`price_history/alias_catalogue.json`](../price_history/alias_catalogue.json)，加载器为 [`price_history/names.py`](../price_history/names.py)。每个应用保存核验标题、类型、AppID、日期和实际使用的官方 `appdetails` URL；每组别名独立保存用法依据与参考链接。运行时仅加载本地数据，不抓取参考网页，不自动学习搜索结果。

身份核对使用 Steam 的公开商店 JSON，逐个检查 `success=true`、应用身份、标题与 `type=game`。本轮轨迹及大众游戏记录使用 `l=schinese&cc=CN`，NEKOPARA 与 Key 记录使用 `l=schinese&cc=US`；仅用于身份核验，没有把这些返回中的价格写入用户价格结果。该接口不是稳定的官方 Web API 合约。

来源区分：官方 Steam 记录证明应用身份；游戏官网可证明正式标题；社区文章或游戏档案只证明俗称确实有人使用，不能单独证明 AppID。`艹猫` / `草猫` 是明确提供的系列称呼，带卷号、副标题的写法由该称呼与官方卷号、副标题组成，不额外杜撰俗称。

新增记录时应：

1. 从官方 Steam 重新核实 AppID、标题和类型，排除 Demo、视频、软件及无关应用，不凭记忆填写数字。
2. 保留准确的日期与查询 URL，并为俗称记录用法依据。不要把玩家评论说成 Steam 官方别名。
3. 对系列或同作不同发行 AppID 保留确定的多候选顺序；超过 8 项时拆分为具体章节词条，不静默截掉或随机挑一部。
4. 不把不同 AppID 的价格历史合并，也不擅自把普通版、扩展版、fan disc、DLC 或合集当作同一个商品。
5. 运行完整测试。加载器拒绝重复标准化别名、未记录来源的 AppID、重复/超限列表及无用应用记录；损坏或漏装目录时明确失败，不以错误映射继续查询。

以下是 2026-10-20 核对快照。详细别名依据见 JSON 各条的 `basis` 与 `references`。

## 精确别名与候选顺序

| 拼写（忽略英文大小写/全半角） | AppID 顺序 |
| --- | --- |
| 老头环 | 1245620 |
| 大表哥2 | 1174180 |
| 博德3 | 1086940 |
| 空轨 | 251150, 251290, 436670, 3447040, 3375780, 4225980 |
| 空轨FC | 251150 |
| 空轨SC | 251290 |
| 空轨3rd | 436670 |
| 空轨1st | 3447040, 3375780 |
| 空轨2nd | 4225980 |
| 空轨重制版 | 3447040, 3375780, 4225980 |
| 艹猫、草猫、NEKOPARA、猫娘乐园 | 333600, 420110, 602520, 1406990, 385800, 899970, 3655150, 1593310 |
| 艹猫0、草猫0 | 385800 |
| 艹猫1、草猫1 | 333600 |
| 艹猫2、草猫2 | 420110 |
| 艹猫3、草猫3 | 602520 |
| 艹猫4、草猫4 | 1406990 |
| 艹猫Extra、草猫Extra | 899970 |
| 艹猫After、草猫After | 3655150 |
| CLANNAD | 324160 |
| 智代After、智代 After | 462990 |
| 小小克星 | 635940 |
| 罚抄 | 1680000 |
| 夏日口袋 | 3418570, 897220 |
| 夏日口袋RB | 3418570 |
| 星之梦 | 623080, 316720 |
| 零轨 | 1457520, 1668510 |
| 碧轨 | 1461920, 1668520 |
| 闪轨 | 1450120, 538680, 1450090, 748490, 1450080, 991270, 1457510, 1198090 |
| 闪轨1 | 1450120, 538680 |
| 闪轨2 | 1450090, 748490 |
| 闪轨3 | 1450080, 991270 |
| 闪轨4 | 1457510, 1198090 |
| 创轨 | 1562940, 1668540 |
| 黎轨 | 1811950, 2138610, 2113920, 2668430 |
| 黎轨1 | 1811950, 2138610 |
| 黎轨2 | 2113920, 2668430 |
| 赛博朋克2077 | 1091500 |
| P5R | 1687950 |
| 怪猎世界 | 582010 |
| 怪猎崛起 | 1446780 |

## 官方 Steam 身份记录

全部类型均为 `game`，核验日均为 2026-10-20。点击 AppID 查看商店，点击核验链接复查官方身份响应。

| AppID | 核验时的标题 | 官方身份来源 |
| --- | --- | --- |
| [251150](https://store.steampowered.com/app/251150/) | The Legend of Heroes: Trails in the Sky | [appdetails](https://store.steampowered.com/api/appdetails?appids=251150&l=schinese&cc=CN) |
| [251290](https://store.steampowered.com/app/251290/) | The Legend of Heroes: Trails in the Sky SC | [appdetails](https://store.steampowered.com/api/appdetails?appids=251290&l=schinese&cc=CN) |
| [316720](https://store.steampowered.com/app/316720/) | planetarian ~the reverie of a little planet~ | [appdetails](https://store.steampowered.com/api/appdetails?appids=316720&l=schinese&cc=US) |
| [324160](https://store.steampowered.com/app/324160/) | CLANNAD | [appdetails](https://store.steampowered.com/api/appdetails?appids=324160&l=schinese&cc=US) |
| [333600](https://store.steampowered.com/app/333600/) | NEKOPARA Vol. 1 | [appdetails](https://store.steampowered.com/api/appdetails?appids=333600&l=schinese&cc=US) |
| [385800](https://store.steampowered.com/app/385800/) | NEKOPARA Vol. 0 | [appdetails](https://store.steampowered.com/api/appdetails?appids=385800&l=schinese&cc=US) |
| [420110](https://store.steampowered.com/app/420110/) | NEKOPARA Vol. 2 | [appdetails](https://store.steampowered.com/api/appdetails?appids=420110&l=schinese&cc=US) |
| [436670](https://store.steampowered.com/app/436670/) | The Legend of Heroes: Trails in the Sky the 3rd | [appdetails](https://store.steampowered.com/api/appdetails?appids=436670&l=schinese&cc=CN) |
| [462990](https://store.steampowered.com/app/462990/) | Tomoyo After ~It's a Wonderful Life~ English Edition | [appdetails](https://store.steampowered.com/api/appdetails?appids=462990&l=schinese&cc=US) |
| [538680](https://store.steampowered.com/app/538680/) | The Legend of Heroes: Trails of Cold Steel | [appdetails](https://store.steampowered.com/api/appdetails?appids=538680&l=schinese&cc=CN) |
| [582010](https://store.steampowered.com/app/582010/) | Monster Hunter: World | [appdetails](https://store.steampowered.com/api/appdetails?appids=582010&l=schinese&cc=CN) |
| [602520](https://store.steampowered.com/app/602520/) | NEKOPARA Vol. 3 | [appdetails](https://store.steampowered.com/api/appdetails?appids=602520&l=schinese&cc=US) |
| [623080](https://store.steampowered.com/app/623080/) | planetarian HD | [appdetails](https://store.steampowered.com/api/appdetails?appids=623080&l=schinese&cc=US) |
| [635940](https://store.steampowered.com/app/635940/) | Little Busters! English Edition | [appdetails](https://store.steampowered.com/api/appdetails?appids=635940&l=schinese&cc=US) |
| [748490](https://store.steampowered.com/app/748490/) | The Legend of Heroes: Trails of Cold Steel II | [appdetails](https://store.steampowered.com/api/appdetails?appids=748490&l=schinese&cc=CN) |
| [897220](https://store.steampowered.com/app/897220/) | Summer Pockets | [appdetails](https://store.steampowered.com/api/appdetails?appids=897220&l=schinese&cc=US) |
| [899970](https://store.steampowered.com/app/899970/) | NEKOPARA Extra | [appdetails](https://store.steampowered.com/api/appdetails?appids=899970&l=schinese&cc=US) |
| [991270](https://store.steampowered.com/app/991270/) | The Legend of Heroes: Trails of Cold Steel III | [appdetails](https://store.steampowered.com/api/appdetails?appids=991270&l=schinese&cc=CN) |
| [1086940](https://store.steampowered.com/app/1086940/) | 博德之门3 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1086940&l=schinese&cc=CN) |
| [1091500](https://store.steampowered.com/app/1091500/) | 赛博朋克 2077 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1091500&l=schinese&cc=CN) |
| [1174180](https://store.steampowered.com/app/1174180/) | Red Dead Redemption 2 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1174180&l=schinese&cc=CN) |
| [1198090](https://store.steampowered.com/app/1198090/) | 英雄传说：闪之轨迹Ⅳ | [appdetails](https://store.steampowered.com/api/appdetails?appids=1198090&l=schinese&cc=CN) |
| [1245620](https://store.steampowered.com/app/1245620/) | 艾尔登法环 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1245620&l=schinese&cc=CN) |
| [1406990](https://store.steampowered.com/app/1406990/) | NEKOPARA Vol. 4 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1406990&l=schinese&cc=US) |
| [1446780](https://store.steampowered.com/app/1446780/) | MONSTER HUNTER RISE | [appdetails](https://store.steampowered.com/api/appdetails?appids=1446780&l=schinese&cc=CN) |
| [1450080](https://store.steampowered.com/app/1450080/) | 英雄传说：闪之轨迹III | [appdetails](https://store.steampowered.com/api/appdetails?appids=1450080&l=schinese&cc=CN) |
| [1450090](https://store.steampowered.com/app/1450090/) | 英雄传说：闪之轨迹II：改 –The Erebonian Civil War- | [appdetails](https://store.steampowered.com/api/appdetails?appids=1450090&l=schinese&cc=CN) |
| [1450120](https://store.steampowered.com/app/1450120/) | 英雄传说：闪之轨迹I：改 –Thors Military Academy 1204– | [appdetails](https://store.steampowered.com/api/appdetails?appids=1450120&l=schinese&cc=CN) |
| [1457510](https://store.steampowered.com/app/1457510/) | 英雄传说 闪之轨迹IV -THE END OF SAGA- | [appdetails](https://store.steampowered.com/api/appdetails?appids=1457510&l=schinese&cc=CN) |
| [1457520](https://store.steampowered.com/app/1457520/) | 英雄传说 零之轨迹：改 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1457520&l=schinese&cc=CN) |
| [1461920](https://store.steampowered.com/app/1461920/) | 英雄传说 碧之轨迹：改 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1461920&l=schinese&cc=CN) |
| [1562940](https://store.steampowered.com/app/1562940/) | 英雄传说 创之轨迹 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1562940&l=schinese&cc=CN) |
| [1593310](https://store.steampowered.com/app/1593310/) | 猫郎乐园 - Catboys Paradise | [appdetails](https://store.steampowered.com/api/appdetails?appids=1593310&l=schinese&cc=US) |
| [1668510](https://store.steampowered.com/app/1668510/) | The Legend of Heroes: Trails from Zero | [appdetails](https://store.steampowered.com/api/appdetails?appids=1668510&l=schinese&cc=CN) |
| [1668520](https://store.steampowered.com/app/1668520/) | The Legend of Heroes: Trails to Azure | [appdetails](https://store.steampowered.com/api/appdetails?appids=1668520&l=schinese&cc=CN) |
| [1668540](https://store.steampowered.com/app/1668540/) | The Legend of Heroes: Trails into Reverie | [appdetails](https://store.steampowered.com/api/appdetails?appids=1668540&l=schinese&cc=CN) |
| [1680000](https://store.steampowered.com/app/1680000/) | Rewrite+ | [appdetails](https://store.steampowered.com/api/appdetails?appids=1680000&l=schinese&cc=US) |
| [1687950](https://store.steampowered.com/app/1687950/) | 女神异闻录5皇家版 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1687950&l=schinese&cc=CN) |
| [1811950](https://store.steampowered.com/app/1811950/) | 英雄传说 黎之轨迹 | [appdetails](https://store.steampowered.com/api/appdetails?appids=1811950&l=schinese&cc=CN) |
| [2113920](https://store.steampowered.com/app/2113920/) | 英雄传说 黎之轨迹Ⅱ -绯红原罪- | [appdetails](https://store.steampowered.com/api/appdetails?appids=2113920&l=schinese&cc=CN) |
| [2138610](https://store.steampowered.com/app/2138610/) | The Legend of Heroes: Trails through Daybreak | [appdetails](https://store.steampowered.com/api/appdetails?appids=2138610&l=schinese&cc=CN) |
| [2668430](https://store.steampowered.com/app/2668430/) | The Legend of Heroes: Trails through Daybreak II | [appdetails](https://store.steampowered.com/api/appdetails?appids=2668430&l=schinese&cc=CN) |
| [3375780](https://store.steampowered.com/app/3375780/) | Trails in the Sky 1st Chapter | [appdetails](https://store.steampowered.com/api/appdetails?appids=3375780&l=schinese&cc=CN) |
| [3418570](https://store.steampowered.com/app/3418570/) | Summer Pockets REFLECTION BLUE | [appdetails](https://store.steampowered.com/api/appdetails?appids=3418570&l=schinese&cc=US) |
| [3447040](https://store.steampowered.com/app/3447040/) | 空之轨迹 the 1st | [appdetails](https://store.steampowered.com/api/appdetails?appids=3447040&l=schinese&cc=CN) |
| [3655150](https://store.steampowered.com/app/3655150/) | NEKOPARA After | [appdetails](https://store.steampowered.com/api/appdetails?appids=3655150&l=schinese&cc=US) |
| [4225980](https://store.steampowered.com/app/4225980/) | 空之轨迹 the 2nd | [appdetails](https://store.steampowered.com/api/appdetails?appids=4225980&l=schinese&cc=CN) |
