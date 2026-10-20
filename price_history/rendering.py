"""Truthful text and headless, gap-aware price-history rendering.

The history endpoint describes state changes rather than daily observations.
The final observation before the visible year is therefore kept as a baseline.
An explicit null deal/price ends a segment; it is never plotted as free.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
import threading
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
import warnings

from .query import EDITION_LABELS

UTC = timezone.utc
_PLOT_LOCK = threading.RLock()
_CJK_FONTS = (
    "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
    "Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS",
)


class RenderingError(ValueError):
    """The supplied data cannot form a usable chart."""


@dataclass(frozen=True)
class HistorySegment:
    currency: str
    timestamps: tuple[datetime, ...]
    amounts: tuple[float, ...]


@dataclass(frozen=True)
class HistorySeries:
    start: datetime
    end: datetime
    segments: tuple[HistorySegment, ...]

    @property
    def currencies(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(segment.currency for segment in self.segments))


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _price(value: Any) -> tuple[float, str] | None:
    """Read normalized prices, ITAD deal objects, or raw history rows."""
    if not isinstance(value, Mapping):
        return None
    if "deal" in value:
        return _price(value.get("deal"))
    if "price" in value:
        return _price(value.get("price"))
    amount = _number(value.get("amount"))
    currency = value.get("currency")
    if amount is None or not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
        return None
    return amount, currency.upper()


def build_history_series(
    records: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    days: int = 365,
) -> HistorySeries:
    """Build separate constant-price segments without bridging missing data.

    Both ``{timestamp, price}`` and ITAD's ``{timestamp, deal: {price}}``
    representation are accepted. Invalid prices are treated as unknown states,
    not discarded in a way that would extend an obsolete price over them.
    Duplicate timestamps use the last input record deterministically.
    """
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise RenderingError("days must be a positive integer")
    end = _datetime(now) if now is not None else datetime.now(UTC)
    if end is None:
        raise RenderingError("now must be a datetime")
    start = end - timedelta(days=days)
    observations: dict[datetime, tuple[float, str] | None] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        timestamp = _datetime(record.get("timestamp") or record.get("date"))
        if timestamp is not None and timestamp <= end:
            observations[timestamp] = _price(record)
    ordered = sorted(observations.items())
    baseline: tuple[float, str] | None = None
    within: list[tuple[datetime, tuple[float, str] | None]] = []
    for timestamp, price in ordered:
        if timestamp <= start:
            baseline = price
        else:
            within.append((timestamp, price))
    states = [(start, baseline), *within]
    segments: list[HistorySegment] = []
    times: list[datetime] = []
    amounts: list[float] = []
    current_currency: str | None = None

    def finish(timestamp: datetime) -> None:
        nonlocal times, amounts, current_currency
        if times and current_currency:
            if times[-1] < timestamp:
                times.append(timestamp)
                amounts.append(amounts[-1])
            segments.append(HistorySegment(current_currency, tuple(times), tuple(amounts)))
        times, amounts, current_currency = [], [], None

    for timestamp, price in states:
        if price is None:
            finish(timestamp)
            continue
        amount, currency = price
        if current_currency is not None and currency != current_currency:
            finish(timestamp)
        if current_currency is None:
            current_currency = currency
        times.append(timestamp)
        amounts.append(amount)
    finish(end)
    return HistorySeries(start, end, tuple(segments))


def _font_properties(font_families: Sequence[str] | str | None, font_path: str | Path | None):
    from matplotlib import font_manager

    if font_path:
        path = Path(font_path).expanduser()
        if not path.is_file():
            raise RenderingError(f"Configured chart font does not exist: {path}")
        return font_manager.FontProperties(fname=str(path))
    if isinstance(font_families, str):
        requested = [part.strip() for part in font_families.split(",") if part.strip()]
    else:
        requested = list(font_families or ())
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for family in (*requested, *_CJK_FONTS, "DejaVu Sans"):
        if family in installed:
            return font_manager.FontProperties(family=family)
    return font_manager.FontProperties()


def _font_covers(text: str, font: Any) -> bool:
    from matplotlib import font_manager, ft2font

    try:
        charmap = ft2font.FT2Font(font_manager.findfont(font)).get_charmap()
    except (RuntimeError, OSError, ValueError):
        return False
    return all(ord(character) in charmap for character in text if not character.isspace())


def _chart_title(title: str) -> str:
    # Bound display width as well as character count for CJK and long titles.
    units, characters = 0, []
    for character in " ".join(title.split()):
        units += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        if units > 82:
            return "".join(characters).rstrip() + "..."
        characters.append(character)
    return "".join(characters)


def render_history_chart(
    records: Iterable[Mapping[str, Any]],
    output_path: str | Path,
    *,
    title: str,
    country: str = "CN",
    now: datetime | None = None,
    days: int = 365,
    font_families: Sequence[str] | str | None = None,
    font_path: str | Path | None = None,
    background_path: str | Path | None = None,
    attribution: str = "",
) -> Path:
    """Save a PNG chart and return its path; no network or global backend use.

    Different currencies get separate labelled panels, never a shared numeric
    curve. Without a font covering the supplied title, emit a RuntimeWarning
    and use a readable English chart title; the text response keeps the title.
    """
    series = build_history_series(records, now=now, days=days)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Matplotlib font caches are global, so serialize rendering in processes
    # where a bot may start several charts on background threads concurrently.
    with _PLOT_LOCK:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.dates import DateFormatter, MonthLocator
        from matplotlib.figure import Figure

        font = _font_properties(font_families, font_path)
        display_title = str(title)
        fallback = not _font_covers(display_title, font)
        if fallback:
            warnings.warn(
                "No configured chart font covers the game title. Configure a CJK font "
                "(for example Noto Sans CJK SC); using an English chart title.",
                RuntimeWarning,
                stacklevel=2,
            )
            display_title = "Steam price history"
        currencies = series.currencies or ("Price",)
        figure = Figure(figsize=(10.8, 4.2 + 1.8 * len(currencies)), dpi=100, facecolor="#111827")
        FigureCanvasAgg(figure)
        if background_path:
            # The service supplies a bounded, locally downloaded official cover.
            # No remote URL is fetched or interpreted by this rendering module.
            try:
                from PIL import Image, ImageOps
                with Image.open(Path(background_path)) as cover:
                    cover = ImageOps.fit(cover.convert("RGB"), (1080, int(figure.get_figheight() * 100)))
                    background = figure.add_axes([0, 0, 1, 1], zorder=-10)
                    background.imshow(cover, aspect="auto", alpha=0.35)
                    background.set_axis_off()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                warnings.warn(f"Steam cover could not be read; rendering without background: {exc}", RuntimeWarning, stacklevel=2)
        axes = figure.subplots(nrows=len(currencies), ncols=1, squeeze=False).ravel()
        for axis, currency in zip(axes, currencies):
            axis.set_facecolor((0.09, 0.125, 0.20, 0.70))
            axis.tick_params(colors="#cbd5e1", labelsize=9)
            for spine in axis.spines.values():
                spine.set_color("#475569")
            relevant = [segment for segment in series.segments if segment.currency == currency]
            for segment in relevant:
                axis.step(segment.timestamps, segment.amounts, where="post", color="#5eead4", linewidth=2)
                # A single observation at the window end would be invisible
                # without a marker; it is still not evidence for earlier days.
                if len(segment.timestamps) == 1:
                    axis.plot(segment.timestamps, segment.amounts, "o", color="#5eead4", markersize=4)
            axis.set_xlim(series.start, series.end)
            axis.set_ylim(bottom=0)
            axis.set_ylabel(currency, color="#e2e8f0", fontproperties=font)
            axis.grid(True, color="#334155", alpha=0.55, linewidth=0.6)
            # Month ticks are just labels: every within-month sale and return
            # to full price is still drawn on its actual timestamp.
            locator = MonthLocator(interval=max(1, days // 365), tz=UTC)
            axis.xaxis.set_major_locator(locator)
            axis.xaxis.set_major_formatter(DateFormatter("%Y-%m", tz=UTC))
            axis.tick_params(axis="x", labelsize=8)
            if relevant:
                low_time, low_amount = min(
                    ((timestamp, amount) for segment in relevant for timestamp, amount in zip(segment.timestamps, segment.amounts)),
                    key=lambda point: point[1],
                )
                near_right = (low_time - series.start).total_seconds() > (series.end - series.start).total_seconds() * 0.70
                high_on_axis = low_amount > axis.get_ylim()[1] * 0.7
                axis.annotate(
                    f"Window low: {low_amount:g}", xy=(low_time, low_amount),
                    xytext=(-8 if near_right else 8, -23 if high_on_axis else 14),
                    textcoords="offset points", ha="right" if near_right else "left",
                    fontsize=8, color="#f8fafc", fontproperties=font,
                    bbox={"boxstyle": "round,pad=0.3", "fc": "#111827", "ec": "#475569", "alpha": 0.90},
                    arrowprops={"arrowstyle": "-", "color": "#cbd5e1", "lw": 0.6},
                )
            if not relevant:
                axis.text(0.5, 0.5, "No price history available", ha="center", va="center", transform=axis.transAxes, color="#e2e8f0", fontproperties=font)
        figure.suptitle(_chart_title(display_title), fontsize=16, fontproperties=font, color="#f8fafc", y=0.965)
        figure.text(0.075, 0.905, f"Steam · {country.upper()} · Last {days} days · UTC", fontsize=10, color="#94a3b8", fontproperties=font)
        note = "Monthly labels; all sale dates retained. Gaps = unknown. No currency conversion."
        if fallback:
            note += " CJK title omitted: configure a CJK font."
        figure.text(0.075, 0.064, note, fontsize=8, color="#94a3b8", fontproperties=font)
        if attribution:
            figure.text(0.075, 0.032, attribution, fontsize=8, color="#94a3b8", fontproperties=font)
        figure.subplots_adjust(left=0.075, right=0.975, top=0.86, bottom=0.15, hspace=0.38)
        figure.savefig(path, format="png", facecolor=figure.get_facecolor())
        figure.clear()
    return path


def format_money(value: Any) -> str:
    price = _price(value)
    if price is None:
        return "暂无数据"
    amount, currency = price
    # Keep three-decimal currencies precise and avoid binary-float artifacts.
    places = 3 if currency in {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"} else 2
    try:
        number = Decimal(str(amount)).quantize(Decimal(1).scaleb(-places))
    except InvalidOperation:
        return "暂无数据"
    rendered = f"{currency} {number:,.{places}f}"
    return f"免费（{rendered}）" if amount == 0 else rendered


def _compact_money(value: Any) -> str:
    text = format_money(value)
    return text.replace("CNY ", "¥")


def _clean(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _time_text(value: Any) -> str:
    parsed = _datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed else "时间未知"


def _deal(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    if isinstance(value.get("deal"), Mapping):
        return value["deal"]
    return value


def _discount(deal: Any) -> str:
    deal = _deal(deal)
    cut = _number(deal.get("cut", deal.get("discount")))
    if cut is None:
        current, regular = _price(deal), _price(deal.get("regular"))
        if current and regular and current[1] == regular[1] and regular[0] > 0 and current[0] <= regular[0]:
            cut = 100 * (1 - current[0] / regular[0])
    if cut is None or cut > 100:
        return ""
    return "（无折扣）" if cut == 0 else f"（-{cut:g}%）"


def format_price_summary(summary: Mapping[str, Any]) -> str:
    """Format a Steam-only report, without inventing zero prices or discounts.

    Expected keys: title, country, edition, current, lowest, rating, store_url, versions,
    fetched_at, stale, warnings. current/lowest are price-bearing mappings;
    lowest may additionally contain timestamp. Version rows accept current or
    price, lowest, is_upgrade and note. Missing fields remain visibly unknown.
    """
    title = _clean(summary.get("title") or "Steam 游戏")
    country = _clean(summary.get("country") or "CN")
    edition = _clean(summary.get("edition") or "standard")
    label = EDITION_LABELS.get(edition, edition)
    current = summary.get("current", summary.get("current_price"))
    lowest = summary.get("lowest", summary.get("historical_low", summary.get("history_low")))
    if current is not None and isinstance(current, Mapping) and "amount" in current:
        current = {"price": current, "cut": summary.get("discount"), "regular": summary.get("regular_price")}
    # Preserve the original plugin's compact five-line response block.
    # The lowest discount belongs to that historical deal, never today's MSRP.
    lines = [title if edition == "standard" else f"{title} · {label}"]
    region = "国区价格" if country.upper() == "CN" else f"{country.upper()}区价格"
    current_line = f"{region}: {_compact_money(current)}"
    current_discount = _discount(current).removeprefix("（").removesuffix("）") if _price(current) else ""
    if current_discount and current_discount != "无折扣":
        current_line += " " + current_discount
    lines.append(current_line)
    historical_discount = _discount(lowest).removeprefix("（").removesuffix("）") if _price(lowest) else ""
    low_line = f"史低: {_compact_money(lowest)}"
    if historical_discount:
        low_line += " " + historical_discount
    lines.append(low_line)
    rating = _number(summary.get("rating"))
    rating_text = f"{rating:g}%" if rating is not None and rating <= 100 else "暂无数据"
    lines.append(f"好评率:{rating_text}")
    lines.append(f"steam商店链接: {_clean(summary.get('store_url')) or '暂无数据'}")

    versions = summary.get("versions")
    if isinstance(versions, Sequence) and not isinstance(versions, (str, bytes)) and versions:
        lines.append("版本对比（不混入本体史低）：")
        for version in versions:
            if not isinstance(version, Mapping):
                continue
            version_edition = _clean(version.get("edition"))
            name = _clean(version.get("title") or version.get("name") or EDITION_LABELS.get(version_edition, version_edition) or "未命名版本")
            version_current = version.get("current", version)
            line = f"- {name}：{format_money(version_current)}"
            if _price(version_current):
                line += _discount(version_current)
            if "lowest" in version or "historical_low" in version:
                line += f"；史低 {format_money(version.get('lowest', version.get('historical_low')))}"
            if version.get("is_upgrade") or version.get("upgrade"):
                line += "；升级包，通常需另有本体"
            if version.get("note"):
                line += f"；{_clean(version['note'])}"
            lines.append(line)

    stamp = summary.get("fetched_at") or summary.get("updated_at")
    if summary.get("stale"):
        lines.append(f"⚠ 当前为过期缓存，不代表实时价格。缓存时间：{_time_text(stamp)}")
    elif stamp:
        lines.append(f"数据获取时间：{_time_text(stamp)}")
    history_stamp = summary.get("history_fetched_at")
    if history_stamp and history_stamp != stamp:
        lines.append(f"历史记录获取时间：{_time_text(history_stamp)}")
    if isinstance(lowest, Mapping) and _price(lowest):
        low_stamp = lowest.get("timestamp") or lowest.get("date")
        if low_stamp:
            lines.append(f"史低记录时间：{_time_text(low_stamp)}")
    extra_warnings = summary.get("warnings") or []
    if isinstance(extra_warnings, str):
        extra_warnings = [extra_warnings]
    for warning in extra_warnings:
        if warning:
            lines.append(f"提示：{_clean(warning, 500)}")
    return "\n".join(lines)


def candidate_kind(candidate: Mapping[str, Any]) -> str:
    """Classify only from metadata, never from a word in a game's name."""
    kind = str(candidate.get("type") or "").casefold()
    ids = candidate.get("shop_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    ids = list(ids) + [candidate.get("shop_id") or ""]
    bundles = any(str(ident).startswith("bundle/") for ident in ids)
    apps = any(str(ident).startswith("app/") for ident in ids)
    if kind == "bundle" or (bundles and not apps):
        return "bundle"
    if kind in {"game", "dlc"} or apps or candidate.get("appid") or candidate.get("steam_appid"):
        return "game"
    return "unknown"


def format_candidates(candidates: Sequence[Mapping[str, Any]], *, query: str = "", limit: int = 8) -> str:
    """Separate verified bundles from similar games; no membership claim."""
    groups = {"game": [], "bundle": [], "unknown": []}
    seen_ids, seen_titles = set(), set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        ident = candidate.get("id") or candidate.get("appid") or candidate.get("steam_appid")
        title_key = _clean(candidate.get("title") or candidate.get("name")).casefold()
        kind = candidate_kind(candidate)
        # Equal names across different product types are not duplicates.
        title_identity = (kind, title_key)
        if (ident is not None and str(ident) in seen_ids) or (title_key and title_identity in seen_titles):
            continue
        groups[kind].append(candidate)
        if ident is not None:
            seen_ids.add(str(ident))
        if title_key:
            seen_titles.add(title_identity)
    sections = []
    labels = {"game": "还有其他类似名字的游戏：", "bundle": "名称相关的捆绑包（不计入本体史低）：", "unknown": "类型尚未确认的相关条目："}
    for kind, items in groups.items():
        if not items:
            continue
        lines = [labels[kind]]
        for index, candidate in enumerate(items[:max(1, limit)], start=1):
            title = _clean(candidate.get("title") or candidate.get("name") or "未命名条目")
            appid = candidate.get("appid") or candidate.get("steam_appid")
            suffix = f" · AppID {appid}" if kind == "game" and str(appid).isdecimal() else ""
            lines.append(f"{index}. {title}{suffix}")
        if len(items) > max(1, limit):
            lines.append("还有其他结果，请缩小游戏名范围。")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
