"""最小限の xlsx セル読取（標準ライブラリのみ・依存ゼロ）。

xlsx は zip+XML。指定セルの値取得・行スキャン・年月推定・日付変換を行う。
- 文字列：sharedStrings 参照（ふりがな rPh は除外）/ inlineStr / str
- 数値：日付シリアル値（1900 日付システム）対応
"""
import io
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _si_text(si):
    """共有文字列 1 件の本文（ふりがな rPh / phoneticPr は除外）。"""
    parts = []
    for child in si:
        tag = child.tag
        if tag == _NS + "t":
            parts.append(child.text or "")
        elif tag == _NS + "r":                 # rich text run
            for t in child.findall(_NS + "t"):
                parts.append(t.text or "")
        # rPh（ふりがな）/ phoneticPr はスキップ
    return "".join(parts)


def _shared_strings(z):
    out = []
    try:
        ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return out
    for si in ss.findall(_NS + "si"):
        out.append(_si_text(si))
    return out


def _first_sheet_path(z):
    names = z.namelist()
    if "xl/worksheets/sheet1.xml" in names:
        return "xl/worksheets/sheet1.xml"
    ws = sorted(n for n in names
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    return ws[0] if ws else None


def cell_map(data):
    """{セル参照: 値文字列} を返す（空セルは含めない）。失敗時は空 dict。"""
    out = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        return out
    shared = _shared_strings(z)
    path = _first_sheet_path(z)
    if not path:
        return out
    root = ET.fromstring(z.read(path))
    for c in root.iter(_NS + "c"):
        ref = c.get("r")
        if not ref:
            continue
        t = c.get("t")
        v = c.find(_NS + "v")
        val = None
        if t == "s" and v is not None:
            try:
                val = shared[int(v.text)]
            except (ValueError, IndexError):
                val = None
        elif t == "inlineStr":
            is_el = c.find(_NS + "is")
            val = "".join(x.text or "" for x in is_el.iter(_NS + "t")) if is_el is not None else None
        elif v is not None:
            val = v.text
        if val not in (None, ""):
            out[ref] = val
    return out


def read_cell(data, cell_ref):
    return cell_map(data).get(cell_ref)


def col_of(ref):
    m = re.match(r"^([A-Z]+)\d+$", ref)
    return m.group(1) if m else None


def to_date(val):
    """セル値 → date。日付シリアル数値 or 'YYYY/M/D' 文字列に対応。無理なら None。"""
    if val is None:
        return None
    s = str(val).strip()
    try:
        f = float(s)
        if 20000 <= f <= 80000:
            return date(1899, 12, 30) + timedelta(days=int(f))
    except ValueError:
        pass
    m = re.search(r"(20\d{2})\D(\d{1,2})\D(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _ym_from_text(s):
    m = re.search(r"(20\d{2})\D{0,3}?(\d{1,2})", s)
    if m:
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return int(m.group(1)), mo
    return None


def cell_year_month(data, cell_ref):
    """セルから (year, month) を推定。取れなければ None。"""
    val = read_cell(data, cell_ref)
    if val is None:
        return None
    d = to_date(val)
    if d:
        return d.year, d.month
    return _ym_from_text(str(val).strip())


def is_number(val):
    if val is None:
        return False
    try:
        float(str(val).strip())
        return True
    except ValueError:
        return False
