import unittest

from price_history.query import QueryError, parse_query, valid_country


class QueryTests(unittest.TestCase):
    def test_default_country_and_edition(self):
        query = parse_query("/史低 Portal 2")
        self.assertEqual((query.country, query.title, query.edition), ("CN", "Portal 2", "standard"))

    def test_positional_country_and_chinese_edition(self):
        query = parse_query("/史低 US 黄金版 Assassin's Creed Shadows")
        self.assertEqual((query.country, query.edition, query.title), ("US", "gold", "Assassin's Creed Shadows"))

    def test_english_multiword_edition(self):
        query = parse_query("CN digital deluxe edition Baldur's Gate 3")
        self.assertEqual((query.edition, query.title), ("deluxe", "Baldur's Gate 3"))

    def test_quotes(self):
        query = parse_query('CN "digital deluxe edition" "Baldur\'s Gate 3"')
        self.assertEqual((query.edition, query.title), ("deluxe", "Baldur's Gate 3"))

    def test_numeric_appid(self):
        self.assertEqual(parse_query("1245620").appid, 1245620)

    def test_steam_url(self):
        query = parse_query("JP https://store.steampowered.com/app/1245620/ELDEN_RING/?l=english")
        self.assertEqual((query.country, query.appid), ("JP", 1245620))

    def test_steam_url_without_scheme(self):
        self.assertEqual(parse_query("store.steampowered.com/app/620/").appid, 620)

    def test_non_app_urls_rejected(self):
        for text in (
            "https://store.steampowered.com/sub/1/", "https://store.steampowered.com/bundle/1/",
            "https://store.steampowered.com.evil.example/app/620/", "https://evil.example/app/620/",
            "https://user:password@store.steampowered.com/app/620/", "https://store.steampowered.com:wrong/app/620/",
        ):
            with self.subTest(text=text), self.assertRaises(QueryError):
                parse_query(text)

    def test_zero_appid_rejected(self):
        with self.assertRaises(QueryError):
            parse_query("0")

    def test_it_takes_two_is_not_italy(self):
        query = parse_query("IT Takes Two")
        self.assertEqual((query.country, query.title), ("CN", "IT Takes Two"))

    def test_explicit_italy_it_takes_two(self):
        query = parse_query("IT IT Takes Two")
        self.assertEqual((query.country, query.title), ("IT", "IT Takes Two"))

    def test_lowercase_words_are_not_countries(self):
        query = parse_query("It Takes Two")
        self.assertEqual((query.country, query.title), ("CN", "It Takes Two"))
        self.assertEqual(parse_query("us Game").title, "us Game")

    def test_unknown_uppercase_country_errors(self):
        with self.assertRaises(QueryError):
            parse_query("ZZ Portal 2")

    def test_unknown_chinese_edition_errors(self):
        with self.assertRaises(QueryError):
            parse_query("CN 至尊超神版 Game")

    def test_unknown_english_edition_errors(self):
        with self.assertRaises(QueryError):
            parse_query("CN Supreme Edition Game")
        with self.assertRaises(QueryError):
            parse_query('CN "Supreme Edition" Game')

    def test_upgrade_alias_is_not_complete_edition(self):
        self.assertEqual(parse_query("CN 豪华升级包 Game").edition, "deluxe-upgrade")
        self.assertEqual(parse_query("CN gold upgrade Game").edition, "gold-upgrade")
        self.assertEqual(parse_query("CN 金库版升级 Game").edition, "vault-upgrade")
        self.assertEqual(parse_query("CN 金库升级 Game").edition, "vault-upgrade")

    def test_configured_alias_mapping(self):
        query = parse_query("至尊版 Game", edition_aliases={"至尊版": "ultimate"})
        self.assertEqual(query.edition, "ultimate")

    def test_configured_alias_lists(self):
        query = parse_query("至尊版 Game", edition_aliases={"ultimate": ["至尊版"]})
        self.assertEqual(query.edition, "ultimate")

    def test_alias_value_can_be_existing_builtin_alias(self):
        self.assertEqual(parse_query("至尊版 Game", edition_aliases={"至尊版": "终极版"}).edition, "ultimate")

    def test_invalid_and_incomplete_commands(self):
        for text in ("", "/史低", "/史低 CN", "/史低 CN 豪华版", '"unclosed'):
            with self.subTest(text=text), self.assertRaises(QueryError):
                parse_query(text)

    def test_default_country_is_validated(self):
        self.assertEqual(parse_query("Game", default_country="us").country, "US")
        with self.assertRaises(QueryError):
            parse_query("Game", default_country="ZZ")

    def test_iso_country_validation(self):
        self.assertTrue(all(valid_country(code) for code in ("CN", "US", "IT", "TR", "GB", "HK", "TW", "BQ")))
        self.assertFalse(any(valid_country(code) for code in ("cn", "UK", "ZZ", "CNY", "")))


if __name__ == "__main__":
    unittest.main()
