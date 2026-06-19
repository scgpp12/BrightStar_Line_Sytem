#!/usr/bin/env python3
"""Markdown → スタイル付きHTML → Chrome 無頭印刷で PDF 生成.

使い方:
    python md2pdf.py docs/通勤コスト_利用マニュアル.md [...複数可]

Windows の Chrome は --headless=new + --no-sandbox + 独立 user-data-dir が必須。
親プロセスが即返り PDF は非同期に書き出されるため、ファイル落地をポーリングで待つ。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import markdown

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans JP","Yu Gothic","Meiryo",sans-serif; color:#1B2733; line-height:1.7; font-size:10.5pt; }
h1 { color:#0E2238; border-bottom:3px solid #1390A6; padding-bottom:8px; font-size:20pt; }
h2 { color:#0B5A82; border-left:5px solid #E0A43B; padding-left:10px; margin-top:1.4em; font-size:14pt; }
h3 { color:#16314e; font-size:11.5pt; }
table { border-collapse:collapse; width:100%; margin:12px 0; font-size:9.5pt; }
th,td { border:1px solid #cdd6df; padding:6px 9px; text-align:left; vertical-align:top; }
th { background:#0E2238; color:#fff; }
tr:nth-child(even) td { background:#F4F7FB; }
code { background:#eef2f6; padding:1px 5px; border-radius:4px; font-size:9pt; }
pre { background:#0E2238; color:#e6edf3; padding:12px 14px; border-radius:8px; overflow:auto; font-size:8.8pt; line-height:1.5; }
pre code { background:transparent; color:inherit; padding:0; }
"""

_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>{css}</style></head><body>{body}</body></html>"""


def convert(md_path: Path) -> Path:
    md_path = md_path.resolve()
    pdf_path = md_path.with_suffix(".pdf")
    body = markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    html = _HTML.format(css=_CSS, body=body)
    html_path = md_path.with_suffix(".tmp.html")
    html_path.write_text(html, encoding="utf-8")

    if pdf_path.exists():
        pdf_path.unlink()
    user_dir = tempfile.mkdtemp(prefix="chrome_pdf_")
    subprocess.run([
        CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
        f"--user-data-dir={user_dir}",
        "--virtual-time-budget=15000",
        f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer",
        html_path.as_uri(),
    ], check=False)

    # PDF 落地を最大40秒ポーリング（サイズが安定したら完了）
    last = -1
    for _ in range(80):
        if pdf_path.exists():
            sz = pdf_path.stat().st_size
            if sz > 0 and sz == last:
                break
            last = sz
        time.sleep(0.5)
    html_path.unlink(missing_ok=True)
    return pdf_path


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        out = convert(p)
        ok = out.exists() and out.stat().st_size > 0
        print(f"{'OK ' if ok else 'NG '} {out.name}  ({out.stat().st_size if out.exists() else 0} bytes)")
