from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest
import warnings

from price_history.rendering import (
    RenderingError, build_history_series, format_candidates, format_money,
    format_price_summary, render_history_chart,
)


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def row(days_ago, amount, currency="CNY", raw=False):
    value = {"amount": amount, "currency": currency} if amount is not None else None
    record = {"timestamp": (NOW - timedelta(days=days_ago)).isoformat()}
    if raw:
        record["deal"] = {"price": value} if value is not None else None
    else:
        record["price"] = value
    return record


class HistorySeriesTests(unittest.TestCase):
    def test_pre_year_baseline_is_kept(self):
        series = build_history_series([row(500, 100), row(20, 50)], now=NOW)
        self.assertEqual(len(series.segments), 1)
        self.assertEqual(series.segments[0].timestamps[0], NOW - timedelta(days=365))
        self.assertEqual(series.segments[0].amounts, (100, 50, 50))
        self.assertEqual(series.segments[0].timestamps[-1], NOW)

    def test_latest_pre_year_state_wins(self):
        series = build_history_series([row(500, 100), row(400, 80), row(30, 60)], now=NOW)
        self.assertEqual(series.segments[0].amounts[0], 80)

    def test_no_backward_extrapolation_without_baseline(self):
        series = build_history_series([row(30, 60)], now=NOW)
        self.assertEqual(series.segments[0].timestamps[0], NOW - timedelta(days=30))

    def test_null_splits_segments_not_free(self):
        series = build_history_series([row(500, 100), row(100, None), row(20, 50)], now=NOW)
        self.assertEqual(len(series.segments), 2)
        self.assertEqual(series.segments[0].timestamps[-1], NOW - timedelta(days=100))
        self.assertEqual(series.segments[1].timestamps[0], NOW - timedelta(days=20))
        self.assertTrue(all(amount > 0 for segment in series.segments for amount in segment.amounts))

    def test_raw_deal_shape_preserves_null_gaps(self):
        series = build_history_series([row(500, 100, raw=True), row(100, None, raw=True), row(20, 50, raw=True)], now=NOW)
        self.assertEqual(len(series.segments), 2)

    def test_repeated_sales_in_same_month_are_not_aggregated(self):
        series = build_history_series([
            row(30, 100), row(18, 50), row(15, 100), row(10, 40), row(5, 100),
        ], now=NOW)
        self.assertEqual(series.segments[0].amounts, (100, 50, 100, 40, 100, 100))
        self.assertEqual(series.segments[0].timestamps[1:5], tuple(NOW - timedelta(days=d) for d in (18, 15, 10, 5)))

    def test_null_baseline_is_unknown_not_previous_price(self):
        series = build_history_series([row(500, 100), row(400, None), row(20, 50)], now=NOW)
        self.assertEqual(series.segments[0].timestamps[0], NOW - timedelta(days=20))

    def test_explicit_free_price_is_zero(self):
        series = build_history_series([row(500, 100), row(10, 0)], now=NOW)
        self.assertEqual(series.segments[0].amounts[-1], 0)

    def test_currency_change_does_not_connect(self):
        series = build_history_series([row(500, 100, "CNY"), row(30, 10, "USD")], now=NOW)
        self.assertEqual(series.currencies, ("CNY", "USD"))
        self.assertEqual(len(series.segments), 2)
        self.assertEqual(series.segments[0].amounts, (100, 100))
        self.assertEqual(series.segments[1].amounts, (10, 10))

    def test_duplicate_timestamp_uses_last_record(self):
        series = build_history_series([row(30, 50), row(30, 40)], now=NOW)
        self.assertEqual(series.segments[0].amounts, (40, 40))

    def test_unsorted_and_future_records(self):
        series = build_history_series([row(-5, 1), row(20, 40), row(400, 90)], now=NOW)
        self.assertEqual(series.segments[0].amounts, (90, 40, 40))

    def test_nonfinite_and_negative_prices_are_gaps(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=value):
                series = build_history_series([row(500, 100), row(20, value)], now=NOW)
                self.assertEqual(series.segments[0].timestamps[-1], NOW - timedelta(days=20))

    def test_empty_records_are_valid_empty_series(self):
        self.assertEqual(build_history_series([], now=NOW).segments, ())

    def test_invalid_window_errors(self):
        for days in (0, -1, True, 1.5):
            with self.subTest(days=days), self.assertRaises(RenderingError):
                build_history_series([], now=NOW, days=days)


class TextRenderingTests(unittest.TestCase):
    def test_missing_is_not_free(self):
        self.assertEqual(format_money(None), "暂无数据")
        self.assertEqual(format_money({"amount": None, "currency": "CNY"}), "暂无数据")
        self.assertIn("免费", format_money({"amount": 0, "currency": "CNY"}))

    def test_summary_prices_discount_and_steam_scope(self):
        text = format_price_summary({
            "title": "Game", "country": "CN", "edition": "gold",
            "current": {"price": {"amount": 50, "currency": "CNY"}, "regular": {"amount": 100, "currency": "CNY"}, "cut": 50},
            "lowest": {"price": {"amount": 40, "currency": "CNY"}, "cut": 60, "timestamp": "2026-05-01T00:00:00Z"},
            "fetched_at": "2026-10-20T00:00:00Z",
        })
        self.assertIn("黄金版", text)
        self.assertIn("国区价格: ¥50.00 -50%", text)
        self.assertIn("史低: ¥40.00 -60%", text)
        self.assertIn("Steam 历史最低", text)
        self.assertNotIn("全店", text)
        self.assertIn("2026-05-01", text)
        self.assertIn("数据获取时间", text)

    def test_original_compact_field_order(self):
        text = format_price_summary({
            "title": "Original Game", "country": "CN", "edition": "standard",
            "current": {"price": {"amount": 98, "currency": "CNY"}},
            "lowest": {"price": {"amount": 29.4, "currency": "CNY"}, "cut": 70},
            "rating": 76, "store_url": "https://store.steampowered.com/app/981750",
        })
        self.assertEqual(text.splitlines()[:5], [
            "Original Game", "国区价格: ¥98.00", "史低: ¥29.40 -70%", "好评率:76%",
            "steam商店链接: https://store.steampowered.com/app/981750",
        ])

    def test_lowest_does_not_borrow_current_discount_or_regular(self):
        text = format_price_summary({
            "current": {"price": {"amount": 50, "currency": "CNY"}, "regular": {"amount": 100, "currency": "CNY"}, "cut": 50},
            "lowest": {"price": {"amount": 20, "currency": "CNY"}},
        })
        self.assertEqual(text.splitlines()[2], "史低: ¥20.00")

    def test_current_full_price_has_no_discount_suffix(self):
        text = format_price_summary({"current": {"price": {"amount": 98, "currency": "CNY"}, "cut": 0}})
        self.assertEqual(text.splitlines()[1], "国区价格: ¥98.00")

    def test_lowest_discount_can_use_own_regular_price(self):
        text = format_price_summary({"lowest": {
            "price": {"amount": 20, "currency": "CNY"}, "regular": {"amount": 100, "currency": "CNY"},
        }})
        self.assertEqual(text.splitlines()[2], "史低: ¥20.00 -80%")

    def test_other_region_and_unknown_rating(self):
        text = format_price_summary({"title": "Game", "country": "US", "rating": None})
        self.assertEqual(text.splitlines()[1], "US区价格: 暂无数据")
        self.assertEqual(text.splitlines()[3], "好评率:暂无数据")
        self.assertEqual(text.splitlines()[4], "steam商店链接: 暂无数据")

    def test_discount_computed_only_same_currency(self):
        text = format_price_summary({"current": {"price": {"amount": 50, "currency": "CNY"}, "regular": {"amount": 100, "currency": "USD"}}})
        self.assertNotIn("-50%", text)

    def test_stale_cache_is_explicit(self):
        text = format_price_summary({"title": "Game", "stale": True, "fetched_at": "2026-01-01T00:00:00Z"})
        self.assertIn("过期缓存", text)
        self.assertIn("不代表实时价格", text)
        self.assertIn("2026-01-01", text)

    def test_versions_lowest_and_upgrade_note(self):
        text = format_price_summary({"versions": [{"title": "Deluxe Upgrade", "price": {"amount": 20, "currency": "CNY"}, "lowest": {"amount": 10, "currency": "CNY"}, "is_upgrade": True, "note": "需拥有本体"}]})
        self.assertIn("史低 CNY 10.00", text)
        self.assertIn("升级包", text)
        self.assertIn("需拥有本体", text)

    def test_candidates_include_appids(self):
        text = format_candidates([{"title": "Portal", "appid": 400}, {"title": "Portal 2", "appid": 620}], query="Portal")
        self.assertIn("AppID 400", text)
        self.assertIn("AppID 620", text)
        self.assertEqual(text.splitlines()[0], "还有其他类似名字的游戏：")

    def test_candidates_deduplicate_id_or_title(self):
        text = format_candidates([
            {"id": "a", "title": "Portal", "appid": 400},
            {"id": "a", "title": "Portal other spelling", "appid": 400},
            {"id": "b", "title": "portal", "appid": 400},
            {"id": "c", "title": "Portal 2", "appid": 620},
        ])
        self.assertEqual(len(text.splitlines()), 3)
        self.assertEqual(text.count("AppID 400"), 1)


class ChartRenderingTests(unittest.TestCase):
    def test_png_chart_created_for_multicurrency_gaps(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = render_history_chart(
                [row(500, 100), row(80, None), row(40, 50), row(10, 10, "USD")],
                Path(temporary) / "nested" / "chart.png", title="Demo Game", now=NOW,
            )
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(path.stat().st_size, 10000)

    def test_empty_history_chart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = render_history_chart([], Path(temporary) / "chart.png", title="No Data", now=NOW)
            self.assertTrue(path.exists())

    def test_local_cover_background_renders(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            background = Path(temporary) / "cover.png"
            Image.new("RGB", (600, 300), (160, 50, 20)).save(background)
            plain = render_history_chart([row(30, 60)], Path(temporary) / "plain.png", title="Cover Demo", now=NOW)
            covered = render_history_chart([row(30, 60)], Path(temporary) / "covered.png", title="Cover Demo", now=NOW, background_path=background)
            self.assertNotEqual(plain.read_bytes(), covered.read_bytes())

    def test_missing_cover_falls_back_without_breaking_chart(self):
        with tempfile.TemporaryDirectory() as temporary, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            path = render_history_chart([], Path(temporary) / "chart.png", title="Demo", now=NOW, background_path=Path(temporary) / "missing.jpg")
            self.assertTrue(path.exists())
            self.assertTrue(any("without background" in str(item.message) for item in caught))

    def test_missing_configured_font_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(RenderingError, "font does not exist"):
            render_history_chart([], Path(temporary) / "chart.png", title="Demo", now=NOW, font_path=Path(temporary) / "missing.ttf")

    def test_cjk_fallback_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            path = render_history_chart([row(5, 10)], Path(temporary) / "chart.png", title="中文游戏标题", now=NOW, font_families=["DejaVu Sans"])
            self.assertTrue(path.exists())
            self.assertTrue(any("CJK" in str(item.message) for item in caught))


if __name__ == "__main__":
    unittest.main()
