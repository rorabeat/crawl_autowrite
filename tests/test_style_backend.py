"""Offline checks for the isolated style workflow."""

import json
from pathlib import Path
import threading
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

import pipeline
from style_multitask import runner
from style_multitask import crawl_worker
from style_multitask.models import StyleTaskItem
from style_multitask.profile import ArticleAnalysisError, analyze_first_html, markdown_body_text, visible_char_count
from style_multitask.validation import validate_article


BODY = "여행을 준비하며 교통편과 시간을 비교하고 있어요. " * 5


def _html(images: str = "") -> str:
    return f'<div class="se-main-container"><p class="se-text-paragraph">{BODY}</p>{images}</div>'


def test_style_body_photo_count_excludes_profile_stickers_duplicates_and_supports_lazy_src():
    images = (
        '<div class="profile"><img src="https://x.test/avatar.jpg"></div>'
        '<div class="sticker"><img src="https://x.test/sticker.png"></div>'
        '<img data-lazy-src="https://x.test/a.jpg?size=1">'
        '<img src="https://x.test/a.jpg?size=2">'
        '<img src="https://x.test/b.jpg">'
    )
    profile = analyze_first_html(_html(images))
    assert profile.photo_count == 2
    assert profile.body_chars == visible_char_count(BODY)
    assert profile.paragraph_count == 1


def test_style_missing_or_unresolved_article_fails_instead_of_zero_photos():
    with pytest.raises(ArticleAnalysisError):
        analyze_first_html(f"<div><p>{BODY}</p></div>")
    with pytest.raises(ArticleAnalysisError):
        analyze_first_html(_html('<div class="se-component se-image"></div>'))
    with pytest.raises(ArticleAnalysisError):
        analyze_first_html(_html('<div class="se-component se-image"><img src="https://x.test/a.jpg"></div>'
                                 '<div class="se-component se-image"></div>'))
    with pytest.raises(ArticleAnalysisError):
        analyze_first_html(_html('<img src="data:image/png;base64,AAAA">'))
    assert analyze_first_html(_html()).photo_count == 0


def test_style_equivalent_html_markdown_with_headings_and_repeated_paragraphs_have_equal_length():
    paragraph = "교통편과 가격을 비교하며 계획을 세우고 있어요. " * 3
    html = (f'<div class="se-main-container"><h2>교통 비교</h2><p>{paragraph}</p>'
            f'<p>{paragraph}</p><p>#여행 #계획</p><code>ignored</code></div>')
    markdown = f"# 제목\n\n## 교통 비교\n\n{paragraph}\n\n{paragraph}\n\n#여행 #계획\n\n`ignored`<b></b>"
    assert analyze_first_html(html).body_chars == visible_char_count(markdown_body_text(markdown))


def test_style_text_length_and_no_copy_or_visit_guard(tmp_path):
    md = tmp_path / "article.md"
    source = "가" * 100
    md.write_text("# 제목\n\n" + "나" * 100 + "\n\n#태그", encoding="utf-8")
    assert visible_char_count(markdown_body_text(md.read_text(encoding="utf-8"))) == 100
    assert validate_article(md, source, 100, 0, []) == []
    md.write_text("# 제목\n\n" + "가" * 100 + " 직접 가봤어요", encoding="utf-8")
    errors = validate_article(md, source, 100, 0, [])
    assert any("원문" in e for e in errors)
    assert any("방문" in e for e in errors)


def test_style_image_guard_checks_actual_file_and_md_reference(tmp_path):
    md = tmp_path / "article.md"
    image = tmp_path / "images" / "new.png"
    image.parent.mkdir()
    Image.new("RGB", (2, 2), "red").save(image)
    md.write_text("# 제목\n\n" + "나" * 100 + "\n\n![사진](images/new.png)", encoding="utf-8")
    assert validate_article(md, "가" * 100, 100, 1, [image]) == []
    assert validate_article(md, "가" * 100, 100, 0, [])
    assert validate_article(md, "가" * 100, 100, 1, [])
    image.write_bytes(b"image")
    assert any("디코딩" in error for error in validate_article(md, "가" * 100, 100, 1, [image]))


def test_style_attempt_image_inventory_detects_extra_and_folds_identical_copy(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    first = images / "one.png"
    Image.new("RGB", (2, 2), "red").save(first)
    original = tmp_path / "one.png"
    original.write_bytes(first.read_bytes())
    assert runner._attempt_images(tmp_path) == [first]
    second = images / "two.png"
    Image.new("RGB", (2, 2), "blue").save(second)
    assert len(runner._attempt_images(tmp_path)) == 2


def test_style_task_context_and_legacy_default():
    task = StyleTaskItem("id", "label", "keyword")
    assert task.to_pipeline_context().workflow_kind == "style"
    assert task.to_pipeline_context().crawl_count == 3
    assert pipeline.TaskItem("id", "label", "keyword").to_pipeline_context().workflow_kind == "standard"
    with pytest.raises(ValueError):
        StyleTaskItem("id", "label", "keyword", ai_model="claude:haiku").to_pipeline_context()


def test_style_crawler_keeps_first_eligible_api_rank_and_order(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVER_CLIENT_ID", "test-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "test-secret")
    items = [
        {"link": "https://other.example/post"},
        {"link": "https://blog.naver.com/first"},
        {"link": "https://blog.naver.com/second"},
    ]
    api = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": items})
    page = SimpleNamespace(raise_for_status=lambda: None, content=_html().encode("utf-8"))
    class FakeCrawler:
        session = SimpleNamespace(get=lambda *args, **kwargs: page)
        get_inner_frame_url = lambda self, url: url
        extract_title = lambda self, soup: "첫 글"
        crawl_blog_post = lambda self, url: ("두 번째", "참고 내용", "second.txt", "", "")
    fake_module = SimpleNamespace(
        load_project_dotenv=lambda: None,
        requests=SimpleNamespace(get=lambda *args, **kwargs: api),
        NaverBlogCrawler=FakeCrawler,
        _browser_headers=lambda **kwargs: {},
    )
    monkeypatch.setitem(sys.modules, "blogcontentsClawring", fake_module)
    crawl_worker.run(tmp_path, "여행", 3, tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "source_manifest.json").read_text(encoding="utf-8"))
    assert [source["search_rank_raw"] for source in manifest["sources"]] == [2, 3]
    assert manifest["sources"][0]["url"] == "https://blog.naver.com/first"
    assert (tmp_path / "run" / "blog" / "01.txt").exists()


def test_style_crawler_first_eligible_failure_does_not_use_second(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVER_CLIENT_ID", "test-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "test-secret")
    items = [{"link": "https://blog.naver.com/first"}, {"link": "https://blog.naver.com/second"}]
    api = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": items})
    second_calls = []
    class FakeCrawler:
        session = SimpleNamespace(get=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("blocked")))
        get_inner_frame_url = lambda self, url: url
        crawl_blog_post = lambda self, url: second_calls.append(url)
    fake_module = SimpleNamespace(
        load_project_dotenv=lambda: None,
        requests=SimpleNamespace(get=lambda *args, **kwargs: api),
        NaverBlogCrawler=FakeCrawler,
        _browser_headers=lambda **kwargs: {},
    )
    monkeypatch.setitem(sys.modules, "blogcontentsClawring", fake_module)
    with pytest.raises(RuntimeError, match="첫 검색 블로그 분석 실패"):
        crawl_worker.run(tmp_path, "여행", 2, tmp_path / "run")
    assert second_calls == []
    assert not (tmp_path / "run" / "source_manifest.json").exists()


def _fake_crawl(args, **kwargs):
    work_dir = Path(args[args.index("--work-dir") + 1])
    blog = work_dir / "blog"
    blog.mkdir()
    source = "가" * 100
    first = blog / "01.txt"
    first.write_text("블로그 URL: https://blog.naver.com/first\n제목: 첫째\n\n" + source, encoding="utf-8")
    manifest = {"sources": [{"filtered_rank": 1, "search_rank_raw": 2,
                             "txt": str(first), "url": "https://blog.naver.com/first"}],
                "first_profile": {"body_chars": 100, "photo_count": 0, "tone_endings": {},
                                  "average_paragraph_chars": 100, "paragraph_count": 1, "heading_count": 0}}
    (work_dir / "source_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return 0


@pytest.mark.parametrize("review,approve", [(False, True), (True, True), (True, False)])
def test_style_zero_image_run_validates_then_publishes(monkeypatch, tmp_path, review, approve):
    monkeypatch.setattr(runner, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess_runner, "run", _fake_crawl)
    monkeypatch.setattr(runner, "write_run_agents", lambda work, *args: work / "AGENTS.md")
    def generate(context, work_dir, blog_txts, cancel_event=None):
        assert context.generate_images is False
        assert context.image_gen_count == 0
        md = work_dir / "new.md"
        md.write_text("# 제목\n\n" + "나" * 100, encoding="utf-8")
        return "success", md, []
    monkeypatch.setattr(runner.pipeline, "run_generation", generate)
    published = []
    monkeypatch.setattr(runner.pipeline, "run_publish", lambda md, *args, **kwargs: (published.append(md) or "success", None))
    reviewed = []
    def on_review(md):
        assert md.exists()
        reviewed.append(md)
        return approve
    result = runner.run_style_pipeline(
        pipeline.PipelineContext(keyword="키워드", workflow_kind="style", review_draft=review),
        on_review=on_review,
    )
    assert result["steps"]["crawl"]["status"] == "success"
    assert result["steps"]["image_gen"]["status"] == "skipped"
    assert result["steps"]["publish"]["status"] == ("canceled" if review and not approve else "success")
    assert len(published) == (0 if review and not approve else 1)
    assert bool(reviewed) is review


def test_style_failed_generation_stops_after_six_without_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess_runner, "run", _fake_crawl)
    monkeypatch.setattr(runner, "write_run_agents", lambda work, *args: work / "AGENTS.md")
    attempts = []
    def generate(context, work_dir, blog_txts, cancel_event=None):
        attempts.append(work_dir)
        md = work_dir / "new.md"
        md.write_text("# 제목\n\n너무 짧음", encoding="utf-8")
        return "success", md, []
    monkeypatch.setattr(runner.pipeline, "run_generation", generate)
    monkeypatch.setattr(runner.pipeline, "run_publish", lambda *args, **kwargs: pytest.fail("must not publish"))
    result = runner.run_style_pipeline(pipeline.PipelineContext(keyword="키워드", workflow_kind="style"))
    assert len(attempts) == 6
    assert result["steps"]["generate"]["status"] == "failed"
    assert result["steps"]["publish"]["status"] == "skipped"


def test_style_first_result_failure_and_cancel_do_not_generate(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess_runner, "run", lambda args, **kwargs: 1)
    monkeypatch.setattr(runner.pipeline, "run_generation", lambda *args, **kwargs: pytest.fail("must not generate"))
    failed = runner.run_style_pipeline(pipeline.PipelineContext(keyword="키워드", workflow_kind="style"))
    assert failed["steps"]["crawl"]["status"] == "failed"
    event = threading.Event()
    event.set()
    canceled = runner.run_style_pipeline(pipeline.PipelineContext(keyword="키워드", workflow_kind="style"), cancel_event=event)
    assert canceled["steps"]["crawl"]["status"] == "canceled"
