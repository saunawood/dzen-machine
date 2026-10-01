"""Производство статьи: бриф → текст → проверки и критик (с доработками) → Дзен-редактор."""
import re

from .core import (CFG, DRAFTS, KB, ask, insights_block, kb_block, now, prompt, read_lines,
                   read_post, slugify, unique_slug, write_post)
from .photos import candidates, load_catalog
from .render import PHOTO_RE, photo_ids
from .topics import mark_topic, next_topics


# ───────── механические проверки (без модели) ─────────

def local_checks(body: str):
    issues, low = [], body.lower()
    for phrase in read_lines(KB / "stopwords.txt"):
        if phrase.lower() in low:
            issues.append({"severity": "minor", "type": "штамп", "quote": phrase,
                           "fix": "убрать или переформулировать по-человечески"})
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip() and not p.lstrip().startswith(("#", "-", "*", "[[", "1."))]
    long = [p for p in paras if len(p) > 700]
    if long:
        issues.append({"severity": "minor", "type": "читаемость", "quote": long[0][:80] + "…",
                       "fix": f"разбить длинные абзацы ({len(long)} шт.) — в Дзене читают с телефона"})
    if paras:
        lens = [len(p) for p in paras]
        avg = sum(lens) / len(lens)
        spread = sum(abs(l - avg) for l in lens) / len(lens) / max(avg, 1)
        if len(paras) >= 6 and spread < 0.2:
            issues.append({"severity": "minor", "type": "ритм", "quote": "",
                           "fix": "абзацы почти одинаковой длины — машинный ритм, разнообразить"})
    known = set(load_catalog())
    for pid in photo_ids(body):
        if pid not in known:
            issues.append({"severity": "major", "type": "фото", "quote": f"[[photo:{pid}]]",
                           "fix": "такого фото нет в каталоге — заменить на фото из списка или убрать"})
    return issues


# ───────── этапы ─────────

def critique(article: dict, brief: dict) -> dict:
    found = local_checks(article["body"])
    review = ask(prompt("critic", article=article, brief=brief, kb=kb_block(), local=found), role="critic")
    review.setdefault("issues", [])
    review["issues"] = found + review["issues"]
    blocking = [i for i in review["issues"] if i.get("severity") in ("critical", "major")]
    review["verdict"] = "revise" if blocking else "pass"
    return review


def revise(article: dict, issues, brief: dict, photos, notes: str = "") -> dict:
    out = ask(prompt("revise", article=article, issues=issues, brief=brief, kb=kb_block(),
                     photos=photos, notes=notes or "(нет)", length=CFG["generation"]["length"]))
    return {"title": out.get("title", article["title"]), "body": out["body"]}


def improve_loop(article, brief, photos, log, notes=""):
    review = critique(article, brief)
    rounds = 0
    while review["verdict"] == "revise" and rounds < CFG["generation"]["max_revisions"]:
        rounds += 1
        n = sum(1 for i in review["issues"] if i.get("severity") in ("critical", "major"))
        log(f"  ↻ доработка {rounds}: серьёзных замечаний {n}")
        article = revise(article, review["issues"], brief, photos, notes)
        notes = ""
        review = critique(article, brief)
    return article, review, rounds


def summarize(review, rounds) -> dict:
    sev = lambda s: sum(1 for i in review["issues"] if i.get("severity") == s)
    return {"verdict": review["verdict"], "rounds": rounds, "critical": sev("critical"),
            "major": sev("major"), "minor": sev("minor"), "summary": review.get("summary", ""),
            "open_issues": [f"[{i.get('severity')}] {i.get('type')}: {i.get('fix')}"
                            for i in review["issues"] if i.get("severity") in ("critical", "major")][:8]}


def produce(topic: dict, log=print):
    log(f"→ {topic['title']}  (балл {topic.get('score', '—')})")
    brief = ask(prompt("brief", topic=topic, kb=kb_block(), insights=insights_block(),
                       competitors="\n".join(read_lines(KB.parent / "inputs" / "competitors.txt")) or "(нет)",
                       length=CFG["generation"]["length"]))
    log("  ✓ бриф")
    photos = candidates(brief.get("photo_tags", []) + [topic.get("cluster", "")])
    draft = ask(prompt("write", brief=brief, kb=kb_block(), photos=photos or "(фото нет — не вставляй метки)",
                       length=CFG["generation"]["length"]))
    article = {"title": draft["title"], "body": draft["body"]}
    log("  ✓ текст")
    article, review, rounds = improve_loop(article, brief, photos, log)
    log(f"  ✓ проверка: {review['verdict']}, кругов доработки {rounds}")
    edit = ask(prompt("editor", article=article, brief=brief))
    log("  ✓ заголовки и лид")

    titles = [t for t in edit.get("titles", []) if t][:7] or [article["title"]]
    best = min(max(int(edit.get("best", 0)), 0), len(titles) - 1)
    used = photo_ids(article["body"])
    slug = unique_slug(slugify(titles[best]))
    meta = {
        "title": titles[best],
        "title_variants": titles,
        "description": edit.get("lead", "").strip(),
        "topic": topic["title"],
        "cluster": topic.get("cluster", ""),
        "format": brief.get("format", topic.get("format", "")),
        "score": topic.get("score"),
        "cover": used[0] if used else "",
        "check": summarize(review, rounds),
        "status": "new",
        "created": now().isoformat(timespec="minutes"),
        "brief": brief,
    }
    write_post(DRAFTS / f"{slug}.md", meta, article["body"])
    mark_topic(topic["title"], "drafted", slug)
    log(f"  ✓ posts/drafts/{slug}.md")
    return slug


def rework(path, log=print):
    """Доработка черновика по вашим комментариям из Telegram."""
    meta, body = read_post(path)
    notes = "\n".join(meta.get("notes", []))
    log(f"↻ Доработка по комментариям: {path.stem}")
    brief = meta.get("brief", {})
    photos = candidates(brief.get("photo_tags", []) + [meta.get("cluster", "")])
    article = revise({"title": meta["title"], "body": body}, [], brief, photos, notes)
    article, review, rounds = improve_loop(article, brief, photos, log)
    meta["check"] = summarize(review, rounds)
    meta["notes_done"] = meta.get("notes_done", []) + meta.pop("notes", [])
    meta["status"] = "new"
    meta.pop("tg", None)
    used = photo_ids(article["body"])
    meta["cover"] = used[0] if used else ""
    write_post(path, meta, article["body"])
    log("  ✓ готово, уйдёт на повторное согласование")


def cmd_generate(args):
    for path in sorted(DRAFTS.glob("*.md")):
        meta = read_post(path)[0]
        if meta.get("status") == "revise" and meta.get("notes"):
            try:
                rework(path)
            except Exception as e:
                print(f"  ✗ {e}")
    if getattr(args, "rework_only", False):
        return
    count = getattr(args, "count", 0) or CFG["generation"]["per_run"]
    topics = next_topics(count)
    if not topics:
        print("Нет тем с проходным баллом — запустите score или добавьте идеи.")
        return
    for t in topics:
        try:
            produce(t)
        except Exception as e:
            print(f"  ✗ ошибка: {e} — тема останется в очереди")
