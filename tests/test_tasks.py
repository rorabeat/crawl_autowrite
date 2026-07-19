"""Task 014 완료 조건 검증: TaskItem 영속화(tasks.json)와 TaskManager CRUD."""

import config
import pipeline
from pipeline import TaskItem


def test_save_and_load_tasks_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")

    t1 = TaskItem(
        task_id=pipeline.new_task_id(),
        label="제주 흑돼지",
        keyword="제주 흑돼지 맛집",
        comment="실내석",
        image_paths=["C:/imgs/a.jpg", "C:/imgs/한글.png"],
        use_crawling=True,
        generate_images=True,
        login_mode="manual",
    )
    t2 = TaskItem(task_id=pipeline.new_task_id(), label="속초 순대", keyword="속초 오징어순대", use_crawling=False)

    pipeline.save_tasks([t1, t2])
    loaded = pipeline.load_tasks()

    assert loaded == [t1, t2]


def test_load_tasks_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")
    assert pipeline.load_tasks() == []


def test_load_tasks_returns_empty_when_file_corrupted(tmp_path, monkeypatch):
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text("not json{{{", encoding="utf-8")
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tasks_path)

    assert pipeline.load_tasks() == []


def test_task_item_to_pipeline_context_maps_fields():
    task = TaskItem(
        task_id="abc",
        label="라벨",
        keyword="키워드",
        comment="코멘트",
        image_paths=["a.jpg"],
        use_crawling=False,
        generate_images=True,
        image_gen_count=4,
        agents_md_path="C:/agents/custom.md",
        login_mode="manual",
        ai_model="claude:sonnet",
    )
    ctx = task.to_pipeline_context()

    assert ctx.keyword == "키워드"
    assert ctx.comment == "코멘트"
    assert ctx.image_paths == ["a.jpg"]
    assert ctx.use_crawling is False
    assert ctx.generate_images is True
    assert ctx.image_gen_count == 4
    assert ctx.agents_md_path == "C:/agents/custom.md"
    assert ctx.login_mode == "manual"
    assert ctx.ai_model == "claude:sonnet"
    assert ctx.reuse_work_dir is None


def test_task_item_ai_model_defaults_to_config_default():
    task = TaskItem(task_id="x", label="라벨", keyword="키워드")
    assert task.ai_model == config.AI_MODEL_DEFAULT


def test_load_tasks_backfills_ai_model_for_legacy_json(tmp_path, monkeypatch):
    tasks_path = tmp_path / "tasks.json"
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tasks_path)

    legacy_task = {
        "task_id": "legacy-1",
        "label": "라벨",
        "keyword": "키워드",
        "comment": "",
        "image_paths": [],
        "use_crawling": True,
        "generate_images": False,
        "image_gen_count": 1,
        "agents_md_path": None,
        "login_mode": "auto",
        "created_at": "2026-01-01T00:00:00",
    }
    import json

    tasks_path.write_text(json.dumps([legacy_task]), encoding="utf-8")

    loaded = pipeline.load_tasks()

    assert loaded[0].ai_model == config.AI_MODEL_DEFAULT


def test_task_item_agents_md_path_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")

    task = TaskItem(task_id=pipeline.new_task_id(), label="라벨", keyword="키워드", agents_md_path="C:/agents/x.md")
    pipeline.save_tasks([task])
    loaded = pipeline.load_tasks()

    assert loaded[0].agents_md_path == "C:/agents/x.md"


def test_task_item_agents_md_path_defaults_to_none():
    task = TaskItem(task_id="x", label="라벨", keyword="키워드")
    assert task.agents_md_path is None


def test_task_item_image_gen_count_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")

    task = TaskItem(
        task_id=pipeline.new_task_id(), label="라벨", keyword="키워드", generate_images=True, image_gen_count=5
    )
    pipeline.save_tasks([task])
    loaded = pipeline.load_tasks()

    assert loaded[0].image_gen_count == 5


def test_task_edit_dialog_ai_model_combo_defaults_and_round_trips(qtbot):
    from app import TaskEditDialog

    dialog = TaskEditDialog()
    qtbot.addWidget(dialog)
    assert dialog.ai_model_combo.currentData() == "codex:gpt-5.6-sol"

    idx = dialog.ai_model_combo.findData("claude:sonnet")
    assert idx >= 0
    dialog.ai_model_combo.setCurrentIndex(idx)
    dialog.keyword_edit.setText("키워드")

    task = dialog.get_task_item()
    assert task.ai_model == "claude:sonnet"


def test_task_edit_dialog_loads_existing_task_ai_model(qtbot):
    from app import TaskEditDialog

    task = TaskItem(task_id="x", label="라벨", keyword="키워드", ai_model="codex:gpt-5.6-sol")
    dialog = TaskEditDialog(task=task)
    qtbot.addWidget(dialog)

    assert dialog.ai_model_combo.currentData() == "codex:gpt-5.6-sol"


def test_task_manager_crud_persists_and_emits_changed(tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")

    from app import TaskManager

    tm = TaskManager()
    changed_count = 0

    def on_changed():
        nonlocal changed_count
        changed_count += 1

    tm.changed.connect(on_changed)

    t1 = TaskItem(task_id=pipeline.new_task_id(), label="A", keyword="kwA")
    t2 = TaskItem(task_id=pipeline.new_task_id(), label="B", keyword="kwB")
    t3 = TaskItem(task_id=pipeline.new_task_id(), label="C", keyword="kwC")

    tm.add(t1)
    tm.add(t2)
    tm.add(t3)
    assert [t.label for t in tm.tasks] == ["A", "B", "C"]
    assert changed_count == 3
    assert [t.label for t in pipeline.load_tasks()] == ["A", "B", "C"]

    tm.move(t2.task_id, -1)
    assert [t.label for t in tm.tasks] == ["B", "A", "C"]

    tm.move(t2.task_id, -1)  # 이미 맨 위 -> 변화 없음
    assert [t.label for t in tm.tasks] == ["B", "A", "C"]

    tm.move(t3.task_id, 1)  # 이미 맨 아래 -> 변화 없음
    assert [t.label for t in tm.tasks] == ["B", "A", "C"]

    updated_t1 = TaskItem(task_id=t1.task_id, label="A-edited", keyword="kwA2")
    tm.update(t1.task_id, updated_t1)
    assert {t.task_id: t.label for t in tm.tasks}[t1.task_id] == "A-edited"

    tm.remove(t2.task_id)
    assert [t.label for t in tm.tasks] == ["A-edited", "C"]
    assert [t.label for t in pipeline.load_tasks()] == ["A-edited", "C"]
