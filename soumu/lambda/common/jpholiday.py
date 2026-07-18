"""日本の祝日判定（依存ゼロ・近似）。

国民の祝日（固定日・ハッピーマンデー・春分/秋分）＋振替休日＋国民の休日。
※2020-2021 の五輪特例など一部の臨時移動は未対応（通常運用では影響軽微）。
"""
from datetime import date, timedelta

_cache = {}


def _nth_monday(year, month, n):
    d = date(year, month, 1)
    offset = (0 - d.weekday()) % 7          # 0=Monday
    return date(year, month, 1 + offset + (n - 1) * 7)


def _vernal_equinox(year):
    return int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))


def _autumnal_equinox(year):
    return int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))


def _base_holidays(year):
    h = {
        date(year, 1, 1): "元日",
        date(year, 2, 11): "建国記念の日",
        date(year, 4, 29): "昭和の日",
        date(year, 5, 3): "憲法記念日",
        date(year, 5, 4): "みどりの日",
        date(year, 5, 5): "こどもの日",
        date(year, 11, 3): "文化の日",
        date(year, 11, 23): "勤労感謝の日",
        _nth_monday(year, 1, 2): "成人の日",
        _nth_monday(year, 7, 3): "海の日",
        _nth_monday(year, 9, 3): "敬老の日",
        _nth_monday(year, 10, 2): "スポーツの日",
        date(year, 3, _vernal_equinox(year)): "春分の日",
        date(year, 9, _autumnal_equinox(year)): "秋分の日",
    }
    if year >= 2016:
        h[date(year, 8, 11)] = "山の日"
    if year >= 2020:
        h[date(year, 2, 23)] = "天皇誕生日"
    else:
        h[date(year, 12, 23)] = "天皇誕生日"
    return h


def _holidays(year):
    if year in _cache:
        return _cache[year]
    h = _base_holidays(year)
    # 振替休日：祝日が日曜 → 次の非祝日を休日に
    for d in sorted([d for d in h if d.weekday() == 6]):
        nxt = d + timedelta(days=1)
        while nxt in h:
            nxt += timedelta(days=1)
        h[nxt] = "振替休日"
    # 国民の休日：祝日に挟まれた平日（敬老の日と秋分の間など）
    for d in sorted(list(h)):
        d2 = d + timedelta(days=2)
        mid = d + timedelta(days=1)
        if d2 in h and mid not in h and mid.weekday() != 6:
            h[mid] = "国民の休日"
    _cache[year] = h
    return h


def holiday_name(d):
    """祝日なら名称、そうでなければ None。"""
    return _holidays(d.year).get(d)


def is_holiday(d):
    return d in _holidays(d.year)


def is_rest_day(d):
    """土日 or 祝日。"""
    return d.weekday() >= 5 or is_holiday(d)
