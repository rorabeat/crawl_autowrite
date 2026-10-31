"""이미지 생성 개수 지정(image_gen_count) 및 글 작성 이후 실행 순서 완료 조건 검증."""

import config
import pipeline


def test_codex_image_prompts_require_file_copy_and_verified_save():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True)
    prompts = [
        pipeline._build_inline_image_instruction(context, "codex"),
        pipeline._build_image_generation_prompt(context, "본문", "제목.md"),
    ]
    for prompt in prompts:
        assert "Base64 이미지 데이터나 data URL을 exec_command/PowerShell 명령줄 인자에 넣지 마" in prompt
        assert "Join-Path -Path (Get-Location).Path -ChildPath" in prompt
        assert "-ErrorAction Stop" in prompt
        assert "크기가 0보다 큰지 확인" in prompt
        assert "과거 이미지로 대체하지 마" in prompt


def test_build_image_generation_prompt_defaults_to_one_image():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True)
    prompt = pipeline._build_image_generation_prompt(context, "", None)
    assert "정확히 1장만" in prompt


def test_build_image_generation_prompt_reflects_requested_count():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True, image_gen_count=3)
    prompt = pipeline._build_image_generation_prompt(context, "", None)
    assert "정확히 3장만" in prompt
    assert "\n" not in prompt


def test_build_image_generation_prompt_includes_insertion_instruction_when_md_given():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True)
    prompt = pipeline._build_image_generation_prompt(context, "본문 내용입니다", "제목.md")
    assert "제목.md" in prompt
    assert "삽입" in prompt
    assert "본문 내용입니다" in prompt


def test_build_image_generation_prompt_omits_insertion_instruction_when_no_md():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True)
    prompt = pipeline._build_image_generation_prompt(context, "", None)
    assert "삽입" not in prompt


def test_build_image_generation_prompt_bans_fallback_script():
    """과거 $imagegen 실패 시 codex가 대체로 파이썬 이미지 생성 스크립트를 직접 작성한
    사례가 있어(사용자 리포트), 실패해도 우회 프로그램을 만들지 말라고 명시한다."""
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True)
    prompt = pipeline._build_image_generation_prompt(context, "", None)
    assert "우회 프로그램" in prompt


def test_run_image_generation_skipped_when_toggle_off(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: called.append(1) or 0)

    context = pipeline.PipelineContext(keyword="키워드", generate_images=False, image_gen_count=3)
    status, paths = pipeline.run_image_generation(context, tmp_path, None)

    assert status == "skipped"
    assert paths == []
    assert called == []


def test_run_image_generation_caps_to_requested_count(tmp_path, monkeypatch):
    import config

    work_dir = tmp_path / "work"
    images_dir = config.images_dir(work_dir)
    images_dir.mkdir(parents=True)

    def fake_run(args, cwd=None, input_text=None, **kwargs):
        # codex가 요청보다 많은 5장을 images/에 만들어냈다고 가정한다.
        for i in range(5):
            (images_dir / f"generated_{i}.png").write_bytes(b"fake")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    context = pipeline.PipelineContext(keyword="키워드", generate_images=True, image_gen_count=2)
    status, paths = pipeline.run_image_generation(context, work_dir, None)

    assert status == "success"
    assert len(paths) == 2
    # 실제로 images/에 남은 파일도 채택된 2장뿐이어야 한다(초과분은 옮기지 않음, 원본이
    # 이미 images/ 안에 생성된 이번 테스트 케이스에서는 초과분도 그 자리에 그대로 남는다).
    assert all(p.parent == images_dir for p in paths)


def test_run_image_generation_uses_claude_backend_when_ai_model_is_claude(tmp_path, monkeypatch):
    captured = {}

    def fake_run(args, cwd=None, input_text=None, tee_path=None, **kwargs):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["tee_path"] = tee_path
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(
        keyword="키워드", generate_images=True, image_gen_count=1, ai_model="claude:sonnet"
    )
    pipeline.run_image_generation(context, work_dir, None)

    assert captured["args"] == config.build_claude_exec_args("sonnet")
    assert captured["cwd"] == work_dir
    assert captured["tee_path"] == config.output_dir(work_dir) / "image_gen_last_message.txt"


def test_run_image_generation_inserts_into_existing_md_and_normalizes_paths(tmp_path, monkeypatch):
    import config

    work_dir = tmp_path / "work"
    images_dir = config.images_dir(work_dir)
    images_dir.mkdir(parents=True)
    md_path = work_dir / "제목.md"
    md_path.write_text("# 제목\n\n본문입니다.\n", encoding="utf-8")

    captured_prompt = {}

    def fake_run(args, cwd=None, input_text=None, **kwargs):
        captured_prompt["text"] = input_text
        (images_dir / "generated_0.png").write_bytes(b"fake")
        # codex가 절대경로로 이미지 참조를 삽입했다고 가정(정규화 대상).
        md_path.write_text(
            f"# 제목\n\n본문입니다.\n\n![사진]({(images_dir / 'generated_0.png').resolve()})\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    context = pipeline.PipelineContext(keyword="키워드", generate_images=True, image_gen_count=1)
    status, paths = pipeline.run_image_generation(context, work_dir, md_path)

    assert status == "success"
    assert "제목.md" in captured_prompt["text"]
    assert "본문입니다" in captured_prompt["text"]
    text = md_path.read_text(encoding="utf-8")
    assert "images/generated_0.png" in text
    assert str(images_dir.resolve()) not in text


def test_image_save_prompt_handles_data_url_without_shell():
    prompt = pipeline._build_inline_image_instruction(
        pipeline.PipelineContext(keyword="test", generate_images=True), "codex"
    )
    assert "r.image_url" in prompt
    assert "tools.mcp__node_repl__js" in prompt
    assert "fs.writeFile(dest, bytes)" in prompt
    assert "image_generation_error.txt" in prompt


def test_remove_missing_image_links_keeps_existing_and_remote(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "ok.png").write_bytes(b"image")
    (images / "empty.png").touch()
    md = tmp_path / "article.md"
    md.write_text(
        "![ok](images/ok.png)\n![missing](images/missing.png)\n"
        "[missing ordinary link](images/empty.png)\n"
        "![remote](https://example.com/a.png)\n[shop](https://example.com)",
        encoding="utf-8",
    )
    assert pipeline._remove_missing_image_links(md) == ["images/missing.png", "images/empty.png"]
    text = md.read_text(encoding="utf-8")
    assert "![ok](images/ok.png)" in text
    assert "missing" not in text
    assert "https://example.com/a.png" in text
    assert "[shop]" in text


def test_generation_records_missing_image_reason_and_removes_link(tmp_path, monkeypatch):
    def fake_run(args, **kwargs):
        (tmp_path / "article.md").write_text(
            "# Article\n[image](images/missing.png)", encoding="utf-8"
        )
        assert kwargs["tee_path"].name == "generation_trace.log"
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)
    context = pipeline.PipelineContext(keyword="article", generate_images=True)
    status, md, images = pipeline.run_generation(context, tmp_path, [])
    assert status == "success"
    assert images == []
    assert "missing.png" not in md.read_text(encoding="utf-8")
    assert "rc=0" in (tmp_path / "output/image_generation_error.txt").read_text(encoding="utf-8")
