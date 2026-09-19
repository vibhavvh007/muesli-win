"""A config.json copied off a Mac has to work here without hand-editing."""
import json

from muesli_win.config import VK, Config, map_backend


def write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return Config(p).load()


def test_right_option_becomes_right_alt(tmp_path):
    cfg = write(tmp_path, {"dictation_hotkey": {"keyCode": 61, "label": "Right Option"}})
    hk = cfg.get("dictation_hotkey")
    assert hk["vk"] == VK["Right Alt"]
    assert hk["label"] == "Right Alt"


def test_right_cmd_becomes_right_win(tmp_path):
    cfg = write(tmp_path, {"computer_use_hotkey": {"keyCode": 54, "label": "Right Cmd"},
                           "quill_hotkey": {"keyCode": 54, "label": "Right Cmd"}})
    assert cfg.get("quill_hotkey")["vk"] == VK["Right Win"]


def test_whisperkit_model_id_maps_to_a_loadable_model(tmp_path):
    cfg = write(tmp_path, {"stt_backend": "whisper",
                           "stt_model": "large-v3-v20240930_626MB"})
    assert cfg.get("stt_model") == "large-v3"


def test_coreml_parakeet_id_is_stripped(tmp_path):
    cfg = write(tmp_path, {"whisper_model": "FluidInference/parakeet-tdt-0.6b-v3-coreml"})
    assert cfg.get("whisper_model") == "parakeet-tdt-0.6b-v3"


def test_mac_only_engine_maps_to_whisper_large_not_a_tiny_fallback(tmp_path):
    """The failure this guards: a Bodhan/Cohere config silently dropping to `small`."""
    cfg = write(tmp_path, {"stt_backend": "bodhan",
                           "stt_model": "phequals/indic-transcribe-flex-coreml-int8"})
    assert cfg.get("stt_backend") == "whisper"
    assert cfg.get("stt_model") == "large-v3"
    assert cfg.imported_from_mac is True


def test_meeting_backend_is_migrated_too(tmp_path):
    cfg = write(tmp_path, {"meeting_transcription_backend": "cohere",
                           "meeting_transcription_model": "cohere-transcribe-coreml"})
    assert cfg.get("meeting_transcription_backend") == "whisper"
    assert cfg.get("meeting_transcription_model") == "large-v3"


def test_windows_backend_is_left_alone(tmp_path):
    cfg = write(tmp_path, {"stt_backend": "parakeet", "stt_model": "parakeet-tdt-0.6b-v2"})
    assert cfg.get("stt_backend") == "parakeet"
    assert cfg.get("stt_model") == "parakeet-tdt-0.6b-v2"
    assert cfg.imported_from_mac is False


def test_map_backend_is_explicit_about_remapping():
    assert map_backend("whisper", "large-v3") == ("whisper", "large-v3", False)
    assert map_backend("apple_speech", "x")[2] is True
    assert map_backend("something-new", "x") == ("whisper", "large-v3", True)


def test_merge_never_blanks_local_api_keys(tmp_path):
    cfg = write(tmp_path, {"openai_api_key": "sk-local-secret"})
    incoming = tmp_path / "mac.json"
    incoming.write_text(json.dumps({"openai_api_key": "", "openrouter_api_key": "",
                                    "dark_mode": True}), encoding="utf-8")
    cfg.merge_file(incoming)
    assert cfg.get("openai_api_key") == "sk-local-secret"
    assert cfg.get("dark_mode") is True


def test_custom_words_survive_the_trip(tmp_path):
    words = [{"word": "ultrahuman", "replacement": "Ultrahuman", "matching_threshold": 0.85}]
    cfg = write(tmp_path, {"custom_words": words})
    assert cfg.get("custom_words") == words


def test_corrupt_config_is_quarantined_not_fatal(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ this is not json", encoding="utf-8")
    cfg = Config(p).load()
    assert cfg.get("stt_backend") == "whisper"          # defaults applied
    assert (tmp_path / "config.json.corrupt").exists()


def test_unknown_keys_are_preserved_for_round_tripping(tmp_path):
    cfg = write(tmp_path, {"some_future_mac_key": 42})
    cfg.save()
    assert json.loads((tmp_path / "config.json").read_text())["some_future_mac_key"] == 42


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    cfg = write(tmp_path, {})
    cfg.set("dark_mode", True)
    assert not (tmp_path / "config.json.tmp").exists()
    assert json.loads((tmp_path / "config.json").read_text())["dark_mode"] is True


def test_redacted_hides_keys(tmp_path):
    cfg = write(tmp_path, {"openai_api_key": "sk-abc"})
    assert cfg.redacted()["openai_api_key"] == "***"
    assert cfg.get("openai_api_key") == "sk-abc"
