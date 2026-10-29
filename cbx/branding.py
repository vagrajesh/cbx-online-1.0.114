"""Branding: product name, organisation, tagline, the AI's name, logo, favicon, accent colour.

Two layers, lowest wins last:
  1. `.env` / environment (cbx.config.Settings: CBX_PRODUCT_NAME, CBX_ORG_NAME, CBX_TAGLINE,
     CBX_AI_NAME, CBX_LOGO_FILE, CBX_FAVICON_FILE, CBX_ACCENT) — the install-time defaults.
  2. DATA_DIR/branding/branding.json — what the owner saved from Settings → Branding. Lives in the
     data folder, so a promoted version never undoes it and no `.env` editing is needed.

Uploaded images are stored as DATA_DIR/branding/logo.<ext> and favicon.<ext>. Everything here is
read often (the AI's name goes into every prompt), so the JSON is cached and re-read only when
its modification time changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .config import ROOT, settings

KEYS = ("product", "org", "tagline", "ai_name", "accent")
IMAGE_TYPES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF8": "gif",
    b"\x00\x00\x01\x00": "ico",
    b"RIFF": "webp",
}
MAX_IMAGE_BYTES = 2 * 1024 * 1024
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

_cache: dict = {"mtime": None, "data": {}}


def _dir() -> Path:
    return settings.path("branding")


def _json_path() -> Path:
    return settings.data_dir / "branding" / "branding.json"


def _overrides() -> dict:
    p = _json_path()
    try:
        mtime = p.stat().st_mtime_ns
    except FileNotFoundError:
        _cache.update(mtime=None, data={})
        return {}
    if _cache["mtime"] != mtime:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _cache.update(mtime=mtime, data=data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            _cache.update(mtime=mtime, data={})
    return _cache["data"]


def _write(data: dict) -> None:
    _dir()
    _json_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _cache.update(mtime=None)  # force a re-read


def defaults() -> dict:
    return {"product": settings.product_name, "org": settings.org_name, "tagline": settings.tagline, "ai_name": settings.ai_name, "accent": settings.accent}


def current() -> dict:
    """The effective branding text: .env defaults with the owner's saved overrides on top."""
    d = defaults()
    o = _overrides()
    for k in KEYS:
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            d[k] = v.strip()
    # "AXL" was the name before 1.0.49 (the default, and what .env.example set): left over, it now means
    # C-EXA. A name the owner saves after 1.0.56 stays as typed — AXL included (ai_name_chosen).
    if not d["ai_name"] or (d["ai_name"].strip().upper() == "AXL" and not o.get("ai_name_chosen")):
        d["ai_name"] = "C-EXA"
    if not d["product"]:
        d["product"] = "CBX-Connect"
    return d


def ai_name() -> str:
    return current()["ai_name"]


def product_name() -> str:
    return current()["product"]


def _image(kind: str, env_value: str) -> Optional[Path]:
    """Path of the logo/favicon in force: the uploaded one if any, else the .env path if it exists."""
    o = _overrides()
    up = o.get(f"{kind}_file")
    if isinstance(up, str) and up:
        p = Path(up)
        if not p.is_absolute():
            p = settings.data_dir / "branding" / p
        if p.is_file():
            return p
    if env_value:
        p = Path(env_value.replace("\\", "/"))  # .env files are written on Windows; run anywhere
        if not p.is_absolute():
            p = ROOT / p  # relative to the app folder (next to run.bat)
        if p.is_file():
            return p
    return None


def logo_path() -> Optional[Path]:
    return _image("logo", settings.logo_file)


def favicon_path() -> Optional[Path]:
    return _image("favicon", settings.favicon_file)


def _stamp(p: Optional[Path]) -> int:
    try:
        return int(p.stat().st_mtime) if p else 0
    except OSError:
        return 0


def public() -> dict:
    """What the sign-in page and the shell need before anyone is signed in. No secrets, no model names."""
    from . import VERSION

    c = current()
    lp, fp = logo_path(), favicon_path()
    return {**c, "has_logo": lp is not None, "has_favicon": fp is not None, "logo_v": _stamp(lp), "favicon_v": _stamp(fp), "version": VERSION}


class BrandingError(ValueError):
    pass


def save_text(**changes: Optional[str]) -> dict:
    """Owner saves product/org/tagline/ai_name/accent. Empty string = back to the .env default."""
    o = dict(_overrides())
    for k, v in changes.items():
        if k not in KEYS or v is None:
            continue
        v = str(v).strip()
        if len(v) > 80:
            raise BrandingError("Keep names under 80 characters.")
        if k == "ai_name" and v and not re.match(r"^[A-Za-z][A-Za-z0-9 .'\-]{0,39}$", v):
            raise BrandingError("The AI's name should start with a letter and use letters, digits, spaces, dots or hyphens only.")
        if k == "accent" and v and not _HEX.match(v):
            raise BrandingError("The accent colour must be a hex value like #0B8A63.")
        if v:
            o[k] = v
            if k == "ai_name":
                o["ai_name_chosen"] = True  # the owner typed it: never swapped for C-EXA
        else:
            o.pop(k, None)
            if k == "ai_name":
                o.pop("ai_name_chosen", None)
    _write(o)
    return current()


def image_type(data: bytes, filename: str = "") -> Optional[str]:
    for magic, ext in IMAGE_TYPES.items():
        if data.startswith(magic):
            return ext
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower()):
        return "svg"
    return None


def trim_margins(data: bytes, ext: str) -> bytes:
    """A logo exported from a design tool often sits in a sea of transparent (or white) padding; inside a
    44-px tile that leaves a speck. Crop to the visible pixels, with a small breathing margin. Best effort:
    anything Pillow can't read is stored as it came."""
    if ext not in ("png", "webp", "gif", "jpg"):
        return data
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            box = im.convert("RGBA").getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
        else:
            from PIL import ImageChops

            rgb = im.convert("RGB")
            bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
            box = ImageChops.difference(rgb, bg).convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
        if not box:
            return data
        w, h = im.size
        pad = max(2, int(0.03 * max(box[2] - box[0], box[3] - box[1])))
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad))
        if box == (0, 0, w, h):
            return data
        out = io.BytesIO()
        im.crop(box).save(out, format="PNG" if ext in ("png", "gif") else im.format or "PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001 — trimming is a nicety
        return data


def save_image(kind: str, data: bytes, filename: str = "") -> Path:
    """Store an uploaded logo/favicon (PNG, JPG, GIF, WEBP, ICO or SVG, ≤ 2 MB) and point the overrides at it.
    Logos have their empty margins trimmed so they fill the tile."""
    if kind not in ("logo", "favicon"):
        raise BrandingError("Unknown image.")
    if not data:
        raise BrandingError("That file was empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise BrandingError("Keep the image under 2 MB.")
    ext = image_type(data, filename)
    if ext is None:
        raise BrandingError("Use a PNG, JPG, GIF, WEBP, ICO or SVG image.")
    if ext == "svg" and re.search(rb"<script|on\w+\s*=|javascript:", data, flags=re.I):
        raise BrandingError("That SVG contains script, which isn't allowed.")
    if kind == "logo":
        data = trim_margins(data, ext)
        if ext == "gif":
            ext = "png"
    d = _dir()
    for old in d.glob(f"{kind}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    p = d / f"{kind}.{ext}"
    p.write_bytes(data)
    o = dict(_overrides())
    o[f"{kind}_file"] = p.name
    _write(o)
    return p


def clear_image(kind: str) -> None:
    if kind not in ("logo", "favicon"):
        raise BrandingError("Unknown image.")
    o = dict(_overrides())
    name = o.pop(f"{kind}_file", None)
    if isinstance(name, str) and name:
        p = settings.data_dir / "branding" / name
        try:
            p.unlink()
        except OSError:
            pass
    _write(o)


MEDIA = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "ico": "image/x-icon", "webp": "image/webp", "svg": "image/svg+xml"}


def media_type(p: Path) -> str:
    return MEDIA.get(p.suffix.lstrip(".").lower(), "application/octet-stream")
