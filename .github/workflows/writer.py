"""Производство статьи: бриф → текст → проверки и критик (с доработками) → Дзен-редактор."""
import re

from .core import (CFG, DRAFTS, KB, ask, insights_block, kb_block, now, prompt, read_lines,
                   read_post, slugify, unique_slug, write_post)
from .photos import candidates, load_catalog
from .render import PHOTO_RE, photo_ids
from .topics import mark_topic, next_topics


# ───────── механические проверки (без модели) ─────────

def length_limits():
    """Из строки вида «6000–9000 знаков» достаёт (6000, 9000)."""
    raw = str(CFG["generation"].get("length", "")).replace(" ", "").replace("\u00a0", "")
    nums = [int(n) for n in re.findall(r"\d+", raw)]
    return (nums[0], nums[-1]) if nums else (None, None)


def text_length(body: str) -> int:
    """Число знаков с пробелами, без меток фото."""
    text = re.sub(PHOTO_RE, "", body)
    return len(re.sub(r"\s+", " ", text).strip())


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
    # Точный подсчёт объёма: модели плохо считают знаки «на глаз»
    lo, hi = length_limits()
    if hi:
        n = text_length(body)
        if n > hi * 1.1:
            issues.append({"severity": "major", "type": "объём", "quote": "",
                           "fix": f"сейчас {n} знаков, нужно {lo}–{hi}. Сократить примерно на {n - hi} знаков: "
                                  f"убрать повторы и общие слова, сжать второстепенные разделы, смысл сохранить"})
        elif lo and n < lo * 0.8:
            issues.append({"severity": "minor", "type": "объём", "quote": "",
                           "fix": f"сейчас {n} знаков, нужно {lo}–{hi}. Добавить конкретики: примеры, цифры, решения"})
    return issues


# ───────── обложка: не повторять недавние ─────────

def recent_covers(exclude_slug: str = "", limit: int = 10):
    """Обложки последних статей (черновики, расписание, опубликованные)."""
    items = []
    for path in DRAFTS.parent.rglob("*.md"):
        if path.stem == exclude_slug:
            continue
        try:
            meta = read_post(path)[0]
        except Exception:
            continue
        if meta.get("cover"):
            items.append((str(meta.get("created", "")), meta["cover"]))
    items.sort(reverse=True)
    return [c for _, c in items[:limit]]


def choose_cover(used, exclude_slug: str = "") -> str:
    """Первое фото статьи, которое не было обложкой в последних 10 статьях.
    Если все фото статьи уже были обложками — берём самое давнее из них."""
    if not used:
        return ""
    recent = recent_covers(exclude_slug)
    for pid in used:
        if pid not in recent:
            return pid
    return max(used, key=lambda pid: recent.index(pid) if pid in recent else -1)


def fresh_first_photo(body: str, exclude_slug: str = "") -> str:
    """Чтобы статьи не начинались с одного и того же снимка: если первое фото
    уже было обложкой недавно, меняем его местами с более «свежим» фото этой же
    статьи (метка переезжает вместе с подписью)."""
    used = photo_ids(body)
    if len(used) < 2:
        return body
    target = choose_cover(used, exclude_slug)
    if target == used[0]:
        return body
    tags = list(re.finditer(PHOTO_RE, body))
    if len(tags) != len(used):
        return body                      # неожиданная разметка — ничего не трогаем
    a, b = tags[0], tags[used.index(target)]
    return (body[:a.start()] + b.group(0) + body[a.end():b.start()]
            + a.group(0) + body[b.end():])

# ───────── этапы ─────────

def blocking(review: dict):
    return [i for i in review["issues"] if i.get("severity") in ("critical", "major")]


def badness(review: dict) -> int:
    """Чем больше, тем хуже: критическое замечание весит как 10 серьёзных."""
    return sum(10 if i.get("severity") == "critical" else 1 for i in blocking(review))


def critique(article: dict, brief: dict) -> dict:
    found = local_checks(article["body"])
    review = ask(prompt("critic", article=article, brief=brief, kb=kb_block(), local=found), role="critic")
    review.setdefault("issues", [])
    review["issues"] = found + review["issues"]
    review["verdict"] = "revise" if blocking(review) else "pass"
    return review


def revise(article: dict, issues, brief: dict, photos, notes: str = "") -> dict:
    out = ask(prompt("revise", article=article, issues=issues, brief=brief, kb=kb_block(),
                     photos=photos, notes=notes or "(нет)", length=CFG["generation"]["length"]))
    return {"title": out.get("title", article["title"]), "body": out["body"]}


def improve_loop(article, brief, photos, log, notes=""):
    """Критик → доработка по кругу. Всегда возвращает лучшую из версий, а не последнюю.
    Ваши комментарии (notes) передаются в каждый круг, чтобы критик их не «откатил»."""
    review = critique(article, brief)
    best_article, best_review = article, review
    rounds = 0
    while best_review["verdict"] == "revise" and rounds < CFG["generation"]["max_revisions"]:
        rounds += 1
        log(f"  ↻ доработка {rounds}: серьёзных замечаний {len(blocking(best_review))}")
        article = revise(best_article, best_review["issues"], brief, photos, notes)
        review = critique(article, brief)
        if badness(review) <= badness(best_review):
            best_article, best_review = article, review
        else:
            log(f"    ↩ версия хуже предыдущей ({len(blocking(review))} серьёзных) — продолжаю от лучшей")
    return best_article, best_review, rounds


def summarize(review, rounds) -> dict:
    sev = lambda s: sum(1 for i in review["issues"] if i.get("severity") == s)
    return {"verdict": review["verdict"], "rounds": rounds, "critical": sev("critical"),
            "major": sev("major"), "minor": sev("minor"), "summary": review.get("summary", ""),
            "open_issues": [f"[{i.get('severity')}] {i.get('type')}: {i.get('fix')}"
                            for i in blocking(review)][:8]}


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
    log(f"  ✓ текст ({text_length(article['body'])} знаков)")
    article, review, rounds = improve_loop(article, brief, photos, log)
    log(f"  ✓ проверка: {review['verdict']}, кругов доработки {rounds}, итог {text_length(article['body'])} знаков")
    edit = ask(prompt("editor", article=article, brief=brief))
    log("  ✓ заголовки и лид")

    titles = [t for t in edit.get("titles", []) if t][:7] or [article["title"]]
    best = min(max(int(edit.get("best", 0)), 0), len(titles) - 1)
    article["body"] = fresh_first_photo(article["body"])
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
    article, review, rounds = improve_loop(article, brief, photos, log, notes)
    meta["check"] = summarize(review, rounds)
    meta["notes_done"] = meta.get("notes_done", []) + meta.pop("notes", [])
    meta["status"] = "new"
    meta.pop("tg", None)
    article["body"] = fresh_first_photo(article["body"], exclude_slug=path.stem)
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
