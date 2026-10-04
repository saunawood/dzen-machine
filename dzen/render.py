"""Markdown → HTML, совместимый с Дзеном. Фото вставляются меткой [[photo:ID|подпись]]."""
import html
import re
from urllib.parse import urlencode

import markdown

from .core import CFG
from .photos import data_uri, public_url

ALLOWED = {"p", "a", "b", "strong", "i", "em", "u", "s", "h2", "h3", "h4", "blockquote",
           "ul", "ol", "li", "figure", "img", "figcaption", "br"}
PHOTO_RE = re.compile(r"\[\[photo:([a-z0-9_-]+)(?:\|([^\]]*))?\]\]")


COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _cells(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def tables_to_lists(body: str) -> str:
    """Дзен не поддерживает таблицы, поэтому таблица Markdown превращается в блоки:
    первая колонка — жирный подзаголовок, остальные — список «Колонка: значение»."""
    lines = body.split("\n")
    out, i = [], 0
    while i < len(lines):
        if (i + 1 < len(lines) and "|" in lines[i] and TABLE_SEP_RE.match(lines[i + 1])):
            head = _cells(lines[i])
            i += 2
            blocks = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                row = _cells(lines[i])
                title = row[0] if row else ""
                items = [f"- **{h}:** {v}" for h, v in zip(head[1:], row[1:]) if v]
                blocks.append(f"**{title}**\n\n" + "\n".join(items))
                i += 1
            out.append("\n\n" + "\n\n".join(blocks) + "\n\n")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def photo_ids(body: str):
    return [m.group(1) for m in PHOTO_RE.finditer(body)]


def figure(src: str, caption: str = "") -> str:
    cap = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
    return f'<figure><img src="{src}"/>{cap}</figure>'


def cta_link(slug: str) -> str:
    url = CFG["channel"].get("cta_url")
    if not url:
        return ""
    sep = "&" if "?" in url else "?"
    return url + sep + urlencode({"utm_source": "dzen", "utm_medium": "article", "utm_campaign": slug})


def to_html(body: str, *, embed: bool = False) -> str:
    """embed=True — фото встраиваются в файл (для предпросмотра в Telegram)."""
    def photo(m):
        pid, caption = m.group(1), (m.group(2) or "").strip()
        src = data_uri(pid) if embed else public_url(pid)
        return "\n\n" + (figure(src, caption) if src else "") + "\n\n"

    body = COMMENT_RE.sub("", body)        # служебные пометки для редактора не публикуем
    body = tables_to_lists(body)
    body = PHOTO_RE.sub(photo, body)
    out = markdown.markdown(body, extensions=["extra", "sane_lists"])
    out = re.sub(r"<p>\s*(<figure>.*?</figure>)\s*</p>", r"\1", out, flags=re.S)
    out = re.sub(r'<p>\s*<img alt="([^"]*)" src="([^"]+)"\s*/?>\s*</p>',
                 lambda m: figure(m.group(2), m.group(1)), out)
    out = re.sub(r"<h1>(.*?)</h1>", r"<h2>\1</h2>", out)
    out = re.sub(r"</?([a-zA-Z0-9]+)[^>]*>",
                 lambda m: m.group(0) if m.group(1).lower() in ALLOWED else "", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><meta name="description" content="{desc}">{extra}
<style>body{{max-width:720px;margin:2rem auto;padding:0 1rem;font:18px/1.6 Georgia,serif;color:#222;background:#fff}}
img{{max-width:100%;height:auto;border-radius:6px}}figure{{margin:1.5rem 0}}figcaption{{color:#777;font-size:.85em}}
a{{color:#06c}}.meta{{color:#888;font-size:.9em}}.box{{background:#f5f3ef;padding:1rem;border-radius:8px;font:15px/1.5 sans-serif}}</style>
</head><body>{content}</body></html>"""


def page(title: str, desc: str, content: str, extra: str = "") -> str:
    return PAGE.format(title=html.escape(title), desc=html.escape(desc), content=content, extra=extra)
