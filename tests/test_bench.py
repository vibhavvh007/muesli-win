"""Capability assessment.

The claim this feature makes has to be defensible, so the arithmetic behind it
is tested directly. The pivotal number is the real-time factor: below 1.0 the
meeting queue drains and a recording of any length is safe; at or above 1.0
chunks arrive faster than they are consumed and the backlog - which is an
unbounded list in MeetingRecorder - grows for as long as you keep recording.
"""
import numpy as np

from muesli_win.bench import (
    MODEL_RAM_MB,
    RATE,
    BenchResult,
    Specs,
    _sample_audio,
    interpret,
    probe_specs,
    recommend,
)


def result(rtf, model="m", free_gb=16.0, **kw):
    return BenchResult(model=model, rtf=rtf,
                       specs=Specs(ram_free_gb=free_gb, ram_gb=16.0), **kw)


# --- the machine -------------------------------------------------------------
def test_specs_probe_returns_something_usable():
    s = probe_specs()
    assert s.cores >= 1
    assert s.ram_gb > 0
    assert isinstance(s.summary(), str) and s.summary()


def test_sample_audio_is_the_right_length_and_not_silent():
    """Silence would let the model short-circuit and report a speed it could
    never sustain on real speech."""
    a = _sample_audio(30.0)
    assert len(a) == int(RATE * 30)
    assert a.dtype == np.float32
    assert 0.1 < float(np.abs(a).max()) <= 1.0
    # energy must be spread through the clip, not a single burst
    halves = [float(np.abs(a[: len(a) // 2]).mean()),
              float(np.abs(a[len(a) // 2:]).mean())]
    assert min(halves) > 0.5 * max(halves)


# --- verdict thresholds ------------------------------------------------------
def test_verdict_scales_with_speed():
    assert interpret(result(0.05))["verdict"] == "excellent"
    assert interpret(result(0.3))["verdict"] == "good"
    assert interpret(result(0.6))["verdict"] == "ok"
    assert interpret(result(0.9))["verdict"] == "slow"
    assert interpret(result(1.6))["verdict"] == "unusable"


def test_dictation_wait_is_rtf_times_duration():
    """The number a user actually feels: how long after they stop talking."""
    assert "9 second" in interpret(result(0.3))["dictation"]     # 0.3 x 30s
    assert "15 second" in interpret(result(0.5))["dictation"]


def test_comfortably_below_realtime_is_safe_for_any_meeting_length():
    text = interpret(result(0.4))["meetings"]
    assert "any length" in text
    assert "60%" in text            # headroom = 1 - rtf


def test_thin_headroom_is_not_promised_as_safe():
    """A real measurement of 0.921 exposed this: 8% headroom was being reported
    as 'a meeting of any length is fine', which one busy moment disproves."""
    text = interpret(result(0.92))["meetings"]
    assert "any length" not in text
    assert "only just keeps up" in text
    assert "smaller model" in text


def test_at_or_above_realtime_predicts_a_growing_backlog():
    """MeetingRecorder._pending is unbounded, so this is a real failure mode."""
    text = interpret(result(1.5))["meetings"]
    assert "cannot keep up" in text
    assert "30 minutes for every hour" in text     # (1.5 - 1) x 60
    assert "keeps growing" in text


def test_exactly_realtime_counts_as_unusable():
    assert interpret(result(1.0))["verdict"] == "unusable"


# --- warnings ----------------------------------------------------------------
def test_battery_is_called_out():
    r = result(0.2)
    r.specs.on_battery = True
    assert any("battery" in w.lower() for w in interpret(r)["warnings"])


def test_insufficient_free_memory_is_called_out():
    r = result(0.2, free_gb=1.0)
    r.ram_needed_mb = 3400            # large-v3
    warn = " ".join(interpret(r)["warnings"])
    assert "3.3 GB" in warn and "1.0 GB" in warn


def test_no_memory_warning_when_there_is_headroom():
    r = result(0.2, free_gb=16.0)
    r.ram_needed_mb = 1400
    assert not any("close some apps" in w for w in interpret(r)["warnings"])


def test_slow_load_is_mentioned_once():
    r = result(0.2)
    r.load_seconds = 45.0
    assert any("45s to load" in w for w in interpret(r)["warnings"])


# --- failures and recommendation ---------------------------------------------
def test_a_failed_measurement_says_so_rather_than_inventing_a_verdict():
    r = BenchResult(model="m", error="out of memory")
    i = interpret(r)
    assert i["verdict"] == "error"
    assert "out of memory" in i["detail"]
    assert not r.ok


def test_recommend_picks_the_most_accurate_model_that_still_keeps_up():
    """Slowest usable = largest model that is still comfortable."""
    rec = recommend([result(0.05, "tiny"), result(0.45, "large-v3-turbo"),
                     result(1.4, "large-v3")])
    assert "large-v3-turbo" in rec
    assert "tiny" not in rec


def test_recommend_is_honest_when_nothing_is_comfortable():
    rec = recommend([result(0.8, "tiny"), result(2.0, "large-v3")])
    assert "Nothing tested is comfortable" in rec
    assert "tiny" in rec            # still names the fastest


def test_recommend_handles_everything_failing():
    assert "No model could be measured" in recommend(
        [BenchResult(model="a", error="x"), BenchResult(model="b", error="y")])


def test_every_catalogued_model_has_a_memory_figure():
    from muesli_win.stt import parakeet, whisper_fw
    for name in list(whisper_fw.MODELS) + list(parakeet.MODELS):
        assert name in MODEL_RAM_MB, f"{name} has no RAM estimate"


# --- what the real measurement run exposed -----------------------------------
def test_speed_is_not_used_as_a_proxy_for_accuracy():
    """A real run measured tiny at 9x and parakeet at 31x realtime, and the old
    'slowest usable' heuristic recommended tiny - the least accurate model in
    the set. Quality has to be ranked explicitly."""
    rec = recommend([result(0.108, "tiny"), result(0.921, "small"),
                     result(0.032, "parakeet-tdt-0.6b-v3")])
    assert "parakeet-tdt-0.6b-v3" in rec
    assert "tiny" not in rec


def test_english_first_models_are_flagged_as_such():
    rec = recommend([result(0.03, "parakeet-tdt-0.6b-v3")])
    assert "English-first" in rec


def test_english_only_models_are_excluded_for_a_non_english_user():
    """Not a slower choice - a wrong one. It would return nonsense.

    Asserts what is RECOMMENDED, not merely what is mentioned: the caveat text
    legitimately lists every model that was tested.
    """
    rec = recommend([result(0.03, "parakeet-tdt-0.6b-v3"), result(0.1, "tiny")],
                    language="hi")
    assert rec.startswith("Use tiny:")


def test_non_english_user_with_only_an_english_model_is_warned_not_silently_served():
    rec = recommend([result(0.03, "parakeet-tdt-0.6b-v3")], language="hi")
    assert "parakeet-tdt-0.6b-v3" in rec
    assert "accuracy will be poor" in rec


def test_quality_ranking_prefers_the_better_model_at_equal_comfort():
    rec = recommend([result(0.2, "small"), result(0.4, "large-v3-turbo")])
    assert "large-v3-turbo" in rec


def test_every_catalogued_model_has_a_quality_rank():
    from muesli_win.bench import MODEL_QUALITY
    from muesli_win.stt import parakeet, whisper_fw
    for name in list(whisper_fw.MODELS) + list(parakeet.MODELS):
        assert name in MODEL_QUALITY, f"{name} has no quality rank"


def test_models_dir_can_be_relocated(tmp_path, monkeypatch):
    """The models run to gigabytes; a small system drive should not be the only
    option."""
    from muesli_win import paths
    monkeypatch.setenv("MUESLI_MODELS_DIR", str(tmp_path / "elsewhere"))
    assert paths.models_dir() == tmp_path / "elsewhere"
    monkeypatch.delenv("MUESLI_MODELS_DIR")
    assert paths.models_dir() != tmp_path / "elsewhere"


def test_a_weak_winner_is_flagged_rather_than_quietly_endorsed():
    """tiny won a real run only because it was the sole multilingual option
    measured. 'Best of what was tested' is not the same as 'good'."""
    rec = recommend([result(0.126, "tiny"), result(0.029, "parakeet-tdt-0.6b-v3")],
                    language="hi")
    assert "tiny" in rec
    assert "low-accuracy" in rec
    assert "large-v3-turbo" in rec        # tells them what to measure next


def test_a_strong_winner_gets_no_such_caveat():
    rec = recommend([result(0.3, "large-v3-turbo")])
    assert "low-accuracy" not in rec
