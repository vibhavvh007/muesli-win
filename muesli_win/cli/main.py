"""muesli-cli - JSON-first command line, for scripts and agents.

Mirrors the macOS `muesli-cli` surface so a script written against one works
against the other:

    muesli-cli transcribe <file> [--model M] [--format json|text|srt]
    muesli-cli dictations list [--limit N]
    muesli-cli dictations get <id>
    muesli-cli meetings list [--limit N]
    muesli-cli meetings get <id>
    muesli-cli meetings update-notes <id> --file notes.md
    muesli-cli meetings export <id> [--format markdown|pdf] [--content notes|transcript|both]
    muesli-cli models list
    muesli-cli models download <name>
    muesli-cli config get|set|path
    muesli-cli devices
    muesli-cli info
    muesli-cli spec
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import paths
from ..config import Config
from ..storage.db import Store


def _out(obj, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        print(obj)


def _fail(msg: str, code: int = 1) -> int:
    print(json.dumps({"error": msg}), file=sys.stderr)
    return code


# --- commands ---------------------------------------------------------------
def cmd_transcribe(args) -> int:
    src = Path(args.file)
    if not src.exists():
        return _fail(f"no such file: {src}")
    try:
        audio, rate = _read_audio(src)
    except Exception as exc:
        return _fail(f"could not read audio: {exc}")

    from ..audio.capture import _resample
    audio = _resample(audio, rate)

    cfg = Config().load()
    if args.model:
        cfg.set("stt_model", args.model, save=False)
    if args.backend:
        cfg.set("stt_backend", args.backend, save=False)

    from ..stt.registry import build
    try:
        t = build(cfg)
        t.load()
        result = t.transcribe(audio, language=args.language or "auto")
    except Exception as exc:
        return _fail(str(exc))

    if args.format == "text":
        print(result.text)
    elif args.format == "srt":
        print(_srt(result.segments))
    else:
        _out({"text": result.text, "language": result.language,
              "backend": result.backend, "model": result.model,
              "latency_ms": result.latency_ms,
              "segments": [{"start": s.start, "end": s.end, "text": s.text}
                           for s in result.segments]})
    return 0


def _read_audio(path: Path):
    """.wav natively; anything else needs soundfile or ffmpeg."""
    import numpy as np
    if path.suffix.lower() == ".wav":
        import wave
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            channels = w.getnchannels()
            width = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width)
        if dtype is None:
            raise ValueError(f"unsupported sample width {width}")
        data = np.frombuffer(frames, dtype=dtype).astype(np.float32)
        data /= float(np.iinfo(dtype).max)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return data, rate
    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            f"{path.suffix} needs soundfile - pip install soundfile (or convert to .wav)"
        ) from exc
    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    return data, rate


def _srt(segments) -> str:
    def ts(sec: float) -> str:
        h, rem = divmod(int(sec), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{int((sec % 1) * 1000):03d}"
    return "\n".join(
        f"{i}\n{ts(s.start)} --> {ts(s.end)}\n{s.text}\n"
        for i, s in enumerate(segments, 1)
    )


def cmd_dictations(args) -> int:
    store = Store()
    if args.sub == "list":
        _out(store.list_dictations(limit=args.limit))
    elif args.sub == "get":
        row = store.last_dictation() if args.id in ("last", "latest") \
            else store.get_dictation(args.id)
        if not row:
            return _fail("not found", 4)
        _out(row)
    return 0


def cmd_meetings(args) -> int:
    store = Store()
    if args.sub == "list":
        _out(store.list_meetings(limit=args.limit))
        return 0

    mid = args.id
    if mid in ("last", "latest"):
        last = store.last_meeting()
        mid = last["id"] if last else None
    if not mid:
        return _fail("not found", 4)
    meeting = store.get_meeting(mid)
    if not meeting:
        return _fail("not found", 4)

    if args.sub == "get":
        _out({**meeting, "segments": store.segments(mid)})
    elif args.sub == "update-notes":
        notes = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        store.set_meeting_notes(mid, notes, "cli", meeting.get("template_id", ""))
        _out({"ok": True, "id": mid, "bytes": len(notes)})
    elif args.sub == "export":
        from ..meetings import export as ex
        segs = store.segments(mid)
        try:
            path = (ex.write_pdf if args.format == "pdf" else ex.write_markdown)(
                meeting, segs, args.content)
        except Exception as exc:
            return _fail(str(exc))
        _out({"ok": True, "path": str(path)})
    elif args.sub == "summarise":
        from ..meetings import summarize
        try:
            notes, tpl = summarize.summarise(Config().load(), meeting.get("transcript", ""),
                                             args.template)
        except Exception as exc:
            return _fail(str(exc))
        store.set_meeting_notes(mid, notes, "cli", tpl)
        _out({"ok": True, "id": mid, "template": tpl, "notes": notes})
    return 0


def cmd_models(args) -> int:
    from ..stt import download as dl
    if args.sub == "list":
        _out(dl.status())
        return 0
    if args.sub == "path":
        print(paths.models_dir())
        return 0
    # download
    cfg = Config().load()
    try:
        print(f"downloading {args.name} - this can take several minutes",
              file=sys.stderr)
        info = dl.download(args.name,
                           device=cfg.get("win_compute_device", "auto"),
                           compute=cfg.get("win_compute_type", "auto"))
    except Exception as exc:
        return _fail(str(exc), 3)
    if args.use:
        cfg.set("stt_backend", info["backend"])
        cfg.set("stt_model", info["name"])
        info["now_active"] = True
    _out({"ok": True, **info})
    return 0


def cmd_config(args) -> int:
    cfg = Config().load()
    if args.sub == "path":
        print(cfg.path)
    elif args.sub == "get":
        _out(cfg.redacted() if not args.key else {args.key: cfg.get(args.key)})
    elif args.sub == "set":
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value
        cfg.set(args.key, value)
        _out({"ok": True, args.key: cfg.get(args.key)})
    return 0


def cmd_devices(_args) -> int:
    from ..audio.capture import list_input_devices, list_loopback_devices
    _out({
        "inputs": [d.__dict__ for d in list_input_devices()],
        "loopback": [d.__dict__ for d in list_loopback_devices()],
    })
    return 0


def cmd_info(_args) -> int:
    from .. import __version__
    from ..stt.compute import resolve
    cfg = Config().load()
    device, compute = resolve(cfg.get("win_compute_device", "auto"),
                              cfg.get("win_compute_type", "auto"))
    _out({
        "version": __version__,
        "platform": sys.platform,
        "frozen": paths.is_frozen(),
        "config": str(paths.config_path()),
        "database": str(paths.db_path()),
        "models": str(paths.models_dir()),
        "logs": str(paths.logs_dir()),
        "stt_backend": cfg.get("stt_backend"),
        "stt_model": cfg.get("stt_model"),
        "compute_device": device,
        "compute_type": compute,
        "hotkey": cfg.get("dictation_hotkey", {}).get("label"),
    })
    return 0


def cmd_spec(_args) -> int:
    _out({
        "name": "muesli-cli",
        "commands": {
            "transcribe": {"args": ["file"],
                           "options": ["--model", "--backend", "--language", "--format"]},
            "dictations": {"sub": ["list", "get"]},
            "meetings": {"sub": ["list", "get", "update-notes", "export", "summarise"]},
            "models": {"sub": ["list", "download", "path"]},
            "config": {"sub": ["get", "set", "path"]},
            "devices": {}, "info": {}, "spec": {},
        },
        "output": "json",
    })
    return 0


# --- parser -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="muesli-cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="transcribe an audio file")
    t.add_argument("file")
    t.add_argument("--model")
    t.add_argument("--backend", choices=["whisper", "parakeet", "openai", "openrouter"])
    t.add_argument("--language")
    t.add_argument("--format", choices=["json", "text", "srt"], default="json")
    t.set_defaults(func=cmd_transcribe)

    d = sub.add_parser("dictations")
    ds = d.add_subparsers(dest="sub", required=True)
    dl = ds.add_parser("list")
    dl.add_argument("--limit", type=int, default=20)
    dg = ds.add_parser("get")
    dg.add_argument("id")
    d.set_defaults(func=cmd_dictations)

    m = sub.add_parser("meetings")
    ms = m.add_subparsers(dest="sub", required=True)
    ml = ms.add_parser("list")
    ml.add_argument("--limit", type=int, default=20)
    mg = ms.add_parser("get")
    mg.add_argument("id")
    mu = ms.add_parser("update-notes")
    mu.add_argument("id")
    mu.add_argument("--file")
    me = ms.add_parser("export")
    me.add_argument("id")
    me.add_argument("--format", choices=["markdown", "pdf"], default="markdown")
    me.add_argument("--content", choices=["notes", "transcript", "both"], default="notes")
    msum = ms.add_parser("summarise")
    msum.add_argument("id")
    msum.add_argument("--template", default="auto")
    m.set_defaults(func=cmd_meetings)

    mo = sub.add_parser("models", help="list or pre-download speech models")
    mos = mo.add_subparsers(dest="sub", required=True)
    mos.add_parser("list")
    mos.add_parser("path")
    mod = mos.add_parser("download")
    mod.add_argument("name")
    mod.add_argument("--use", action="store_true",
                     help="also make this the active dictation model")
    mo.set_defaults(func=cmd_models)

    c = sub.add_parser("config")
    cs = c.add_subparsers(dest="sub", required=True)
    cg = cs.add_parser("get")
    cg.add_argument("key", nargs="?")
    cset = cs.add_parser("set")
    cset.add_argument("key")
    cset.add_argument("value")
    cs.add_parser("path")
    c.set_defaults(func=cmd_config)

    sub.add_parser("devices").set_defaults(func=cmd_devices)
    sub.add_parser("info").set_defaults(func=cmd_info)
    sub.add_parser("spec").set_defaults(func=cmd_spec)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:                 # a CLI must not spew a traceback at a script
        return _fail(str(exc), 2)


if __name__ == "__main__":
    sys.exit(main())
