"""Task 007 완료 조건 검증: safe_title, normalize_image_paths_in_md, run_generation."""

from pathlib import Path

import config
import pipeline


def test_safe_title_removes_reserved_characters():
    assert config.safe_title('오키나와: "여행" <2박3일> *특가*') == "오키나와 여행 2박3일 특가"


def test_safe_title_empty_falls_back_to_default():
    assert config.safe_title("   ") == "제목없음"


def test_normalize_image_paths_in_md_keeps_work_dir_relative_paths(tmp_path):
    """md는 사람이 직접 열어볼 수 있어야 하므로 이미지 경로는 절대경로가 아니라
    work_dir(base_dir) 기준 "images/파일명" 같은 짧은 상대경로로 남아야 한다(사용자 요청).
    NaverAutoWrite 쪽은 md 파일 자신의 위치를 기준으로 이 상대경로를 해석한다."""
    md_path = tmp_path / "제목.md"
    md_path.write_text(
        "# 제목\n\n![사진1](images/1.jpg)\n![원격 이미지](https://example.com/2.jpg)\n",
        encoding="utf-8",
    )

    config.normalize_image_paths_in_md(md_path, tmp_path)

    content = md_path.read_text(encoding="utf-8")
    assert "![사진1](images/1.jpg)" in content
    assert "![원격 이미지](https://example.com/2.jpg)" in content


def test_normalize_image_paths_in_md_rewrites_absolute_path_to_relative(tmp_path):
    """codex가 절대경로나 다른 형태로 이미지 경로를 써도 work_dir 기준 상대경로로 통일한다."""
    md_path = tmp_path / "제목.md"
    abs_image = (tmp_path / "images" / "1.jpg").resolve().as_posix()
    md_path.write_text(f"# 제목\n\n![사진1]({abs_image})\n", encoding="utf-8")

    config.normalize_image_paths_in_md(md_path, tmp_path)

    content = md_path.read_text(encoding="utf-8")
    assert "![사진1](images/1.jpg)" in content


def test_normalize_image_paths_in_md_uses_forward_slashes_not_backslashes(tmp_path):
    """백슬래시 경로를 마크다운 이미지 문법에 그대로 쓰면 mistune 등 마크다운 파서가
    URL 이스케이프로 처리해(`\\N` -> `%5CN`) 나중에 편집기 자동화가 이미지를 실제
    파일과 매칭하지 못하는 버그가 있었다(실측 확인). as_posix()로 슬래시만 쓰는지 검증한다."""
    md_path = tmp_path / "output" / "제목.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("# 제목\n\n![사진1](images/1.jpg)\n", encoding="utf-8")

    config.normalize_image_paths_in_md(md_path, tmp_path)

    content = md_path.read_text(encoding="utf-8")
    assert "\\" not in content


def test_build_generation_prompt_truncates_over_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROMPT_MAX_CHARS", 50)
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    blog_file = tmp_path / "blog.txt"
    blog_file.write_text("가" * 200, encoding="utf-8")

    context = pipeline.PipelineContext(keyword="키워드")
    prompt = pipeline._build_generation_prompt(context, [blog_file], "codex")

    blog_section = prompt.split("참고 크롤링 결과:\n")[1]
    assert len(blog_section) == 50


def test_build_generation_prompt_includes_web_search_instruction(monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(keyword="반도체주가")
    prompt = pipeline._build_generation_prompt(context, [], "codex")

    assert "웹 검색" in prompt
    assert "최신 정보" in prompt


def test_build_generation_prompt_forbids_clarifying_questions(monkeypatch):
    """AGENTS.md 페르소나(여행)와 주제(주식 등)가 안 맞을 때 AI가 글을 쓰지 않고 되묻기만
    해서 제목 없는 md가 생성되던 실사례가 있었다 — 무인 실행이라 질문에 답할 수 없으니
    항상 완성된 글을 바로 쓰라고 명시해야 한다."""
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(keyword="삼성전자 주가 하락 이유")
    prompt = pipeline._build_generation_prompt(context, [], "codex")

    assert "확인 질문" in prompt
    assert "무인" in prompt


def test_build_generation_prompt_includes_image_instruction_when_generate_images_on(monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(keyword="키워드", generate_images=True, image_gen_count=2)
    prompt = pipeline._build_generation_prompt(context, [], "codex")

    assert "$imagegen" in prompt
    assert "정확히 2장" in prompt
    assert "images" in prompt
    assert "우회 프로그램" in prompt


def test_build_generation_prompt_omits_image_instruction_when_generate_images_off(monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(keyword="키워드", generate_images=False)
    prompt = pipeline._build_generation_prompt(context, [], "codex")

    assert "$imagegen" not in prompt


def test_build_generation_prompt_uses_placeholder_instruction_for_claude_backend(monkeypatch):
    """claude 백엔드는 $imagegen 도구가 없어 자리표시자+URL 매핑 지시를 대신 써야 한다
    (사용자 리포트: 클로드 하이쿠 사용 시 이미지가 안 나옴)."""
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(keyword="키워드", generate_images=True, image_gen_count=2)
    prompt = pipeline._build_generation_prompt(context, [], "claude")

    assert "$imagegen" not in prompt
    assert "[IMAGE 1]" in prompt
    assert "정확히 2개" in prompt


def test_build_generation_prompt_includes_attached_image_instruction(monkeypatch):
    """사용자가 이미지를 첨부하면(generate_images 여부와 무관하게) 모두 본문에 포함시키라는
    지시가 프롬프트에 들어가야 한다(사용자 요청)."""
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(
        keyword="키워드", generate_images=False, image_paths=["/tmp/a.jpg", "/tmp/b.png"]
    )
    prompt = pipeline._build_generation_prompt(context, [], "codex")

    assert "a.jpg" in prompt
    assert "b.png" in prompt
    assert "모두" in prompt or "전부" in prompt


def test_build_generation_prompt_omits_attached_image_instruction_when_no_images(monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    context = pipeline.PipelineContext(keyword="키워드", image_paths=[])
    prompt = pipeline._build_generation_prompt(context, [], "codex")

    assert "첨부 이미지 지시" not in prompt


def test_ensure_attached_images_included_appends_only_missing_ones(tmp_path):
    md_path = tmp_path / "글.md"
    md_path.write_text("본문 중간에 ![사진](images/a.jpg) 이미 있음", encoding="utf-8")

    added = pipeline._ensure_attached_images_included(md_path, ["/원본/a.jpg", "/원본/b.png"])

    assert added == 1
    text = md_path.read_text(encoding="utf-8")
    assert text.count("a.jpg") == 1  # 이미 있던 것은 중복 삽입되지 않음
    assert "images/b.png" in text


def test_ensure_attached_images_included_noop_when_all_already_present(tmp_path):
    md_path = tmp_path / "글.md"
    md_path.write_text("![사진](images/a.jpg)", encoding="utf-8")

    added = pipeline._ensure_attached_images_included(md_path, ["/원본/a.jpg"])

    assert added == 0


def test_run_generation_uses_attached_image_filename_as_is_for_claude_backend(tmp_path, monkeypatch):
    """claude 백엔드는 이미지를 시각적으로 첨부하지 못하지만(_build_claude_exec_args에
    -i 옵션이 없음), 응답에 참조가 없어도 코드 레벨 보완으로 첨부 이미지가 모두 포함돼야
    한다(사용자 요청 — claude 하이쿠로 첨부 이미지가 통째로 무시되던 문제 해결)."""
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    image_file = tmp_path / "photo.jpg"
    image_file.write_bytes(b"fake")

    def fake_run(args, cwd=None, input_text=None, tee_path=None, **kwargs):
        tee_path.parent.mkdir(parents=True, exist_ok=True)
        tee_path.write_text("AI가 쓴 본문(이미지 언급 없음)", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(
        keyword="키워드", use_crawling=False, image_paths=[str(image_file)], ai_model="claude:sonnet"
    )

    status, md_path, _ = pipeline.run_generation(context, work_dir, [])

    assert status == "success"
    assert "images/photo.jpg" in md_path.read_text(encoding="utf-8")


def test_extract_and_apply_claude_images_replaces_placeholders(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    md_path = work_dir / "제목.md"
    md_path.write_text(
        "# 제목\n\n본문 [IMAGE 1] 이어지는 문장.\n\n[IMAGE 1] https://example.com/photo.jpg | 풍경 사진\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline.image_fetch,
        "download_and_crop_square",
        lambda url, dest: (dest.parent.mkdir(parents=True, exist_ok=True), dest.write_bytes(b"jpg"), True)[-1],
    )

    result_paths = pipeline._extract_and_apply_claude_images(work_dir, md_path, 1)

    content = md_path.read_text(encoding="utf-8")
    assert "[IMAGE 1] https://example.com/photo.jpg" not in content
    assert "![풍경 사진](images/claude_1.jpg)" in content
    assert len(result_paths) == 1
    assert result_paths[0].name == "claude_1.jpg"


def test_extract_and_apply_claude_images_removes_placeholder_on_download_failure(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    md_path = work_dir / "제목.md"
    md_path.write_text(
        "# 제목\n\n본문 [IMAGE 1] 이어지는 문장.\n\n[IMAGE 1] https://example.com/broken.jpg | 실패\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pipeline.image_fetch, "download_and_crop_square", lambda url, dest: False)

    result_paths = pipeline._extract_and_apply_claude_images(work_dir, md_path, 1)

    content = md_path.read_text(encoding="utf-8")
    assert "[IMAGE 1]" not in content
    assert result_paths == []


def test_extract_and_apply_claude_images_downloads_stray_remote_links_too(tmp_path, monkeypatch):
    """claude가 [IMAGE n] 자리표시자 지시를 어기고 원격 URL을 직접 마크다운 이미지로
    써버려도(사용자 리포트: "이미지는 링크만 가져오는 게 아니라 다운로드해서 크롭해야"),
    코드가 그 링크까지 찾아 다운로드+크롭해서 로컬 파일로 바꿔야 한다."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    md_path = work_dir / "제목.md"
    md_path.write_text(
        "# 제목\n\n본문 ![오사카 성](https://example.com/osaka.jpg) 이어지는 문장.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline.image_fetch,
        "download_and_crop_square",
        lambda url, dest: (dest.parent.mkdir(parents=True, exist_ok=True), dest.write_bytes(b"jpg"), True)[-1],
    )

    result_paths = pipeline._extract_and_apply_claude_images(work_dir, md_path, 1)

    content = md_path.read_text(encoding="utf-8")
    assert "https://example.com/osaka.jpg" not in content
    assert "![오사카 성](images/claude_extra_1.jpg)" in content
    assert len(result_paths) == 1


def test_run_generation_creates_images_in_same_codex_call_when_enabled(tmp_path, monkeypatch):
    """Task 017: 글 작성과 이미지 생성을 codex exec 한 번으로 합쳐 요청한다(2차 codex exec
    호출이 응답 없이 멈추는 문제가 있어 사용자 요청으로 통합)."""
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    work_dir = tmp_path / "work"
    captured_prompt = {}

    def fake_run(args, cwd=None, input_text=None, **kwargs):
        captured_prompt["text"] = input_text
        images_dir = config.images_dir(work_dir)
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "generated_0.png").write_bytes(b"fake")
        md_path = work_dir / f"{config.safe_title('오키나와 여행')}.md"
        md_path.write_text("# 오키나와 여행\n\n본문입니다.\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    context = pipeline.PipelineContext(
        keyword="오키나와 여행", use_crawling=False, generate_images=True, image_gen_count=1
    )

    status, md_path, generated_images = pipeline.run_generation(context, work_dir, [])

    assert status == "success"
    assert len(generated_images) == 1
    assert "$imagegen" in captured_prompt["text"]


def test_run_generation_passes_absolute_image_paths_and_writes_md(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    image_file = tmp_path / "photo.jpg"
    image_file.write_bytes(b"fake")

    captured_args = {}

    def fake_run(args, **kwargs):
        captured_args["args"] = args
        output_dir = config.output_dir(work_dir)
        (output_dir / "last_message.txt").write_text("생성된 본문", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(
        keyword="오키나와 여행", image_paths=[str(image_file)], use_crawling=False
    )

    status, md_path, generated_images = pipeline.run_generation(context, work_dir, [])

    assert status == "success"
    assert md_path.exists()
    # AI가 응답 본문에 첨부 이미지를 언급하지 않아도(여기서는 아예 참조가 없음),
    # 사용자가 첨부한 이미지는 모두 본문에 포함돼야 한다(사용자 요청 —
    # _ensure_attached_images_included가 누락된 첨부 이미지를 코드로 강제 삽입함).
    text = md_path.read_text(encoding="utf-8")
    assert "생성된 본문" in text
    assert "images/photo.jpg" in text
    assert generated_images == []

    image_index = captured_args["args"].index("--image")
    assert Path(captured_args["args"][image_index + 1]) == image_file.resolve()


def test_run_generation_uses_claude_backend_when_ai_model_is_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    captured = {}

    def fake_run(args, cwd=None, input_text=None, tee_path=None, **kwargs):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["tee_path"] = tee_path
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="오키나와 여행", use_crawling=False, ai_model="claude:sonnet")

    pipeline.run_generation(context, work_dir, [])

    assert captured["args"] == config.build_claude_exec_args("sonnet")
    assert captured["cwd"] == work_dir
    assert captured["tee_path"] == config.output_dir(work_dir) / "last_message.txt"


def test_run_generation_falls_back_to_tee_path_when_claude_backend_and_no_md_found(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    def fake_run(args, cwd=None, input_text=None, tee_path=None, **kwargs):
        if tee_path is not None:
            tee_path.parent.mkdir(parents=True, exist_ok=True)
            tee_path.write_text("claude가 응답한 본문", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="제목없는글", use_crawling=False, ai_model="claude:sonnet")

    status, md_path, generated_images = pipeline.run_generation(context, work_dir, [])

    assert status == "success"
    assert md_path.read_text(encoding="utf-8") == "claude가 응답한 본문"


def test_run_generation_passes_model_to_codex_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    captured_args = {}

    def fake_run(args, **kwargs):
        captured_args["args"] = args
        output_dir = config.output_dir(work_dir)
        (output_dir / "last_message.txt").write_text("생성된 본문", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="키워드", use_crawling=False, ai_model="codex:gpt-5.6-sol")

    pipeline.run_generation(context, work_dir, [])

    assert "--model" in captured_args["args"]
    model_index = captured_args["args"].index("--model")
    assert captured_args["args"][model_index + 1] == "gpt-5.6-sol"


def test_resolve_agents_md_content_uses_custom_path_when_set(tmp_path):
    custom = tmp_path / "custom_agents.md"
    custom.write_text("커스텀 지침", encoding="utf-8")

    context = pipeline.PipelineContext(keyword="키워드", agents_md_path=str(custom))
    assert pipeline._resolve_agents_md_content(context) == "커스텀 지침"


def test_resolve_agents_md_content_falls_back_when_custom_path_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "기본 지침")

    context = pipeline.PipelineContext(keyword="키워드", agents_md_path=str(tmp_path / "없는파일.md"))
    assert pipeline._resolve_agents_md_content(context) == "기본 지침"


def test_sync_agents_md_writes_inside_work_dir_not_parent(tmp_path):
    """work_dir.parent에 쓰면 실제 운영에서 PostResult/AGENTS.md 원본을 덮어써 버리므로
    (태스크별 커스텀 AGENTS.md 선택 기능 도입 이후 위험해짐), work_dir 바로 안에 써야 한다."""
    work_dir = tmp_path / "PostResult" / "2026-07-18_제목"
    pipeline._sync_agents_md_for_codex_discovery(work_dir, "동기화된 내용")

    assert (work_dir / "AGENTS.md").read_text(encoding="utf-8") == "동기화된 내용"
    assert not (tmp_path / "PostResult" / "AGENTS.md").exists()


def test_run_generation_with_custom_agents_md_still_detects_generated_md(tmp_path, monkeypatch):
    """AGENTS.md 동기화 파일 자신이 "새로 생긴 *.md"로 오인되어 실제 생성된 글 대신
    AGENTS.md 내용이 채택되는 회귀를 막는다(과거 실제로 발생했던 버그)."""
    custom = tmp_path / "custom_agents.md"
    custom.write_text("커스텀 지침", encoding="utf-8")

    work_dir = tmp_path / "work"

    def fake_run(args, **kwargs):
        output_dir = config.output_dir(work_dir)
        (output_dir / "last_message.txt").write_text("생성된 본문", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    context = pipeline.PipelineContext(
        keyword="제목", use_crawling=False, agents_md_path=str(custom)
    )

    status, md_path, generated_images = pipeline.run_generation(context, work_dir, [])

    assert status == "success"
    assert md_path.name != "AGENTS.md"
    assert md_path.read_text(encoding="utf-8") == "생성된 본문"
    assert generated_images == []
