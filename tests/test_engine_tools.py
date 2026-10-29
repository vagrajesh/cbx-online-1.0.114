"""Every tool folder that ships: sound, within its size, its words in four languages.

Two of these are warnings for now and hard failures later, on a switch: set CBX_STRICT_TOOLS=1 in the
environment of a release run and a folder over the token limit, or with an untranslated word, fails
the release. Until then the run prints what is missing so it can be fixed before it is enforced."""
import os
import warnings

from cbx.engine import loader

STRICT = bool(os.environ.get("CBX_STRICT_TOOLS"))


def test_every_shipped_tool_folder_is_sound():
    reg = loader.load_all()
    assert not reg.broken, {s: t.errors for s, t in reg.broken.items()}
    assert reg.tools, "no tool folders under tools/"
    for t in reg.tools.values():
        assert t.readme.strip(), f"{t.slug}: README.md is the tool's page in the owner's document — write it"
        assert (t.path / "tests").is_dir() and any((t.path / "tests").glob("test_*.py")), f"{t.slug}: no tests/test_*.py"


def test_tool_folders_fit_the_model_and_speak_four_languages():
    reg = loader.load_all()
    notes = []
    for t in reg.tools.values():
        if t.tokens > loader.TOKEN_LIMIT:
            notes.append(f"{t.slug}: about {t.tokens:,} tokens, limit {loader.TOKEN_LIMIT:,}")
        for lang, keys in t.missing().items():
            if keys:
                notes.append(f"{t.slug}: {lang} has no word for {', '.join(keys[:6])}{'…' if len(keys) > 6 else ''}")
    if STRICT:
        assert not notes, "\n".join(notes)
    for n in notes:
        warnings.warn(n, stacklevel=1)
