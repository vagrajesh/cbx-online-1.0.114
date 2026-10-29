"""1.0.109 (round 2, 23 Oct): pictures inside replies, like Teams.

The reply box on a ticket is a small editor: words, a few marks (bold, italic, lists, line breaks) and pictures pasted where
they belong. What the browser sends is never trusted:

- `clean_html(html, pics)` keeps only `p br b strong i em ul ol li` and `<img data-n="N">` (N: the ticket file the
  i-th picture became), drops every attribute, every other tag (its words stay, as plain text) and the inside of
  `script`/`style`/`template`; links become their words. `plain(html)` is the same message as plain text — what search,
  the plain part of the mail and every older screen read.
- `picture(name, data)` takes a pasted/dropped picture: PNG, JPEG, GIF or WebP only (by its bytes, not its name), at most
  5 MB, re-encoded so nothing but the pixels is kept (EXIF, GPS, comments, colour profiles go). Ten per message.

The pictures become ordinary ticket files (core.add_file) under their message (`msg`), marked `inline` and `body` (they sit
inside the text, not under it); a note's pictures are never the requester's (`staff_mail`).
"""
from __future__ import annotations

import io
import re
from html import escape
from html.parser import HTMLParser
from typing import Optional

MAX_PICS = 10
MAX_PIC = 5 * 1024 * 1024
MAX_HTML = 60_000
MAX_PIXELS = 40_000_000  # 1.0.109 (review): about 40 MP — a screenshot is far smaller
MAX_FRAMES = 100  # a GIF with more frames keeps its first
KINDS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "GIF": ("gif", "image/gif"), "WEBP": ("webp", "image/webp")}
KEEP = {"p", "br", "b", "strong", "i", "em", "ul", "ol", "li"}
AS_P = {"div", "section", "article", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}  # a block the editor may make: a paragraph
DROP = {"script", "style", "template", "noscript", "iframe", "object", "embed", "svg", "math", "head", "title", "textarea", "select"}
VOID = {"br", "img"}


class _Clean(HTMLParser):
    def __init__(self, pics: dict):
        super().__init__(convert_charrefs=True)
        self.pics, self.out, self.stack, self.skip, self.used = pics, [], [], 0, []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            if tag in DROP:
                self.skip += 1
            return
        if tag in DROP:
            self.skip = 1
            return
        if tag == "img":
            a = dict(attrs)
            n = None
            k = a.get("data-k")
            if k is not None and str(k).isdigit():
                n = self.pics.get(int(k))
            if n is not None and n not in self.used:
                self.used.append(n)
                self.out.append(f'<img data-n="{int(n)}">')
            return  # any other picture (a pasted <img src=…> from another page) is dropped
        if tag in AS_P:
            tag = "p"
        if tag not in KEEP:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag == "p" and "p" in self.stack:  # no paragraph inside a paragraph
            self._close("p")
        if tag == "li" and "li" in self.stack:  # <li>one<li>two: the first item ends where the next begins (in the same list)
            last_li = len(self.stack) - 1 - self.stack[::-1].index("li")
            lists = [i for i, x in enumerate(self.stack) if x in ("ul", "ol")]
            if not lists or lists[-1] < last_li:
                self._close("li")
        self.stack.append(tag)
        self.out.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID and tag.lower() in KEEP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag in DROP:
                self.skip -= 1
            return
        if tag in AS_P:
            tag = "p"
        if tag in KEEP and tag != "br" and tag in self.stack:
            self._close(tag)

    def _close(self, tag):
        while self.stack:
            x = self.stack.pop()
            self.out.append(f"</{x}>")
            if x == tag:
                break

    def handle_data(self, data):
        data = (data or "").replace("\u200b", "").replace("\ufeff", "")  # the editor's caret holders
        if not self.skip and data:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        s = "".join(self.out)
        s = re.sub(r"<(p|b|strong|i|em|li|ul|ol)>\s*</\1>", "", s)  # empty marks
        s = re.sub(r"(?:<br>\s*){3,}", "<br><br>", s)
        return s.strip()


def clean_html(html: str, pics: Optional[dict] = None) -> tuple[str, list[int]]:
    """(the safe HTML, the picture numbers used in order). `pics`: {placeholder index k: ticket file n}."""
    p = _Clean(pics or {})
    p.feed((html or "")[: MAX_HTML * 2])
    p.close()
    out = p.result()
    if len(out) > MAX_HTML:
        raise ValueError("That message is too long.")
    return out, p.used


def plain(html: str) -> str:
    """The safe HTML as plain words: paragraphs and line breaks as new lines, list items as "- …", pictures as [picture]."""
    s = html or ""
    s = re.sub(r"<img[^>]*>", "[picture]", s)
    s = re.sub(r"<br>", "\n", s)
    s = re.sub(r"<li>", "\n- ", s)
    s = re.sub(r"</p>|</li>|</ul>|</ol>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    from html import unescape

    s = unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def has_marks(html: str) -> bool:
    """Anything a plain-text message couldn't say (a picture in the words, bold, a list)?"""
    return bool(re.search(r"<(img|b|strong|i|em|ul|ol)\b", html or ""))


def picture(name: str, data: bytes) -> tuple[str, bytes]:
    """(a safe file name, the re-encoded bytes) — or ValueError in plain words."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data:
        raise ValueError(f"{name or 'A picture'} is empty.")
    if len(data) > MAX_PIC:
        raise ValueError(f"{name or 'A picture'} is over 5 MB.")
    try:
        img = Image.open(io.BytesIO(data))  # reads the header only
        fmt = img.format
        w, h = img.size
        # 1.0.109 (review): a decompression bomb is refused by its size before a pixel is decoded (PIL's own global limit untouched)
        if w * h > MAX_PIXELS:
            raise ValueError(f"{name or 'A picture'} is too large ({w} × {h} pixels).")
        frames = getattr(img, "n_frames", 1) if fmt == "GIF" else 1
        img.load()
    except ValueError as e:
        if "too large" in str(e):
            raise
        raise ValueError(f"{name or 'A picture'} isn't a picture CBX can take (PNG, JPEG, GIF or WebP).") from e
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError(f"{name or 'A picture'} isn't a picture CBX can take (PNG, JPEG, GIF or WebP).") from e
    if fmt not in KINDS:
        raise ValueError(f"{name or 'A picture'}: only PNG, JPEG, GIF or WebP pictures.")
    ext, _ = KINDS[fmt]
    out = io.BytesIO()
    if fmt == "GIF":
        if frames > MAX_FRAMES or frames * w * h > MAX_PIXELS:  # 1.0.109 (review): a long or huge animation keeps its first frame
            frames = 1
        if frames > 1:
            img.save(out, "GIF", save_all=True, loop=img.info.get("loop", 0), duration=img.info.get("duration", 100))
        else:
            img.save(out, "GIF")
    else:
        try:
            img = ImageOps.exif_transpose(img)  # a phone photo the right way up, then its EXIF is gone
        except Exception:  # noqa: BLE001 — an odd EXIF block: keep the picture as it is
            pass
        if fmt == "JPEG":
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(out, "JPEG", quality=90, optimize=True)
        elif fmt == "PNG":
            if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                img = img.convert("RGBA")
            img.save(out, "PNG", optimize=True)
        else:
            img.save(out, "WEBP", quality=90)
    stem = re.sub(r"[^\w\- ]", "_", (name or "picture").rsplit(".", 1)[0])[:60].strip() or "picture"
    return f"{stem}.{ext}", out.getvalue()


def attach(db, t, m, html: str, pics: list, by: str, *, author_id: Optional[int], note: bool) -> list[int]:
    """Put the message's pictures on the ticket (under message m) and give the message its safe HTML. `pics`: [(name, bytes)]
    already through picture(). Returns the file numbers."""
    from . import core as C

    nums: dict[int, int] = {}
    for k, (name, data) in enumerate(pics):
        item = C.add_file(db, t, name, data, by, inline=True, author_id=author_id, msg_id=m.id, staff_mail=note)
        if item:
            nums[k] = item["n"]
    safe, used = clean_html(html, nums)
    # a picture that came but isn't in the words (the browser lost its place) still shows: at the end
    for k, n in nums.items():
        if n not in used:
            safe += f'<p><img data-n="{n}"></p>'
            used.append(n)
    if used:
        files = []
        for f in t.files or []:
            f = dict(f)
            if f.get("n") in used and f.get("msg") == m.id:
                f["body"] = True  # inside the words, not under them
            files.append(f)
        t.files = files
    m.html = safe if (used or has_marks(safe)) else ""
    return list(nums.values())
