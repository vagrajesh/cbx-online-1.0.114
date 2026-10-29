"""1.0.109 (round 2): read an Outlook .msg for the preview — its subject, from, to, cc and the plain-text body — with no extra
package (a .msg is an OLE compound file: a little FAT file system). Read-only, bounded, and never trusted: a broken or odd
file gives {} and the preview falls back to name, size and Download."""
from __future__ import annotations

import re
import struct

END, FREE = 0xFFFFFFFE, 0xFFFFFFFF
SIG = bytes.fromhex("D0CF11E0A1B11AE1")
MAX_SECTORS = 200_000
MAX_BYTES = 40 * 1024 * 1024  # 1.0.109 (review): the most one .msg may expand to while it is read


def _chain(fat: list[int], start: int) -> list[int]:
    out, seen = [], set()
    s = start
    while s not in (END, FREE) and 0 <= s < len(fat) and s not in seen and len(out) < MAX_SECTORS:
        seen.add(s)
        out.append(s)
        s = fat[s]
    return out


def streams(data: bytes) -> dict[str, bytes]:
    """The root's own streams (not those inside attachment or recipient storages): {name: bytes}."""
    if len(data) < 512 or data[:8] != SIG:
        return {}
    # 1.0.109 (review): only the shifts a compound file may have (9 or 12, mini 6) — checked before anything is shifted
    sshift, mshift = struct.unpack_from("<H", data, 0x1E)[0], struct.unpack_from("<H", data, 0x20)[0]
    if sshift not in (9, 12) or mshift != 6:
        return {}
    ssz, msz = 1 << sshift, 1 << mshift
    nsec = len(data) // ssz + 1  # 1.0.109 (review): no table may name more sectors than the file has
    first_dir, cutoff, first_mini, n_mini, first_difat, n_difat = (struct.unpack_from("<I", data, o)[0] for o in (0x30, 0x38, 0x3C, 0x40, 0x44, 0x48))

    def sector(i: int) -> bytes:
        off = (i + 1) * ssz
        return data[off:off + ssz]

    difat = [x for x in struct.unpack_from("<109I", data, 0x4C) if x not in (END, FREE) and x < nsec]
    d, seen_d = first_difat, set()
    # 1.0.109 (review): a DIFAT chain that loops (or names sectors past the end) stops — a 1.5 KB file made 640 MB of tables
    while d not in (END, FREE) and d < nsec and d not in seen_d and len(seen_d) < nsec:
        seen_d.add(d)
        b = sector(d)
        if len(b) != ssz:
            break
        vals = struct.unpack(f"<{ssz // 4}I", b)
        difat += [x for x in vals[:-1] if x not in (END, FREE) and x < nsec]
        d = vals[-1]
    fat: list[int] = []
    for s in dict.fromkeys(difat):  # each FAT sector once
        if len(fat) >= nsec:
            break
        b = sector(s)
        if len(b) == ssz:
            fat += struct.unpack(f"<{ssz // 4}I", b)
    del fat[nsec:]
    budget = [MAX_BYTES]

    def read(start: int) -> bytes:  # 1.0.109 (review): what one file may make, in all
        got = b"".join(sector(s) for s in _chain(fat, start))
        budget[0] -= len(got)
        if budget[0] < 0:
            raise ValueError("too big")
        return got
    dirs = read(first_dir)
    ents = []
    for o in range(0, len(dirs) - 127, 128):
        e = dirs[o:o + 128]
        n = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(0, n - 2)].decode("utf-16-le", "replace") if 0 < n <= 64 else ""
        ents.append({"name": name, "type": e[66], "left": struct.unpack_from("<I", e, 68)[0], "right": struct.unpack_from("<I", e, 72)[0],
                     "child": struct.unpack_from("<I", e, 76)[0], "start": struct.unpack_from("<I", e, 116)[0], "size": struct.unpack_from("<I", e, 120)[0]})
    if not ents or ents[0]["type"] != 5:
        return {}
    root = ents[0]
    ministream = read(root["start"]) if root["size"] else b""
    minifat: list[int] = []
    if n_mini:
        mf = read(first_mini)
        minifat = list(struct.unpack(f"<{len(mf) // 4}I", mf[: len(mf) // 4 * 4]))

    def body(e: dict) -> bytes:
        if e["size"] < cutoff:
            got = b"".join(ministream[s * msz:(s + 1) * msz] for s in _chain(minifat, e["start"]))[: e["size"]]
            budget[0] -= len(got)
            if budget[0] < 0:
                raise ValueError("too big")
            return got
        return read(e["start"])[: e["size"]]

    out: dict[str, bytes] = {}
    todo, seen = [root["child"]], set()
    while todo:
        i = todo.pop()
        if i in (END, FREE) or i >= len(ents) or i in seen:
            continue
        seen.add(i)
        e = ents[i]
        todo += [e["left"], e["right"]]
        if e["type"] == 2 and e["name"].startswith("__substg1.0_") and e["size"] <= 4_000_000:
            out[e["name"][12:].upper()] = body(e)
    return out


def _s(st: dict, prop: str) -> str:
    for kind, enc in (("001F", "utf-16-le"), ("001E", "cp1252")):
        b = st.get(prop + kind)
        if b:
            return b.decode(enc, "replace").rstrip("\x00").strip()
    return ""


def read(data: bytes) -> dict:
    """{subject, from, to, cc, text} — or {} when it can't be read."""
    try:
        st = streams(data)
    except (struct.error, IndexError, ValueError):
        return {}
    if not st:
        return {}
    sender = _s(st, "0C1A")
    addr = _s(st, "5D01") or _s(st, "0C1F")
    text = _s(st, "1000")
    if not text and st.get("10130102"):
        h = st["10130102"].decode("utf-8", "replace")
        h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", h)
        text = re.sub(r"(?s)<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", h))
        text = re.sub(r"[ \t]+", " ", text)
    return {"subject": _s(st, "0037"), "from": (f"{sender} <{addr}>" if sender and addr and addr not in sender else sender or addr),
            "to": _s(st, "0E04"), "cc": _s(st, "0E03"), "date": "", "text": re.sub(r"\n{3,}", "\n\n", text.replace("\r", "")).strip()}
