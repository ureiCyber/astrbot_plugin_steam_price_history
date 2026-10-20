"""Parse the small, deliberately strict ``/史低`` command language.

Only an uppercase ISO 3166-1 alpha-2 code in the first argument selects a
country. Edition words are positional, before the game name. To avoid silently
looking up a base game, edition-looking but unknown prefixes are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from typing import Iterable, Mapping
from urllib.parse import urlparse


class QueryError(ValueError):
    """The command cannot be interpreted without guessing."""


@dataclass(frozen=True)
class Query:
    country: str
    title: str
    edition: str = "standard"
    appid: int | None = None


# A fallback permits the parser to work in minimal installations. pycountry,
# when installed, remains the source of truth for assigned country codes.
_ISO_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB
GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE
IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB
LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS
MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH
PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK
SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV
TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

EDITION_ALIASES: dict[str, tuple[str, ...]] = {
    "standard": ("standard", "standard edition", "base", "普通版", "标准版", "本体"),
    "gold": ("gold", "gold edition", "黄金版"),
    "deluxe": ("deluxe", "deluxe edition", "digital deluxe", "digital deluxe edition", "豪华版", "数字豪华版"),
    "ultimate": ("ultimate", "ultimate edition", "终极版"),
    "vault": ("vault", "vault edition", "金库版"),
    "premium": ("premium", "premium edition", "高级版"),
    "complete": ("complete", "complete edition", "完整版"),
    "definitive": ("definitive", "definitive edition", "决定版", "最终版"),
    "goty": ("goty", "game of the year", "game of the year edition", "年度版"),
    "collectors": ("collectors", "collector's edition", "collectors edition", "收藏版", "典藏版"),
    "anniversary": ("anniversary", "anniversary edition", "周年版"),
}

EDITION_LABELS = {
    "standard": "标准版", "gold": "黄金版", "deluxe": "豪华版", "ultimate": "终极版",
    "vault": "金库版", "premium": "高级版", "complete": "完整版", "definitive": "决定版",
    "goty": "年度版", "collectors": "典藏版", "anniversary": "周年版",
}

# Keep upgrades distinct from complete editions, both in lookup and display.
for _kind, _label in tuple(EDITION_LABELS.items()):
    if _kind != "standard":
        EDITION_ALIASES[_kind + "-upgrade"] = (
            _kind + "-upgrade", _kind + " upgrade", _kind + " edition upgrade",
            _label + "升级包", _label.removesuffix("版") + "升级包",
            _label.removesuffix("版") + "升级版", _label + "升级",
            _label.removesuffix("版") + "升级",
        )
        EDITION_LABELS[_kind + "-upgrade"] = _label + "升级包"


def valid_country(country: str) -> bool:
    """Whether a code is an assigned two-letter country, not a currency."""
    if not re.fullmatch(r"[A-Z]{2}", country):
        return False
    try:
        import pycountry
    except ImportError:
        return country in _ISO_CODES
    return pycountry.countries.get(alpha_2=country) is not None


def _alias_map(custom: Mapping[str, str | Iterable[str]] | None) -> dict[str, str]:
    aliases = {alias.casefold(): canonical for canonical, values in EDITION_ALIASES.items() for alias in values}
    if custom:
        for key, value in custom.items():
            # Support both {"至尊版": "ultimate"} and
            # {"ultimate": ["至尊版", "终极收藏版"]} configuration styles.
            if isinstance(value, str):
                canonical = value.strip().casefold()
                aliases[str(key).strip().casefold()] = aliases.get(canonical, canonical)
            elif isinstance(value, Iterable):
                for alias in value:
                    if isinstance(alias, str) and alias.strip():
                        aliases[alias.strip().casefold()] = str(key).strip().casefold()
    return {key: value for key, value in aliases.items() if key and value}


def edition_terms(edition: str) -> tuple[str, ...]:
    """Built-in aliases, useful when matching a result against the request."""
    return EDITION_ALIASES.get(edition, (edition,))


def _appid(title: str) -> int | None:
    if title.isascii() and title.isdecimal():
        value = int(title)
        if value <= 0:
            raise QueryError("Steam AppID 必须是正整数。")
        return value
    if re.match(r"^(?:https?://|www\.|store\.steampowered\.com)", title, re.I):
        candidate = title if "://" in title else "https://" + title
        parsed = urlparse(candidate)
        if parsed.hostname not in {"store.steampowered.com", "www.store.steampowered.com"}:
            raise QueryError("请提供 Steam 商店的 /app/ 游戏链接，或直接输入游戏名。")
        if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise QueryError("Steam 游戏链接格式无效。")
        match = re.fullmatch(r"/app/([1-9]\d*)(?:/[^\s]*)?", parsed.path)
        if not match or re.search(r"\s", title):
            raise QueryError("只支持 Steam /app/ 链接，不支持礼包或订阅链接。")
        return int(match.group(1))
    return None


def parse_query(
    text: str,
    *,
    default_country: str = "CN",
    edition_aliases: Mapping[str, str | Iterable[str]] | None = None,
) -> Query:
    """Parse a complete command or its argument text.

    Examples: ``CN 豪华版 艾尔登法环``, ``US 1245620``, ``Portal 2``.
    ``IT Takes Two`` is kept intact because its title starts with Italy's code;
    use ``IT IT Takes Two`` to explicitly request its Italian prices.
    """
    if not isinstance(text, str):
        raise QueryError("请输入游戏名称、Steam AppID 或 Steam 游戏链接。")
    country = str(default_country).strip().upper()
    if not valid_country(country):
        raise QueryError("默认国家代码无效，请使用 ISO 两位代码，例如 CN、US、JP。")
    content = text.strip()
    if content.startswith("/史低"):
        content = content[len("/史低"):].strip()
    elif content.startswith("史低") and (len(content) == 2 or content[2].isspace()):
        content = content[2:].strip()
    if not content:
        raise QueryError("用法：/史低 [国家代码] [版本] 游戏名、AppID 或 Steam 链接")
    # posix=False preserves apostrophes in titles such as Assassin's Creed.
    # Quotes wrapping an entire argument are removed without treating embedded
    # apostrophes as shell syntax.
    try:
        tokens = shlex.split(content, posix=False)
    except ValueError as exc:
        raise QueryError("引号未闭合，请检查游戏名称。") from exc
    tokens = [token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token for token in tokens]
    protected_title = re.match(r"^it takes two(?:\s|$)", " ".join(tokens).casefold()) is not None
    first = tokens[0]
    if re.fullmatch(r"[A-Z]{2}", first) and not protected_title:
        if not valid_country(first):
            raise QueryError(f"未知国家代码 {first}。请使用 ISO 两位代码，例如 CN、US、JP。")
        country = tokens.pop(0)
    if not tokens:
        raise QueryError("国家代码后还需要游戏名称、AppID 或 Steam 链接。")

    aliases = _alias_map(edition_aliases)
    edition = "standard"
    # Longest prefix wins: 'digital deluxe edition' must not leave 'edition'
    # at the beginning of the title.
    match: tuple[int, str] | None = None
    for count in range(1, min(len(tokens), 6) + 1):
        value = aliases.get(" ".join(tokens[:count]).casefold())
        if value is not None:
            match = (count, value)
    if match:
        count, edition = match
        tokens = tokens[count:]
    elif tokens[0].endswith(("版", "升级包")) or tokens[0].casefold().endswith(" edition") or (len(tokens) > 1 and tokens[1].casefold() == "edition"):
        raise QueryError(f"不认识版本“{tokens[0]}”。请使用已配置的版本别名，不能默认按标准版查询。")
    elif tokens[0].casefold().startswith(("edition=", "版本=")):
        raise QueryError("请把已配置的版本名放在游戏名之前，例如 /史低 CN 豪华版 游戏名。")
    if not tokens:
        raise QueryError("版本后还需要游戏名称、AppID 或 Steam 链接。")
    title = " ".join(tokens).strip()
    try:
        appid = _appid(title)
    except ValueError as exc:
        if isinstance(exc, QueryError):
            raise
        raise QueryError("Steam 游戏链接格式无效。") from exc
    return Query(country=country, title=title, edition=edition, appid=appid)
