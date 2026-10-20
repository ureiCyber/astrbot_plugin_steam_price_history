"""Exact curated/configurable aliases, not a translation or typo dictionary."""
import json
from pathlib import Path
import re
import unicodedata

MAX_ALIAS_APPIDS = 8
CATALOGUE_PATH = Path(__file__).with_name('alias_catalogue.json')


def normalize_name(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def load_catalogue(path=CATALOGUE_PATH) -> dict[str, list[int]]:
    """Load local reviewed data only; never search or infer aliases at runtime.

    Each ID must have a dated official Steam identity record. Alias evidence is
    recorded separately: an official title check does not endorse a nickname.
    Fail closed for malformed packaged data rather than silently remapping it.
    """
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
                or data['schema_version'] != 1):
            raise ValueError()
        apps, entries = data.get('apps'), data.get('aliases')
        if not isinstance(apps, dict) or not isinstance(entries, list) or not entries:
            raise ValueError()
        for ident, app in apps.items():
            if not re.fullmatch(r'[1-9][0-9]{0,9}', ident) or not isinstance(app, dict):
                raise ValueError()
            if (type(app.get('appid')) is not int or app['appid'] != int(ident)
                    or app.get('type') not in {'game', 'dlc'}
                    or not isinstance(app.get('title'), str) or not app['title'].strip()
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', app.get('verified_on', ''))
                    or app.get('source') not in {
                        f'https://store.steampowered.com/api/appdetails?appids={ident}&l=schinese&cc=CN',
                        f'https://store.steampowered.com/api/appdetails?appids={ident}&l=schinese&cc=US'}):
                raise ValueError()
        aliases = {}
        referenced = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError()
            names, ids = entry.get('names'), entry.get('appids')
            if (not isinstance(names, list) or not names
                    or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ALIAS_APPIDS
                    or any(type(ident) is not int or str(ident) not in apps for ident in ids)
                    or len(set(ids)) != len(ids)
                    or not isinstance(entry.get('basis'), str) or not entry['basis'].strip()):
                raise ValueError()
            for name in names:
                if not isinstance(name, str) or not normalize_name(name):
                    raise ValueError()
                normalized = normalize_name(name)
                if normalized in aliases:
                    raise ValueError()
                aliases[normalized] = list(ids)
            referenced.update(str(ident) for ident in ids)
        if referenced != set(apps):
            raise ValueError()
        return aliases
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('内置游戏别名目录无效，请重新安装完整插件。') from None


# Kept separate from user configuration and purchase-edition aliases.
CURATED_ALIASES = load_catalogue()


def alias_map(custom: dict) -> dict[str, list[int]]:
    result = {}
    # Normalize the two layers separately so custom spellings always override
    # their builtin equivalent, including case/full-width/space variants.
    for name, value in [*CURATED_ALIASES.items(), *custom.items()]:
        if not isinstance(name, str) or not normalize_name(name):
            raise ValueError('game_aliases 的别名必须是非空字符串。')
        values = value if isinstance(value, list) else [value]
        if len(values) > MAX_ALIAS_APPIDS or any(isinstance(v, bool) or not re.fullmatch(r'[1-9][0-9]{0,9}', str(v)) for v in values):
            raise ValueError('game_aliases 的值必须是正整数 AppID 或最多 8 个 AppID 的数组。')
        # An empty list explicitly disables an unwanted curated alias.
        result[normalize_name(name)] = list(dict.fromkeys(int(v) for v in values))
    return result


def name_rank(title: str, query: str) -> int:
    title, query = normalize_name(title), normalize_name(query)
    return 0 if title == query else 1 if query in title else 2
