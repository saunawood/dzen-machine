"""Фото объектов: автоописание через Claude, подбор к статье, подготовка для сайта."""
import base64
import hashlib
import io
import re
from pathlib import Path

import yaml

from .core import BASE, PHOTOS, SITE, ask, prompt, slugify

CATALOG = PHOTOS / "catalog.yaml"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_WIDTH = 700  # требование Дзена к иллюстрациям


def load_catalog() -> dict:
    data = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else None
    return data or {}


def save_catalog(cat: dict):
    head = ("# Каталог фото. Проверьте описания и поставьте confirmed: true.\n"
            "# Неподтверждённые фото используются с нейтральной подписью.\n")
    CATALOG.write_text(head + yaml.safe_dump(cat, allow_unicode=True, sort_keys=True, width=1000),
                       encoding="utf-8")


def photo_files():
    return sorted(p for p in PHOTOS.rglob("*") if p.suffix.lower() in EXTS)


def photo_id(path: Path) -> str:
    """Латинский id из имени файла (русские имена транслитерируются)."""
    rel = path.relative_to(PHOTOS).with_suffix("").as_posix()
    base = slugify(rel.replace("/", "-"), 80)
    if base == "post":  # имя без букв и цифр
        base = "photo-" + hashlib.md5(rel.encode()).hexdigest()[:8]
    return base


def resized_jpeg(path: Path, max_side: int, quality: int = 82):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = img.size
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), w


def cmd_photos(_args=None):
    """Описывает новые фото через Claude и добавляет в каталог."""
    cat = load_catalog()
    new = [p for p in photo_files() if photo_id(p) not in cat]
    if not new:
        print(f"Новых фото нет. В каталоге: {len(cat)}")
        return
    for path in new:
        pid = photo_id(path)
        try:
            data, width = resized_jpeg(path, 1024)
            info = ask(prompt("photo_tag"), images=[(data, "image/jpeg")])
        except Exception as e:
            print(f"  ✗ {path.name}: {e}")
            continue
        cat[pid] = {
            "file": path.relative_to(PHOTOS).as_posix(),
            "room": info.get("room", ""),
            "tags": info.get("tags", []),
            "description": info.get("description", ""),
            "object": "",          # заполните сами: «Хамам 4 м², дом в Истре»
            "width": width,
            "usable": bool(info.get("usable", True)) and width >= MIN_WIDTH,
            "privacy_flag": info.get("privacy_flag", ""),
            "confirmed": False,
        }
        flag = f"  ⚠ {info['privacy_flag']}" if info.get("privacy_flag") else ""
        small = "  ⚠ меньше 700px" if width < MIN_WIDTH else ""
        print(f"  ✓ {pid}: {info.get('room', '?')} — {', '.join(cat[pid]['tags'][:5])}{flag}{small}")
    save_catalog(cat)
    print(f"\nПроверьте photos/catalog.yaml: поправьте описания, заполните object, поставьте confirmed: true.")


def candidates(tags, limit: int = 12):
    """Фото, лучше всего подходящие по тегам темы."""
    want = {t.lower() for t in tags or []}
    scored = []
    for pid, p in load_catalog().items():
        if not p.get("usable", True):
            continue
        have = {t.lower() for t in p.get("tags", [])} | {p.get("room", "").lower()}
        hits = len(want & have) + sum(1 for w in want for h in have if w != h and (w in h or h in w)) * 0.5
        scored.append((hits + (0.3 if p.get("confirmed") else 0), pid, p))
    scored.sort(key=lambda x: -x[0])
    return [{"id": pid, "room": p.get("room"), "description": p.get("description"),
             "object": p.get("object") if p.get("confirmed") else "", "confirmed": bool(p.get("confirmed"))}
            for s, pid, p in scored[:limit] if s > 0] or \
           [{"id": pid, "room": p.get("room"), "description": p.get("description"), "object": "",
             "confirmed": bool(p.get("confirmed"))} for s, pid, p in scored[:limit // 2]]


def public_url(pid: str) -> str:
    return f"{BASE}/static/photos/{pid}.jpg"


def data_uri(pid: str, max_side: int = 900) -> str:
    p = load_catalog().get(pid)
    if not p:
        return ""
    data, _ = resized_jpeg(PHOTOS / p["file"], max_side, 75)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def export_for_site(used_ids):
    """Кладёт использованные фото в site/static/photos (до 1600px)."""
    out = SITE / "static" / "photos"
    out.mkdir(parents=True, exist_ok=True)
    cat = load_catalog()
    for pid in used_ids:
        if pid in cat and not (out / f"{pid}.jpg").exists():
            data, _ = resized_jpeg(PHOTOS / cat[pid]["file"], 1600)
            (out / f"{pid}.jpg").write_bytes(data)


def local_file(pid: str):
    p = load_catalog().get(pid)
    return PHOTOS / p["file"] if p else None
