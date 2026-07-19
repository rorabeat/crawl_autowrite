"""입력 탭 기본값(input_defaults.json) 영속화 및 InputTab 연동 완료 조건 검증."""

import config
import pipeline
from pipeline import InputDefaults


def test_save_and_load_input_defaults_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", tmp_path / "input_defaults.json")

    defaults = InputDefaults(
        generate_images=True, image_gen_count=4, agents_md_path="C:/agents/custom.md", ai_model="claude:sonnet"
    )
    pipeline.save_input_defaults(defaults)

    assert pipeline.load_input_defaults() == defaults


def test_load_input_defaults_backfills_ai_model_for_legacy_json(tmp_path, monkeypatch):
    defaults_path = tmp_path / "input_defaults.json"
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", defaults_path)

    import json

    legacy = {"generate_images": True, "image_gen_count": 2, "agents_md_path": None}
    defaults_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = pipeline.load_input_defaults()

    assert loaded.ai_model == config.AI_MODEL_DEFAULT


def test_load_input_defaults_returns_default_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", tmp_path / "input_defaults.json")
    assert pipeline.load_input_defaults() == InputDefaults()


def test_load_input_defaults_returns_default_when_file_corrupted(tmp_path, monkeypatch):
    defaults_path = tmp_path / "input_defaults.json"
    defaults_path.write_text("not json{{{", encoding="utf-8")
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", defaults_path)

    assert pipeline.load_input_defaults() == InputDefaults()


def test_input_tab_loads_saved_defaults_on_init(tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", tmp_path / "input_defaults.json")
    pipeline.save_input_defaults(
        InputDefaults(generate_images=True, image_gen_count=6, agents_md_path="C:/agents/custom.md")
    )

    from app import InputTab

    tab = InputTab()
    qtbot.addWidget(tab)

    assert tab.generate_images_checkbox.isChecked() is True
    assert tab.image_gen_count_spin.value() == 6
    assert tab.image_gen_count_spin.isEnabled() is True
    assert tab.agents_md_path == "C:/agents/custom.md"


def test_input_tab_changes_persist_immediately(tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", tmp_path / "input_defaults.json")

    from app import InputTab

    tab = InputTab()
    qtbot.addWidget(tab)

    tab.generate_images_checkbox.setChecked(True)
    tab.image_gen_count_spin.setValue(3)

    saved = pipeline.load_input_defaults()
    assert saved.generate_images is True
    assert saved.image_gen_count == 3
