"""Task 010 완료 조건 검증: RunLogTab 실행 흐름, ResultTab 폴더/요약 표시."""

import json

import config
from app import InputTab, ResultTab, RunLogTab


def test_run_log_tab_runs_worker_and_updates_ui(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    def fake_run_pipeline(context, on_step=None, cancel_event=None):
        if on_step:
            on_step("crawl", "running")
            on_step("crawl", "skipped")
            on_step("generate", "running")
            on_step("generate", "success")
            on_step("publish", "running")
            on_step("publish", "success")
        return {"keyword": context.keyword, "steps": {}}

    import pipeline

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(pipeline, "run_prelogin", lambda *args, **kwargs: None)

    input_tab = InputTab()
    qtbot.addWidget(input_tab)
    input_tab.keyword_edit.setText("테스트 키워드")

    run_log_tab = RunLogTab(input_tab)
    qtbot.addWidget(run_log_tab)

    assert run_log_tab.run_button.isEnabled() is True

    run_log_tab._on_run_clicked()
    # 새 요청은 버튼을 막지 않고 바로 대기열에 들어가 실행된다(멀티 작업 지원).
    assert run_log_tab.run_button.isEnabled() is True
    assert "대기열에 추가됨" in run_log_tab.log_view.toPlainText()

    qtbot.waitUntil(lambda: "완료" in run_log_tab.log_view.toPlainText(), timeout=3000)

    assert "발행: success" in run_log_tab.log_view.toPlainText()


def test_run_log_tab_step_buttons_run_independently_and_show_prompt(qtbot, monkeypatch, tmp_path):
    """크롤링/AI 글작성/발행을 각각 단독 실행해도 같은 work_dir을 공유하고,
    AI 글작성 실행 시 codex exec에 전달되는 프롬프트가 prompt_view에 표시되는지 검증한다."""
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    import pipeline

    def fake_run_crawling(context, work_dir):
        config.blog_dir(work_dir).mkdir(parents=True, exist_ok=True)
        (config.blog_dir(work_dir) / "글1.txt").write_text("크롤링 결과", encoding="utf-8")
        return "success"

    def fake_run_generation(context, work_dir, blog_txt_paths):
        md_path = config.output_dir(work_dir) / "제목.md"
        md_path.write_text("# 제목\n\n본문", encoding="utf-8")
        return "success", md_path, []

    def fake_run_publish(md_path, work_dir, login_mode="auto", account_id=None, **kwargs):
        return "success", None

    monkeypatch.setattr(pipeline, "run_crawling", fake_run_crawling)
    monkeypatch.setattr(pipeline, "run_generation", fake_run_generation)
    monkeypatch.setattr(pipeline, "run_publish", fake_run_publish)
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    input_tab = InputTab()
    qtbot.addWidget(input_tab)
    input_tab.keyword_edit.setText("단계별 테스트")
    input_tab.use_crawling_checkbox.setChecked(False)

    run_log_tab = RunLogTab(input_tab)
    qtbot.addWidget(run_log_tab)

    run_log_tab._on_crawl_only_clicked()
    qtbot.waitUntil(lambda: run_log_tab.crawl_button.isEnabled() is True, timeout=3000)
    assert "크롤링: success" in run_log_tab.status_labels["crawl"].text()
    first_work_dir = run_log_tab.work_dir

    run_log_tab._on_generate_only_clicked()
    assert "키워드: 단계별 테스트" in run_log_tab.prompt_view.toPlainText()
    assert "지침 내용" in run_log_tab.prompt_view.toPlainText()
    qtbot.waitUntil(lambda: run_log_tab.generate_button.isEnabled() is True, timeout=3000)
    assert "AI 생성: success" in run_log_tab.status_labels["generate"].text()

    run_log_tab._on_publish_only_clicked()
    qtbot.waitUntil(lambda: run_log_tab.publish_button.isEnabled() is True, timeout=3000)
    assert "발행: success" in run_log_tab.status_labels["publish"].text()

    assert run_log_tab.work_dir == first_work_dir


def test_work_dir_choice_combo_lets_repeat_runs_reuse_same_folder(qtbot, monkeypatch, tmp_path):
    """같은 키워드로 '크롤링만 실행'을 반복할 때, 콤보박스에서 첫 실행이 만든 폴더를
    선택하면 두 번째 실행이 새 폴더(_2)를 만들지 않고 같은 work_dir을 재사용해야 한다."""
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    import pipeline

    def fake_run_crawling(context, work_dir):
        return "success"

    monkeypatch.setattr(pipeline, "run_crawling", fake_run_crawling)

    input_tab = InputTab()
    qtbot.addWidget(input_tab)
    input_tab.keyword_edit.setText("반복 테스트")
    input_tab.use_crawling_checkbox.setChecked(False)

    run_log_tab = RunLogTab(input_tab)
    qtbot.addWidget(run_log_tab)

    assert run_log_tab.work_dir_choice_combo.itemText(0) == "(새 폴더 생성)"
    assert run_log_tab.work_dir_choice_combo.count() == 1  # 아직 기존 폴더 없음

    run_log_tab._on_crawl_only_clicked()
    qtbot.waitUntil(lambda: run_log_tab.crawl_button.isEnabled() is True, timeout=3000)
    first_work_dir = run_log_tab.work_dir
    assert first_work_dir is not None

    # 방금 만든 폴더가 콤보박스 선택지로 나타나야 한다.
    assert run_log_tab.work_dir_choice_combo.count() == 2
    assert run_log_tab.work_dir_choice_combo.itemText(1) == first_work_dir.name

    # 다른 키워드/새 실행을 흉내내려면 상태를 초기화하고, 방금 폴더를 명시적으로 선택한다.
    run_log_tab._on_reset_clicked()
    index = run_log_tab.work_dir_choice_combo.findData(str(first_work_dir))
    assert index >= 0
    run_log_tab.work_dir_choice_combo.setCurrentIndex(index)

    run_log_tab._on_crawl_only_clicked()
    qtbot.waitUntil(lambda: run_log_tab.crawl_button.isEnabled() is True, timeout=3000)

    assert run_log_tab.work_dir == first_work_dir
    assert list(config.POST_RESULT_ROOT.iterdir()) == [first_work_dir]  # 새 폴더(_2)가 생기지 않았어야 함


def test_generate_only_copies_images_added_after_first_step_click(qtbot, monkeypatch, tmp_path):
    """work_dir이 이미 정해진 뒤(예: 크롤링 먼저 실행) 입력 탭에 이미지를 추가하고
    'AI 글작성만 실행'을 눌러도 images/ 폴더가 생성되고 이미지가 복사되는지 검증한다."""
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    import pipeline

    def fake_run_generation(context, work_dir, blog_txt_paths):
        md_path = config.output_dir(work_dir) / "제목.md"
        md_path.write_text("# 제목\n\n본문", encoding="utf-8")
        return "success", md_path, []

    monkeypatch.setattr(pipeline, "run_generation", fake_run_generation)
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    input_tab = InputTab()
    qtbot.addWidget(input_tab)
    input_tab.keyword_edit.setText("이미지 나중 추가")
    input_tab.use_crawling_checkbox.setChecked(False)

    run_log_tab = RunLogTab(input_tab)
    qtbot.addWidget(run_log_tab)

    # work_dir을 이미지 없이 먼저 확정한다 (예: 다른 단계를 먼저 눌렀던 상황을 재현).
    run_log_tab._ensure_context()
    work_dir = run_log_tab.work_dir
    assert work_dir is not None
    assert not list(config.images_dir(work_dir).glob("*")) if config.images_dir(work_dir).exists() else True

    # 이제서야 입력 탭에 이미지를 추가한다.
    image_file = tmp_path / "photo.jpg"
    image_file.write_bytes(b"fake")
    input_tab.image_drop_list.image_paths.append(str(image_file))

    run_log_tab._on_generate_only_clicked()
    qtbot.waitUntil(lambda: run_log_tab.generate_button.isEnabled() is True, timeout=3000)

    copied = list(config.images_dir(work_dir).glob("*.jpg"))
    assert len(copied) == 1
    assert copied[0].name == "photo.jpg"


def test_result_tab_lists_folders_and_shows_result_json(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    work_dir = tmp_path / "2026-07-17_제목"
    work_dir.mkdir()
    (work_dir / "result.json").write_text(
        json.dumps({"keyword": "제목", "steps": {"publish": {"status": "success"}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result_tab = ResultTab()
    qtbot.addWidget(result_tab)

    assert result_tab.folder_list.count() == 1
    assert result_tab.folder_list.item(0).text() == "2026-07-17_제목"

    result_tab.folder_list.setCurrentRow(0)

    assert "success" in result_tab.summary_view.toPlainText()
