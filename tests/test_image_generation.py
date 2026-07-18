"""이미지 생성 개수 지정(image_gen_count) 완료 조건 검증."""

import pipeline


def test_build_image_generation_prompt_defaults_to_one_image():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True)
    prompt = pipeline._build_image_generation_prompt(context, [])
    assert "정확히 1장만" in prompt


def test_build_image_generation_prompt_reflects_requested_count():
    context = pipeline.PipelineContext(keyword="키워드", generate_images=True, image_gen_count=3)
    prompt = pipeline._build_image_generation_prompt(context, [])
    assert "정확히 3장만" in prompt
    assert "\n" not in prompt


def test_run_image_generation_skipped_when_toggle_off(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: called.append(1) or 0)

    context = pipeline.PipelineContext(keyword="키워드", generate_images=False, image_gen_count=3)
    status, paths = pipeline.run_image_generation(context, tmp_path, [])

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
    status, paths = pipeline.run_image_generation(context, work_dir, [])

    assert status == "success"
    assert len(paths) == 2
    # 실제로 images/에 남은 파일도 채택된 2장뿐이어야 한다(초과분은 옮기지 않음, 원본이
    # 이미 images/ 안에 생성된 이번 테스트 케이스에서는 초과분도 그 자리에 그대로 남는다).
    assert all(p.parent == images_dir for p in paths)
