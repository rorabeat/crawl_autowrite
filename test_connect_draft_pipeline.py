import json
from unittest.mock import Mock

import pytest

from source.core import pipeline


SHOPPING = "https://naver.me/shop?a=1&b=2"
EXTERNAL_SHOPPING = "https://store.example/shop?ref=123"
TRAVEL = "https://naver.me/travel"
SHOPPING_NOTICE = "이 글은 제휴 활동을 통해 일정액의 수수료를 제공받을 수 있습니다."
TRAVEL_NOTICE = "이 포스팅은 네이버 여행 커넥트 활동의 일환으로, 예약 발생 시 수수료를 제공받습니다."


@pytest.mark.parametrize(
    ("links", "notices"),
    [
        ({}, []),
        ({SHOPPING: "shopping"}, [SHOPPING_NOTICE]),
        ({TRAVEL: "travel"}, [TRAVEL_NOTICE]),
        ({SHOPPING: "shopping", TRAVEL: "travel", EXTERNAL_SHOPPING: "shopping"}, [SHOPPING_NOTICE, TRAVEL_NOTICE]),
    ],
)
def test_connect_links_reach_review_and_saved_checkpoint(tmp_path, monkeypatch, links, notices):
    work_dir = tmp_path / "draft"
    original = "# 제목\n\n첫 문단입니다.\n\n[일반 링크](https://example.com)\n"
    comment = "사용자가 입력한 일반 링크 https://example.com"

    def generate(args, **kwargs):
        assert comment in kwargs["input_text"]
        for piece in [*links, *notices]:
            assert piece in kwargs["input_text"]
        (work_dir / "제목.md").write_text(original, encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.config, "QUEUE_STATE_JSON_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(pipeline, "_resolve_work_dir", lambda keyword: work_dir)
    monkeypatch.setattr(pipeline, "_resolve_agents_md_content", lambda context: "작성 지침")
    monkeypatch.setattr(pipeline.subprocess_runner, "run", generate)
    publish = Mock(return_value=("success", None))
    monkeypatch.setattr(pipeline, "run_publish", publish)
    task = pipeline.TaskItem(
        "test", "제목", "제목", comment=comment, use_crawling=False,
        review_draft=True, connect_affiliate_links=links,
    )
    context = task.to_pipeline_context()
    seen = []

    def review(path):
        content = path.read_text(encoding="utf-8")
        seen.append(content)
        assert content.startswith("# 제목\n")
        assert "첫 문단입니다." in content and "[일반 링크](https://example.com)" in content
        for url in links:
            assert content.count(f"({url})") == 1
        for notice in (SHOPPING_NOTICE, TRAVEL_NOTICE):
            assert content.count(notice) == (1 if notice in notices else 0)
        if not links:
            assert content == original
        return True

    result = pipeline.run_pipeline(context, on_review=review)
    assert result["steps"]["generate"] == {"status": "success"}
    assert result["steps"]["publish"] == {"status": "success"}
    assert len(seen) == 1
    pipeline.save_queue_state([task])
    reopened = pipeline.load_queue_state()[0].to_pipeline_context()
    assert reopened.connect_affiliate_links == links
    reopened.resume_work_dir = work_dir

    def cannot_generate(*args, **kwargs):
        pytest.fail("a completed draft must be reused")

    monkeypatch.setattr(pipeline.subprocess_runner, "run", cannot_generate)
    result = pipeline.resume_completed_draft(reopened, None, None, review)
    assert result["steps"]["generate"] == {"status": "reused"}
    assert seen[0] == seen[1]
    assert publish.call_count == 2


def test_existing_text_and_exact_link_do_not_get_duplicate_additions(tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_text(
        f"# 제목\n\n**{SHOPPING_NOTICE}**\n\n[상품]({SHOPPING})\n"
        f"<!-- {TRAVEL_NOTICE}\n{TRAVEL} -->\n"
        f"```text\n{TRAVEL_NOTICE}\n{TRAVEL}\n```\n"
        f"[비슷한 URL]({TRAVEL}-other)\n"
        f"[다른 링크](https://example.com/?source={TRAVEL})\n",
        encoding="utf-8",
    )
    links = {SHOPPING: "shopping", TRAVEL: "travel"}
    pipeline._ensure_connect_links_included(draft, links)
    content = draft.read_text(encoding="utf-8")
    assert content.count(SHOPPING_NOTICE) == 1
    assert content.count(f"({SHOPPING})") == 1
    assert f"\n\n{TRAVEL_NOTICE}\n\n[여행 제휴 링크]({TRAVEL})\n\n" in content
    pipeline._ensure_connect_links_included(draft, links)
    assert draft.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "hidden",
    [
        f"    {TRAVEL_NOTICE}\n    [예약]({TRAVEL})\n",
        f"\t{TRAVEL_NOTICE}\n\t[예약]({TRAVEL})\n",
        f"<pre class='example'>\n{TRAVEL_NOTICE}\n<a href='{TRAVEL}'>링크</a>\n</pre>\n",
    ],
)
def test_code_examples_do_not_count_as_visible_connect_content(tmp_path, hidden):
    draft = tmp_path / "draft.md"
    original = f"# 여행\n\n여행을 소개합니다.\n\n{hidden}"
    draft.write_text(original, encoding="utf-8")

    links = {TRAVEL: "travel"}
    assert pipeline.present_connect_affiliate_links(original, links) == {}
    pipeline._ensure_connect_links_included(draft, links)
    content = draft.read_text(encoding="utf-8")

    assert f"\n\n{TRAVEL_NOTICE}\n\n[여행 제휴 링크]({TRAVEL})\n\n여행을 소개합니다." in content
    assert hidden in content
    assert pipeline.present_connect_affiliate_links(content, links) == links
    pipeline._ensure_connect_links_included(draft, links)
    assert draft.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "text",
    [
        TRAVEL,
        f"[여행]({TRAVEL})",
        f"<{TRAVEL}>",
        f"예약할 상품 ({TRAVEL}), 참고하세요.",
        f"예약할 상품 [링크 {TRAVEL}].",
        f"예약할 상품 {TRAVEL}。",
    ],
)
def test_form_edits_keep_the_exact_typed_destination(text):
    assert pipeline.present_connect_affiliate_links(text, {TRAVEL: "travel", SHOPPING: "shopping"}) == {TRAVEL: "travel"}


@pytest.mark.parametrize(
    "text",
    [
        f"{TRAVEL}suffix",
        f"{TRAVEL}?x=1",
        f"[여행]({TRAVEL}?x=1)",
        f"[표시값 {TRAVEL}](https://example.com)",
        f"![사진]({TRAVEL})",
        f"https://example.com?target={TRAVEL}",
        f"가짜접두어{TRAVEL}",
    ],
)
def test_form_edits_drop_a_different_url_or_plain_label(text):
    assert pipeline.present_connect_affiliate_links(text, {TRAVEL: "travel"}) == {}


def test_connect_url_validation_and_legacy_queue(tmp_path, monkeypatch):
    path = tmp_path / "queue.json"
    monkeypatch.setattr(pipeline.config, "QUEUE_STATE_JSON_PATH", path)
    legacy = {"task_id": "old", "label": "제목", "keyword": "제목", "login_mode": "manual"}
    path.write_text(json.dumps([legacy]), encoding="utf-8")
    old = pipeline.load_queue_state()[0]
    assert old.to_pipeline_context().connect_affiliate_links == {}
    pipeline.save_queue_state([old])
    assert "connect_affiliate_links" not in json.loads(path.read_text(encoding="utf-8"))[0]

    urls = {
        SHOPPING: "shopping",
        TRAVEL: "travel",
        EXTERNAL_SHOPPING: "shopping",
        "https://naver.me/path\n[bad]": "travel",
        "https://naver.me/path%0d%0aBad": "travel",
        "https://naver.me.evil/travel": "travel",
        "https://user@naver.me/travel": "travel",
        "https://naver.me:8443/travel": "travel",
        "http://naver.me/travel": "travel",
        "https://naver.me/bad)[]": "travel",
        "https://naver.me/other": ["travel"],
    }
    path.write_text(json.dumps([{**legacy, "connect_affiliate_links": urls}]), encoding="utf-8")
    restored = pipeline.load_queue_state()[0]
    assert restored.connect_affiliate_links == {SHOPPING: "shopping", TRAVEL: "travel", EXTERNAL_SHOPPING: "shopping"}
    path.write_text(json.dumps([{**legacy, "connect_affiliate_links": None}]), encoding="utf-8")
    assert pipeline.load_queue_state()[0].to_pipeline_context().connect_affiliate_links == {}


def test_existing_draft_keeps_its_content(tmp_path, monkeypatch):
    draft = tmp_path / "제목.md"
    draft.write_text("# 기존 초안\n\n수정한 원문", encoding="utf-8")
    runner = Mock()
    monkeypatch.setattr(pipeline.subprocess_runner, "run", runner)
    context = pipeline.PipelineContext("제목", connect_affiliate_links={TRAVEL: "travel"})
    assert pipeline.run_generation(context, tmp_path, []) == ("failed", draft, [])
    assert draft.read_text(encoding="utf-8") == "# 기존 초안\n\n수정한 원문"
    runner.assert_not_called()
