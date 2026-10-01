#!/usr/bin/env python3
"""
ДЗЕН МАШИНА v1 — контент-конвейер для Дзена.

  inputs (Wordstat, идеи, конкуренты) → score → topics
  → generate: бриф → текст с фото → критик-эксперт ↻ доработка → заголовки
  → tg-sync: согласование в Telegram → расписание → build: сайт + zen.xml → черновик в Дзене
  → stats/learn: статистика → выводы → влияют на отбор тем

Команды:
  photos              описать новые фото из photos/ (Claude смотрит на снимки)
  score               собрать и оценить темы
  topics              очередь тем с баллами
  generate [-n N]     написать N статей (и доработать черновики по комментариям)
  tg-sync             обмен с Telegram: отправить черновики, принять решения
  list                черновики и расписание
  approve SLUG|--all  одобрить вручную;  reject SLUG  отклонить
  build               собрать сайт и RSS
  stats               какие цифры пора внести;  learn  выводы из статистики
"""
import argparse

from dzen import analytics, photos, publish, telegram, topics, writer


def main():
    ap = argparse.ArgumentParser(description="ДЗЕН МАШИНА v1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, h in [("photos", "описать новые фото"), ("score", "собрать и оценить темы"),
                    ("topics", "очередь тем"), ("tg-sync", "синхронизация с Telegram"),
                    ("list", "черновики и расписание"), ("build", "собрать сайт и RSS"),
                    ("stats", "какую статистику внести"), ("learn", "выводы из статистики")]:
        sub.add_parser(name, help=h)
    g = sub.add_parser("generate", help="написать статьи")
    g.add_argument("-n", "--count", type=int, default=0)
    g.add_argument("--rework-only", action="store_true", help="только доработать черновики по комментариям")
    a = sub.add_parser("approve", help="одобрить")
    a.add_argument("slugs", nargs="*")
    a.add_argument("--all", action="store_true")
    r = sub.add_parser("reject", help="отклонить")
    r.add_argument("slugs", nargs="+")
    args = ap.parse_args()
    {"photos": photos.cmd_photos, "score": topics.cmd_score, "topics": topics.cmd_topics,
     "generate": writer.cmd_generate, "tg-sync": telegram.cmd_tg_sync, "list": publish.cmd_list,
     "approve": publish.cmd_approve, "reject": publish.cmd_reject, "build": publish.cmd_build,
     "stats": analytics.cmd_stats, "learn": analytics.cmd_learn}[args.cmd](args)


if __name__ == "__main__":
    main()
