# 구현 기능: F010
"""설정/자격증명 로드 모듈.

.env(NAVER_ID, NAVER_PW, NAVER_BLOG_ID, NAVER_CATEGORY)와 CLI 인자를 결합하여
Config를 생성한다.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Config:
    naver_id: str
    naver_pw: str
    blog_id: str
    category: str | None
    session_file: str


def load_config(
    blog_id_arg: str | None = None,
    session_file_arg: str | None = None,
) -> Config:
    """.env와 CLI 인자를 로드하여 Config를 반환한다 (F010).

    blog_id_arg가 주어지면 .env의 NAVER_BLOG_ID보다 우선한다.
    필수 값(NAVER_ID, NAVER_PW, blog_id) 누락 시 에러 메시지 출력 후 종료한다.
    """
    load_dotenv()

    naver_id = os.environ.get("NAVER_ID")
    naver_pw = os.environ.get("NAVER_PW")
    if not naver_id or not naver_pw:
        print("에러: .env에 NAVER_ID/NAVER_PW가 설정되어 있어야 합니다.", file=sys.stderr)
        sys.exit(1)

    blog_id = blog_id_arg or os.environ.get("NAVER_BLOG_ID")
    if not blog_id:
        print(
            "에러: 블로그 ID가 없습니다. --blog-id 인자 또는 .env의 NAVER_BLOG_ID를 설정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    category = os.environ.get("NAVER_CATEGORY") or None

    if not session_file_arg:
        print("에러: session_file 경로가 지정되어야 합니다.", file=sys.stderr)
        sys.exit(1)

    return Config(
        naver_id=naver_id,
        naver_pw=naver_pw,
        blog_id=blog_id,
        category=category,
        session_file=session_file_arg,
    )
