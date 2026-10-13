"""Общие утилиты: конфиг, пути, посты, база знаний, вызов Claude."""
import base64
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
TZ = ZoneInfo(CFG["schedule"]["timezone"])
BASE = CFG["channel"]["base_url"].rstrip("/")

DRAFTS = ROOT / "posts" / "drafts"
APPROVED = ROOT / "posts" / "approved"
REJECTED = ROOT / "posts" / "rejected"
DATA = ROOT / "data"
INPUTS = ROOT / "inputs"
KB = ROOT / "kb"
PHOTOS = ROOT / "photos"
PROMPTS = ROOT / "prompts"
SITE = ROOT / "site"
for _d in (DRAFTS, APPROVED, REJECTED, DATA):
    _d.mkdir(parents=True, exist_ok=True)


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def parse_dt(value) -> dt.datetime:
    d = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


# ───────── JSON-хранилище ─────────

def load_json(name: str, default):
    path = DATA / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(name: str, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ───────── посты ─────────

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slugify(text: str, max_len: int = 60) -> str:
    s = "".join(_TR.get(c, c) for c in text.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "post"


def unique_slug(base: str) -> str:
    taken = {p.stem for d in (DRAFTS, APPROVED, REJECTED) for p in d.glob("*.md")}
    slug, i = base, 2
    while slug in taken:
        slug, i = f"{base}-{i}", i + 1
    return slug


def read_post(path: Path):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", path.read_text(encoding="utf-8"), re.S)
    if not m:
        raise ValueError(f"{path.name}: нет блока метаданных ---")
    return yaml.safe_load(m.group(1)) or {}, m.group(2).strip()


def write_post(path: Path, meta: dict, body: str):
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000).strip()
    path.write_text(f"---\n{front}\n---\n\n{body.strip()}\n", encoding="utf-8")


def post_path(slug: str, folder: Path = DRAFTS) -> Path:
    path = folder / f"{slug.removesuffix('.md')}.md"
    if not path.exists():
        raise FileNotFoundError(f"Не найден {path.relative_to(ROOT)}")
    return path


# ───────── база знаний ─────────

def _clean(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    # убираем заголовки, под которыми ничего нет
    blocks = re.split(r"(?m)^(?=#)", text)
    keep = [b for b in blocks if b.strip() and (not b.lstrip().startswith("#")
                                                or len(b.strip().splitlines()) > 1)]
    return "\n".join(b.strip() for b in keep if b.strip()).strip()


def knowledge_base() -> str:
    parts = []
    for path in [*sorted(KB.glob("*.md")), *sorted((KB / "cases").glob("*.md"))]:
        if path.exists() and not path.name.startswith("_"):
            text = _clean(path.read_text(encoding="utf-8"))
            if len(text.splitlines()) > 1 or (text and not text.startswith("#")):
                parts.append(f"### {path.stem}\n{text}")
    return "\n\n".join(parts)


def kb_block() -> str:
    kb = knowledge_base()
    if not kb:
        return ("БАЗА ЗНАНИЙ КОМПАНИИ ПУСТА. Никаких утверждений об опыте, объектах, ценах, "
                "сроках или клиентах компании. Пиши как эксперт отрасли, без «мы делали», "
                "«у нас был случай», «наши клиенты».")
    return ("БАЗА ЗНАНИЙ КОМПАНИИ (единственный допустимый источник фактов о компании; "
            "всё, чего здесь нет, о компании не утверждать):\n" + kb)


def insights_block() -> str:
    path = DATA / "insights.md"
    return ("ВЫВОДЫ ИЗ СТАТИСТИКИ ПРОШЛЫХ СТАТЕЙ:\n" + path.read_text(encoding="utf-8")) \
        if path.exists() else "Статистики прошлых статей пока нет."


def read_lines(path: Path):
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


# ───────── промпты и Claude ─────────

def prompt(name: str, **values) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    common = {"channel": CFG["channel"]["description"], "company": CFG["channel"]["company"],
              "today": now().strftime("%d.%m.%Y")}
    for key, val in {**common, **values}.items():
        if not isinstance(val, str):
            val = json.dumps(val, ensure_ascii=False, indent=2)
        text = text.replace("{{" + key + "}}", val)
    return text


def extract_json(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("в ответе нет JSON")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])


_client = None


def _use_batch() -> bool:
    """Batch API (−50% к цене) — для утреннего конвейера. Ответы на кнопки и комментарии
    в Telegram идут обычными запросами, чтобы не ждать. CLAUDE_SYNC=1 отключает пакеты."""
    if os.getenv("CLAUDE_SYNC") or not CFG["models"].get("batch"):
        return False
    return os.getenv("GITHUB_WORKFLOW", "") != "Telegram sync"


def _batch_call(model: str, messages: list):
    """Один запрос через Batch API. Если пакет не готов за batch_timeout_min — отменяем
    и возвращаем None (тогда делаем обычный запрос)."""
    timeout = float(CFG["models"].get("batch_timeout_min", 30)) * 60
    req = {"custom_id": "dzen", "params": {"model": model, "max_tokens": CFG["models"]["max_tokens"],
                                           "messages": messages}}
    batch = _client.messages.batches.create(requests=[req])
    started, pause = time.time(), 10
    print(f"    ⏳ пакетный запрос {batch.id} (−50%), ждём ответ…", flush=True)
    while batch.processing_status != "ended":
        if time.time() - started > timeout:
            print(f"    ⌛ пакет не готов за {timeout / 60:.0f} мин — отменяю и делаю обычный запрос", flush=True)
            try:
                _client.messages.batches.cancel(batch.id)
            except Exception:
                pass
            return None
        time.sleep(pause)
        pause = min(pause * 1.5, 60)
        batch = _client.messages.batches.retrieve(batch.id)
    for item in _client.messages.batches.results(batch.id):
        if item.result.type == "succeeded":
            print(f"    ✓ пакет готов за {(time.time() - started) / 60:.1f} мин", flush=True)
            return item.result.message
        err = getattr(item.result, "error", None)
        print(f"    ✗ пакет: {item.result.type} {err or ''} — делаю обычный запрос", flush=True)
    return None


def _call(model: str, messages: list):
    if _use_batch():
        try:
            msg = _batch_call(model, messages)
            if msg is not None:
                return msg
        except Exception as e:
            print(f"    ✗ Batch API недоступен ({e}) — делаю обычный запрос", flush=True)
    return _client.messages.create(model=model, max_tokens=CFG["models"]["max_tokens"], messages=messages)


def ask(text: str, *, role: str = "default", images=None, as_json: bool = True):
    """Один вызов Claude. images — список (bytes, media_type)."""
    global _client
    if _client is None:
        try:
            import anthropic
        except ImportError:
            sys.exit("Установите зависимости: pip install -r requirements.txt")
        _client = anthropic.Anthropic()
    model = os.getenv(f"CLAUDE_MODEL_{role.upper()}") or CFG["models"].get(role) or CFG["models"]["default"]
    content = [{"type": "image", "source": {"type": "base64", "media_type": mt,
                                            "data": base64.b64encode(b).decode()}}
               for b, mt in (images or [])]
    content.append({"type": "text", "text": text})
    messages = [{"role": "user", "content": content}]

    last_err = None
    for _ in range(2):  # повтор, если модель сломала JSON
        resp = _call(model, messages)
        out = "".join(b.text for b in resp.content if b.type == "text")
        if not as_json:
            return out
        try:
            return extract_json(out)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise ValueError(f"модель не вернула корректный JSON: {last_err}")
