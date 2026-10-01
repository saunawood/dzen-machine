"""Аналитика: напоминания о статистике и выводы для отбора тем."""
import csv

from .core import APPROVED, CFG, DATA, INPUTS, ask, now, parse_dt, prompt, read_post

STATS = INPUTS / "stats.csv"


def load_stats():
    if not STATS.exists():
        return []
    with STATS.open(encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("slug")]


def due():
    """(slug, день, дата публикации) — для каких статей пора внести цифры."""
    have = {(r["slug"], str(r["day"]).strip()) for r in load_stats()}
    out = []
    for p in APPROVED.glob("*.md"):
        when = parse_dt(read_post(p)[0]["publish_at"])
        age = (now() - when).days
        for d in CFG["analytics"]["checkpoints"]:
            if age >= d and (p.stem, str(d)) not in have and age < d + 14:
                out.append((p.stem, d, when))
    return sorted(out, key=lambda x: (x[2], x[1]))


def cmd_stats(_args=None):
    todo = due()
    if not todo:
        print("Всё внесено.")
        return
    print("Внесите в inputs/stats.csv (данные из Студии Дзена):")
    print("slug,day,views,ctr,read_time_sec,completion_pct,comments,subscriptions,clicks,leads")
    for slug, d, _ in todo:
        print(f"{slug},{d},,,,,,,,")


def cmd_learn(_args=None):
    stats = load_stats()
    posts = {}
    for p in APPROVED.glob("*.md"):
        m = read_post(p)[0]
        posts[p.stem] = {k: m.get(k) for k in ("title", "topic", "cluster", "format", "score", "publish_at")}
    rows = [{**posts[r["slug"]], **r} for r in stats if r["slug"] in posts]
    with7 = {r["slug"] for r in rows if str(r["day"]).strip() == "7"}
    need = CFG["analytics"]["learn_min_posts"]
    if len(with7) < need:
        print(f"Мало данных: статей со статистикой за 7 дней {len(with7)}, нужно {need}.")
        return
    text = ask(prompt("learn", rows=rows), as_json=False)
    (DATA / "insights.md").write_text(text.strip() + "\n", encoding="utf-8")
    print("✓ Выводы сохранены в data/insights.md — учитываются при отборе тем и брифах.\n")
    print(text)
