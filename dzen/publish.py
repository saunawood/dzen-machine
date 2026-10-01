"""Модерация, расписание, сборка сайта и RSS-ленты для Дзена."""
import datetime as dt
import html
import shutil
from email.utils import format_datetime

from .core import (APPROVED, BASE, CFG, DRAFTS, REJECTED, ROOT, SITE, TZ, now, parse_dt, post_path,
                   read_post, write_post)
from .photos import export_for_site, public_url
from .render import cta_link, figure, page, photo_ids, to_html


# ───────── расписание ─────────

def next_slots(n: int):
    taken = {parse_dt(read_post(p)[0]["publish_at"]) for p in APPROVED.glob("*.md")}
    times = sorted(dt.time.fromisoformat(t) for t in CFG["schedule"]["times"])
    day, current, out = now().date(), now(), []
    while len(out) < n:
        for t in times:
            slot = dt.datetime.combine(day, t, TZ)
            if slot > current and slot not in taken and len(out) < n:
                out.append(slot)
        day += dt.timedelta(days=1)
    return out


def approve(slug: str, title_index=None) -> dt.datetime:
    src = post_path(slug, DRAFTS)
    meta, body = read_post(src)
    if title_index is not None and meta.get("title_variants"):
        meta["title"] = meta["title_variants"][title_index]
    slot = next_slots(1)[0]
    meta["publish_at"] = slot.isoformat(timespec="minutes")
    meta["status"] = "scheduled"
    write_post(APPROVED / src.name, meta, body)
    src.unlink()
    return slot


def reject(slug: str):
    src = post_path(slug, DRAFTS)
    shutil.move(src, REJECTED / src.name)


def cmd_approve(args):
    slugs = [p.stem for p in sorted(DRAFTS.glob("*.md"))] if args.all else args.slugs
    if not slugs:
        raise SystemExit("Укажите SLUG или --all")
    for s in slugs:
        print(f"  ✓ {s} → {approve(s):%d.%m %H:%M}")


def cmd_reject(args):
    for s in args.slugs:
        reject(s)
        print(f"  ✗ {s} отклонён")


def status_text() -> str:
    lines = ["Черновики:"]
    for p in sorted(DRAFTS.glob("*.md")):
        m = read_post(p)[0]
        lines.append(f"  [{m.get('status')}] {p.stem} — {m['title']}")
    lines.append("\nРасписание:")
    for when, p in sorted((parse_dt(read_post(p)[0]["publish_at"]), p) for p in APPROVED.glob("*.md")):
        mark = "✓" if when <= now() else "⏳"
        lines.append(f"  {mark} {when:%d.%m %H:%M}  {p.stem}")
    return "\n".join(lines)


def cmd_list(_args=None):
    print(status_text())


# ───────── сборка ─────────

def cdata(s: str) -> str:
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def image_type(url: str) -> str:
    ext = url.lower().rsplit(".", 1)[-1].split("?")[0]
    return {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")


def article_html(slug: str, meta: dict, body: str, *, embed=False) -> str:
    """Тело статьи: обложка + текст + ссылка на сайт компании."""
    content = to_html(body, embed=embed)
    if not meta.get("cover"):  # фото в тексте нет — ставим обложку по умолчанию
        content = figure(CFG["channel"]["default_cover"]) + "\n" + content
    link = cta_link(slug)
    if link:
        content += (f'\n<p>Проекты и фото объектов — на сайте '
                    f'<a href="{link}">{html.escape(CFG["channel"]["company"])}</a>.</p>')
    return content


def cover_url(meta) -> str:
    return public_url(meta["cover"]) if meta.get("cover") else CFG["channel"]["default_cover"]


def published():
    posts = []
    for p in APPROVED.glob("*.md"):
        meta, body = read_post(p)
        when = parse_dt(meta["publish_at"])
        if when <= now():
            posts.append((when, p.stem, meta, body))
    return sorted(posts, reverse=True)


def cmd_build(_args=None):
    ch, feed = CFG["channel"], CFG["feed"]
    posts = published()
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "posts").mkdir(parents=True)
    if (ROOT / "static").exists():
        shutil.copytree(ROOT / "static", SITE / "static", dirs_exist_ok=True)

    items, links = [], []
    for when, slug, meta, body in posts:
        export_for_site(photo_ids(body))
        url, cover = f"{BASE}/posts/{slug}.html", cover_url(meta)
        content = article_html(slug, meta, body)
        title, desc = meta["title"], meta.get("description", "")
        og = f'\n<meta property="og:title" content="{html.escape(title)}"><meta property="og:image" content="{cover}">'
        (SITE / "posts" / f"{slug}.html").write_text(page(title, desc, (
            f'<p class="meta"><a href="../index.html">{html.escape(ch["title"])}</a> · {when:%d.%m.%Y}</p>'
            f'<h1>{html.escape(title)}</h1>{content}'), og), encoding="utf-8")
        links.append(f'<li><a href="posts/{slug}.html">{html.escape(title)}</a> '
                     f'<span class="meta">{when:%d.%m.%Y}</span></li>')

        if len(items) < feed["size"]:
            cats = list(feed["categories"]) + (["native-draft"] if feed["publish_mode"] == "draft" else [])
            items.append("\n".join([
                "    <item>",
                f"      <title>{html.escape(title)}</title>",
                f"      <link>{url}</link>",
                f'      <guid isPermaLink="false">{slug}</guid>',
                f"      <pubDate>{format_datetime(when)}</pubDate>",
                '      <media:rating scheme="urn:simple">nonadult</media:rating>',
                *[f"      <category>{c}</category>" for c in cats],
                f'      <enclosure url="{html.escape(cover)}" type="{image_type(cover)}"/>',
                f"      <description>{cdata(desc)}</description>",
                f"      <content:encoded>{cdata(content)}</content:encoded>",
                "    </item>"]))

    rss = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:georss="http://www.georss.org/georss">',
        "  <channel>",
        f"    <title>{html.escape(ch['title'])}</title>",
        f"    <link>{BASE}/</link>",
        f"    <description>{html.escape(ch['description'])}</description>",
        f"    <language>{ch['language']}</language>",
        *items, "  </channel>", "</rss>", ""])
    (SITE / "zen.xml").write_text(rss, encoding="utf-8")
    (SITE / "index.html").write_text(page(ch["title"], ch["description"], (
        f"<h1>{html.escape(ch['title'])}</h1><p>{html.escape(ch['description'])}</p>"
        f"<ul>{''.join(links) or '<li>Скоро здесь появятся статьи</li>'}</ul>")), encoding="utf-8")
    print(f"✓ Сайт: {len(posts)} опубл., в ленте {len(items)} → site/zen.xml")
