"""Согласование в Telegram. Работает без сервера: каждый запуск tg-sync
забирает нажатия кнопок/ответы и отправляет новые черновики."""
import html
import json
import os
import uuid
import urllib.error
import urllib.request

from .core import CFG, DRAFTS, INPUTS, KB, load_json, now, read_post, save_json, write_post
from .photos import local_file, resized_jpeg
from .publish import approve, article_html, reject, status_text

STATE = "telegram.json"


def _token():
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        raise SystemExit("Нужны переменные TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
    return tok, str(chat)


def api(method: str, fields=None, files=None):
    tok, _ = _token()
    url = f"https://api.telegram.org/bot{tok}/{method}"
    fields = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
              for k, v in (fields or {}).items() if v is not None}
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in fields.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        for k, (name, data, mime) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, json.dumps(fields).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        # Показываем, что именно не понравилось Telegram, а не просто «400 Bad Request»
        try:
            desc = json.loads(e.read()).get("description", "")
        except Exception:
            desc = ""
        raise RuntimeError(f"Telegram {method}: {e.code} {desc}".strip()) from None
    if not res.get("ok"):
        raise RuntimeError(f"Telegram {method}: {res}")
    return res["result"]


def send(text: str, **kw):
    return api("sendMessage", {"chat_id": _token()[1], "text": text, "parse_mode": "HTML",
                               "disable_web_page_preview": True, **kw})


# ───────── отправка черновика ─────────

def card_text(meta) -> str:
    c = meta.get("check", {})
    lines = [f"<b>📝 Новая статья</b>",
             f"<b>{html.escape(meta['title'])}</b>", "",
             f"Тема: {html.escape(meta.get('topic', ''))}",
             f"Балл темы: {meta.get('score', '—')}/100",
             f"Проверка: {'✅ пройдена' if c.get('verdict') == 'pass' else '⚠️ остались замечания'}"
             f" (кругов доработки: {c.get('rounds', 0)})"]
    if c.get("open_issues"):
        lines += ["", "<b>Нерешённые замечания:</b>"] + [f"• {html.escape(i)}" for i in c["open_issues"]]
    variants = meta.get("title_variants", [])
    if len(variants) > 1:
        lines += ["", "<b>Варианты заголовка:</b>"] + [f"{i + 1}. {html.escape(t)}" for i, t in enumerate(variants)]
    if meta.get("notes_done"):
        lines += ["", "<i>Доработано по вашим комментариям</i>"]
    return "\n".join(lines)[:4000]


def keyboard(sid: int, n_titles: int):
    rows = [[{"text": "✅ Опубликовать", "callback_data": f"ok:{sid}"}],
            [{"text": "✏️ На доработку", "callback_data": f"rev:{sid}"},
             {"text": "❌ Отклонить", "callback_data": f"no:{sid}"}]]
    if n_titles > 1:
        rows.insert(1, [{"text": f"Заг. {i + 1}", "callback_data": f"t:{sid}:{i}"}
                        for i in range(min(n_titles, 7))])
    return {"inline_keyboard": rows}


def preview_file(slug, meta, body) -> bytes:
    from .render import page
    content = f"<h1>{html.escape(meta['title'])}</h1><p class='box'>{html.escape(meta.get('description', ''))}</p>"
    return page(meta["title"], "", content + article_html(slug, meta, body, embed=True)).encode()


def send_draft(path, state):
    meta, body = read_post(path)
    slug = path.stem
    sid = state["next_id"]
    state["next_id"] += 1
    state["ids"][str(sid)] = slug
    chat = _token()[1]

    cover = local_file(meta["cover"]) if meta.get("cover") else None
    if cover and cover.exists():
        img, _ = resized_jpeg(cover, 1280)
        api("sendPhoto", {"chat_id": chat}, {"photo": ("cover.jpg", img, "image/jpeg")})
    msg = send(card_text(meta), reply_markup=keyboard(sid, len(meta.get("title_variants", []))))
    doc = api("sendDocument", {"chat_id": chat, "caption": "Полный текст — откройте файл. "
                               "Чтобы отправить на доработку, ответьте на это или предыдущее сообщение."},
              {"document": (f"{slug}.html", preview_file(slug, meta, body), "text/html")})
    for m in (msg, doc):
        state["msgs"][str(m["message_id"])] = slug
    meta["status"] = "sent"
    meta["tg"] = sid
    write_post(path, meta, body)


# ───────── обработка входящих ─────────

def handle_callback(cq, state):
    data = cq.get("data", "")
    parts = data.split(":")
    slug = state["ids"].get(parts[1]) if len(parts) > 1 else None
    reply = "Черновик уже обработан"
    if slug and (DRAFTS / f"{slug}.md").exists():
        path = DRAFTS / f"{slug}.md"
        if parts[0] == "ok":
            slot = approve(slug)
            reply = f"✅ В расписании: {slot:%d.%m в %H:%M}. Придёт в Студию Дзена черновиком."
        elif parts[0] == "no":
            reject(slug)
            reply = "❌ Отклонено"
        elif parts[0] == "rev":
            meta, body = read_post(path)
            meta["status"] = "revise"
            write_post(path, meta, body)
            reply = "✏️ Ответьте на сообщение со статьёй: что исправить. Доработаю при следующем запуске."
        elif parts[0] == "t":
            meta, body = read_post(path)
            meta["title"] = meta["title_variants"][int(parts[2])]
            write_post(path, meta, body)
            reply = f"Заголовок: {meta['title']}"
    # Бот приходит раз в 30 минут, и Telegram к этому времени считает нажатие «просроченным»
    # и отвечает ошибкой 400. Это нормально для бота без сервера — просто пропускаем.
    try:
        api("answerCallbackQuery", {"callback_query_id": cq["id"]})
    except Exception:
        pass
    send(html.escape(reply))


def handle_message(msg, state):
    text = (msg.get("text") or "").strip()
    if text.startswith("/status"):
        send(f"<pre>{html.escape(status_text())[:3900]}</pre>")
        return
    if text.startswith(("/fact", "/idea")):
        cmd, _, value = text.partition(" ")
        value = value.strip()
        if not value:
            send("Напишите текст после команды, например: /fact Трапы в хамаме всегда ставим отдельно от душа")
            return
        if cmd == "/fact":
            path = KB / "from_telegram.md"
            if not path.exists():
                path.write_text("# Факты от владельца (из Telegram)\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as f:
                f.write(f"- {value}\n")
            send("📚 Добавлено в базу знаний. Будет учитываться в новых статьях.")
        else:
            with (INPUTS / "ideas.txt").open("a", encoding="utf-8") as f:
                f.write(value + "\n")
            send("💡 Идея добавлена — попадёт в следующий отбор тем.")
        return
    if text.startswith("/start"):
        send(f"Бот конвейера на связи. Ваш chat_id: <code>{msg['chat']['id']}</code>")
        return
    ref = (msg.get("reply_to_message") or {}).get("message_id")
    slug = state["msgs"].get(str(ref))
    if slug and text and (DRAFTS / f"{slug}.md").exists():
        path = DRAFTS / f"{slug}.md"
        meta, body = read_post(path)
        meta.setdefault("notes", []).append(text)
        meta["status"] = "revise"
        write_post(path, meta, body)
        send("✏️ Принято. Доработаю и пришлю снова.")


def cmd_tg_sync(_args=None):
    _, chat = _token()
    state = load_json(STATE, {"offset": 0, "next_id": 1, "ids": {}, "msgs": {}, "reminded": []})

    for upd in api("getUpdates", {"offset": state["offset"], "timeout": 0}):
        state["offset"] = upd["update_id"] + 1
        try:
            if "callback_query" in upd:
                if str(upd["callback_query"]["message"]["chat"]["id"]) == chat:
                    handle_callback(upd["callback_query"], state)
            elif "message" in upd:
                if str(upd["message"]["chat"]["id"]) == chat or upd["message"].get("text", "").startswith("/start"):
                    handle_message(upd["message"], state)
        except Exception as e:
            send(f"⚠️ Ошибка: {html.escape(str(e))}")
        save_json(STATE, state)

    if CFG.get("moderation") == "telegram":
        for path in sorted(DRAFTS.glob("*.md")):
            if read_post(path)[0].get("status") == "new":
                send_draft(path, state)
                save_json(STATE, state)
                print(f"  → отправлен на согласование: {path.stem}")

    from .analytics import due
    todo = [d for d in due() if f"{d[0]}:{d[1]}" not in state["reminded"]]
    if todo:
        lines = ["<b>📊 Пора внести статистику</b> (inputs/stats.csv):"]
        lines += [f"• {html.escape(s)} — день {d}" for s, d, _ in todo]
        send("\n".join(lines))
        state["reminded"] += [f"{s}:{d}" for s, d, _ in todo]
    save_json(STATE, state)
    print(f"✓ Telegram синхронизирован ({now():%H:%M})")
