"""Persisted definitions for the style-based task tab."""

from dataclasses import dataclass, field
from datetime import datetime

import config
from pipeline import PipelineContext


@dataclass
class StyleTaskItem:
    task_id: str
    label: str
    keyword: str
    comment: str = ""
    crawl_count: int = 3
    ai_model: str = config.AI_MODEL_DEFAULT
    login_mode: str = "auto"
    account_id: str | None = None
    review_draft: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_pipeline_context(self) -> PipelineContext:
        if not 1 <= self.crawl_count <= 100:
            raise ValueError("크롤링 개수는 1~100이어야 합니다.")
        if config.parse_ai_model(self.ai_model)[0] != "codex":
            raise ValueError("첫 글 스타일 작업은 Codex AI 이미지 생성을 지원하는 모델만 사용할 수 있습니다.")
        return PipelineContext(
            keyword=self.keyword,
            comment=self.comment,
            use_crawling=True,
            crawl_count=self.crawl_count,
            ai_model=self.ai_model,
            login_mode=self.login_mode,
            account_id=self.account_id,
            workflow_kind="style",
            review_draft=self.review_draft,
        )
