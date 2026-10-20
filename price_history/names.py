"""Exact curated/configurable aliases, not a translation or typo dictionary."""
import re
import unicodedata

# Steam AppIDs are verified against official appdetails; see README.
CURATED_ALIASES = {
    '老头环': 1245620, '大表哥2': 1174180, '博德3': 1086940,
    '空轨': [251150, 251290, 436670, 3447040, 3375780, 4225980],
    '空轨fc': 251150, '空轨sc': 251290, '空轨3rd': 436670,
    '空轨1st': [3447040, 3375780], '空轨2nd': 4225980,
    '空轨重制版': [3447040, 3375780, 4225980],
}


def normalize_name(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def alias_map(custom: dict) -> dict[str, list[int]]:
    result = {}
    for name, value in {**CURATED_ALIASES, **custom}.items():
        if not isinstance(name, str) or not normalize_name(name):
            raise ValueError('game_aliases 的别名必须是非空字符串。')
        values = value if isinstance(value, list) else [value]
        if len(values) > 8 or any(isinstance(v, bool) or not re.fullmatch(r'[1-9][0-9]{0,9}', str(v)) for v in values):
            raise ValueError('game_aliases 的值必须是正整数 AppID 或最多 8 个 AppID 的数组。')
        # An empty list explicitly disables an unwanted curated alias.
        result[normalize_name(name)] = list(dict.fromkeys(int(v) for v in values))
    return result


def name_rank(title: str, query: str) -> int:
    title, query = normalize_name(title), normalize_name(query)
    return 0 if title == query else 1 if query in title else 2
