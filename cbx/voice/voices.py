"""Her voices (1.0.68): the voices AXL can speak with, and which one she uses.

    quick   Kokoro on the CPU — instant. English voices from England and America, plus three made-up British
            women mixed from Kokoro's own (Charlotte, Harriet, Imogen): no real person's voice.
    studio  Chatterbox on the graphics card (cbx/voice/studio.py) — the fuller, more natural voice. It speaks
            in the sound of a short sample ("voice print"); every print here is made by Kokoro from its own or
            mixed voices, so these are made-up voices too, never a real person's.

The owner picks one for everyone (Agents → Now → Voice). Kept in DATA_DIR/voice/choice.json.
English only for the studio voices; Portuguese and French are spoken by Kokoro, Arabic by the browser.
"""
from __future__ import annotations

import json
from typing import Optional

from ..config import settings

# id → (name, engine, accent, gender, blurb, kokoro style: a voice name, or a mix {voice: weight})
CATALOG: dict[str, tuple] = {
    "studio:charlotte": ("Charlotte", "studio", "British", "woman", "Warm and clear — the fullest voice", {"bf_emma": 0.6, "bf_isabella": 0.4}),
    "studio:emma": ("Emma", "studio", "British", "woman", "Calm and measured", "bf_emma"),
    "studio:harriet": ("Harriet", "studio", "British", "woman", "Bright and friendly", {"bf_emma": 0.5, "bf_alice": 0.5}),
    "studio:imogen": ("Imogen", "studio", "British", "woman", "Soft and gentle", {"bf_isabella": 0.5, "bf_lily": 0.5}),
    "quick:charlotte": ("Charlotte", "quick", "British", "woman", "Warm and clear", {"bf_emma": 0.6, "bf_isabella": 0.4}),
    "quick:bf_emma": ("Emma", "quick", "British", "woman", "Calm and measured", "bf_emma"),
    "quick:harriet": ("Harriet", "quick", "British", "woman", "Bright and friendly", {"bf_emma": 0.5, "bf_alice": 0.5}),
    "quick:imogen": ("Imogen", "quick", "British", "woman", "Soft and gentle", {"bf_isabella": 0.5, "bf_lily": 0.5}),
    "quick:bf_isabella": ("Isabella", "quick", "British", "woman", "Lively", "bf_isabella"),
    "quick:bf_alice": ("Alice", "quick", "British", "woman", "Light", "bf_alice"),
    "quick:bf_lily": ("Lily", "quick", "British", "woman", "Soft", "bf_lily"),
    "quick:bm_george": ("George", "quick", "British", "man", "Deep and steady", "bm_george"),
    "quick:bm_fable": ("Fable", "quick", "British", "man", "Storyteller", "bm_fable"),
    "quick:bm_lewis": ("Lewis", "quick", "British", "man", "Plain and even", "bm_lewis"),
    "quick:bm_daniel": ("Daniel", "quick", "British", "man", "Quiet", "bm_daniel"),
    "quick:af_heart": ("Heart", "quick", "American", "woman", "Warm", "af_heart"),
    "quick:af_bella": ("Bella", "quick", "American", "woman", "Bright", "af_bella"),
    "quick:af_nicole": ("Nicole", "quick", "American", "woman", "Soft, close", "af_nicole"),
    "quick:am_michael": ("Michael", "quick", "American", "man", "Steady", "am_michael"),
}
DEFAULT = "quick:bf_emma"
SAMPLE = "Hello, I'm {ai}. I've looked through this morning's work — three things need you today. Shall I go through them now?"
# What each print says (about twelve seconds). Plain, everyday, varied so the studio voice learns the tune.
PRINT_TEXT = ("Good morning. I've been through today's requests, and three of them need your attention before lunch. "
              "The compliance register is up to date, and the Telkom run went out on time. "
              "Would you like me to go through them one by one, or shall I send you a short summary instead?")


def choice_file():
    return settings.path("voice") / "choice.json"


def chosen() -> str:
    try:
        v = json.loads(choice_file().read_text(encoding="utf-8")).get("voice")
        if v in CATALOG:
            return v
    except (OSError, ValueError):
        pass
    if settings.voice_tts_voice:
        k = f"quick:{settings.voice_tts_voice}"
        if k in CATALOG:
            return k
    return DEFAULT


def choose(vid: str) -> None:
    if vid not in CATALOG:
        raise ValueError("No such voice.")
    p = choice_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"voice": vid}), encoding="utf-8")


def kokoro_twin(vid: str) -> str:
    """The quick voice that sounds most like this one (when the studio is not running)."""
    name = CATALOG[vid][0]
    return next((k for k, v in CATALOG.items() if v[1] == "quick" and v[0] == name), DEFAULT)


def style(vid: str, kokoro) -> object:
    """Kokoro's style for a quick voice (or a print's): a voice name, or a mixed style array."""
    st = CATALOG[vid][5]
    if isinstance(st, str):
        return st
    import numpy as np

    mix = None
    for name, w in st.items():
        part = np.asarray(kokoro.get_voice_style(name), dtype="float32") * float(w)
        mix = part if mix is None else mix + part
    return mix


def out(vid: str, ready: Optional[bool] = None) -> dict:
    name, engine, accent, gender, blurb, _ = CATALOG[vid]
    return {"id": vid, "name": name, "engine": engine, "accent": accent, "gender": gender, "blurb": blurb, "ready": ready}
