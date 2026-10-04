#!/usr/bin/env python3
"""Забирает новые фото из публичной папки Яндекс Диска в папку photos/.

Как работает:
  • папка на Яндекс Диске открыта «по ссылке», ссылка лежит в секрете YADISK_PUBLIC_URL;
  • берутся файлы .jpg .jpeg .png .webp (в том числе из подпапок);
  • имя файла переводится в латиницу (так требует конвейер: только a-z, 0-9, - и _);
  • фото уменьшается до 2000 px по длинной стороне и сохраняется как JPEG;
  • уже скачанные фото запоминаются в data/yadisk.json и повторно не качаются.
Удаление или переименование фото на Диске на репозиторий не влияет.
"""
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
PHOTOS = Path("photos")
STATE = Path("data/yadisk.json")
EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_SIDE = 2000
MAX_PER_RUN = int(os.getenv("YADISK_MAX_PER_RUN", "50"))   # чтобы не раздувать один коммит

TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
              ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
               "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(name: str) -> str:
    s = "".join(TR.get(ch, ch) for ch in name.lower())
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return s or "photo"


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "dzen-machine"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def walk(public_key: str, path: str = ""):
    offset = 0
    while True:
        params = {"public_key": public_key, "limit": 200, "offset": offset}
        if path:
            params["path"] = path
        data = get_json(API + "?" + urllib.parse.urlencode(params))
        items = data.get("_embedded", {}).get("items", [])
        for it in items:
            if it.get("type") == "dir":
                yield from walk(public_key, it["path"])
            elif Path(it.get("name", "")).suffix.lower() in EXT:
                yield it
        if len(items) < 200:
            break
        offset += 200


def download(public_key: str, item) -> bytes:
    url = item.get("file")
    if not url:
        q = urllib.parse.urlencode({"public_key": public_key, "path": item["path"]})
        url = get_json(API + "/download?" + q)["href"]
    req = urllib.request.Request(url, headers={"User-Agent": "dzen-machine"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    public_key = os.getenv("YADISK_PUBLIC_URL", "").strip()
    if not public_key:
        sys.exit("Нет секрета YADISK_PUBLIC_URL — добавьте ссылку на папку Яндекс Диска")
    PHOTOS.mkdir(exist_ok=True)
    STATE.parent.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    taken = {p.stem for p in PHOTOS.iterdir() if p.is_file()}
    added = 0
    for item in walk(public_key):
        key = item.get("md5") or item.get("path")
        if key in state:
            continue
        if added >= MAX_PER_RUN:
            print(f"Достигнут лимит {MAX_PER_RUN} фото за запуск — остальные заберутся в следующий раз.")
            break
        base = slug(Path(item["name"]).stem)
        pid, n = base, 2
        while pid in taken:
            pid, n = f"{base}-{n}", n + 1
        try:
            img = Image.open(io.BytesIO(download(public_key, item)))
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            img.save(PHOTOS / f"{pid}.jpg", "JPEG", quality=85, optimize=True)
        except Exception as e:
            print(f"  ✗ {item['name']}: {e}")
            continue
        state[key] = pid
        taken.add(pid)
        added += 1
        print(f"  + {item['name']} → photos/{pid}.jpg")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ Новых фото: {added}")


if __name__ == "__main__":
    main()
