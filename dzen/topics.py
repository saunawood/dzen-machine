"""Сбор тем (Wordstat + идеи + конкуренты) и скоринг 0–100."""
import csv
import io
import re

from .core import CFG, INPUTS, ask, insights_block, kb_block, load_json, now, prompt, read_lines, save_json

TOPICS_FILE = "topics.json"


def _read_text(path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def wordstat_phrases(limit: int = 300):
    """Читает выгрузки Wordstat (CSV/TSV любого вида): берёт фразу и самое большое число в строке."""
    rows = {}
    for path in sorted((INPUTS / "wordstat").glob("*.*")):
        if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
            continue
        text = _read_text(path)
        delim = "\t" if text.count("\t") > text.count(";") else (";" if ";" in text else ",")
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            cells = [c.strip() for c in row if c.strip()]
            if not cells:
                continue
            phrase = cells[0]
            nums = [int(re.sub(r"\D", "", c)) for c in cells[1:] if re.sub(r"[\s\u00a0]", "", c).isdigit()]
            if not nums or not re.search(r"[а-яa-z]", phrase.lower()):
                continue
            rows[phrase.lower()] = max(rows.get(phrase.lower(), 0), max(nums))
    return sorted(rows.items(), key=lambda kv: -kv[1])[:limit]


def total(scores: dict) -> int:
    w = CFG["topics"]["weights"]
    # модель ставит 0–10 по каждому критерию, приводим к весам
    return round(sum(w[k] * max(0, min(10, float(scores.get(k, 0)))) / 10 for k in w))


def _norm(title: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", title.lower()).strip()


def cmd_score(_args=None):
    phrases = wordstat_phrases()
    ideas = read_lines(INPUTS / "ideas.txt")
    competitors = read_lines(INPUTS / "competitors.txt")
    if not (phrases or ideas):
        print("Нет входных данных: положите выгрузку Wordstat в inputs/wordstat/ или идеи в inputs/ideas.txt")
        return

    db = load_json(TOPICS_FILE, [])
    known = [t["title"] for t in db]
    wordstat_text = "\n".join(f"{p} — {n}" for p, n in phrases) or "(выгрузки Wordstat нет)"

    result = ask(prompt("score",
                        wordstat=wordstat_text,
                        ideas="\n".join(ideas) or "(нет)",
                        competitors="\n".join(competitors) or "(нет данных)",
                        known="\n".join(known[-150:]) or "(нет)",
                        kb=kb_block(), insights=insights_block(),
                        weights=CFG["topics"]["weights"]))

    seen = {_norm(t) for t in known}
    added = 0
    for t in result.get("topics", []):
        if not t.get("title") or _norm(t["title"]) in seen:
            continue
        seen.add(_norm(t["title"]))
        t["score"] = total(t.get("scores", {}))
        t["status"] = "new"
        t["added"] = now().date().isoformat()
        db.append(t)
        added += 1
    save_json(TOPICS_FILE, db)
    print(f"✓ Новых тем: {added}. Всего в базе: {len(db)}")
    cmd_topics()


def cmd_topics(_args=None):
    db = load_json(TOPICS_FILE, [])
    fresh = sorted((t for t in db if t["status"] == "new"), key=lambda t: -t["score"])
    limit = CFG["topics"]["min_score"]
    print(f"\nОчередь тем (проходной балл {limit}):")
    for t in fresh[:20]:
        mark = "✓" if t["score"] >= limit else "·"
        print(f"  {mark} {t['score']:>3}  {t['title']}")
    if not fresh:
        print("  пусто — запустите score")


def next_topics(n: int):
    db = load_json(TOPICS_FILE, [])
    ready = sorted((t for t in db if t["status"] == "new" and t["score"] >= CFG["topics"]["min_score"]),
                   key=lambda t: -t["score"])
    return ready[:n]


def mark_topic(title: str, status: str, slug: str = ""):
    db = load_json(TOPICS_FILE, [])
    for t in db:
        if t["title"] == title:
            t["status"] = status
            if slug:
                t["slug"] = slug
    save_json(TOPICS_FILE, db)
