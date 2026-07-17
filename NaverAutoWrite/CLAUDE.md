# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 작업 이력 기록 규칙 (IMPORTANT)

사용자로부터 프롬프트(요청)를 받으면 다음 순서를 반드시 지킨다.

1. 사용자가 입력한 프롬프트 원문을 **수정하거나 요약하지 않고 그대로** 먼저 출력한다.
2. 요청된 작업을 수행한다.
3. 작업이 끝나면 `history.md` 파일에 아래 형식으로 기록을 추가한다.
   - 파일이 없으면 새로 생성한다.
   - 기존 내용은 삭제하지 않고 맨 아래에 이어서 추가(append)한다.
   - 기록 형식:

     ```markdown
     ## [YYYY-MM-DD HH:MM] 요청 요약: <한 줄 요약>

     ### 입력 프롬프트 (원문)
     <사용자가 입력한 프롬프트 원문 그대로>

     ### 처리 결과 요약
     - <무엇을 했는지 핵심만 불릿으로 요약>
     - <변경/생성한 파일 경로>
     - <특이사항, 리스크, 다음 할 일 등이 있다면 함께 기재>
     ```

4. `history.md`에는 프롬프트 원문 전체와 결과 요약만 기록한다. 대화 중 생성된
   중간 산출물(코드 전문, 긴 로그 등)은 기록하지 않고 요약만 남긴다.
5. 단순 질의응답(코드 설명, 파일 조회 등 아무 변경도 발생하지 않는 요청)도
   기록 대상이다. 다만 작업 지시가 아닌 순수 확인/대화성 메시지는 생략 가능하다.

## 그 외 지침

- 모든 응답은 한글로 작성한다.

## 프로젝트 현황

이 리포지토리는 현재 **문서/설계 단계**이며, `main.py` 등 실제 소스 코드는 아직 존재하지 않는다.
지금까지의 작업은 요구사항 정리(`doc/PRD_PROMPT.md`) → PRD 작성(`doc/PRD.md`) → 기술 검증(`prd-validator`) → 로드맵 수립(`doc/ROADMAP.md`) 단계까지 진행되었다.
구현에 착수할 때는 `doc/ROADMAP.md`의 Task 001부터 순서대로 진행한다(Task 003~005, 009는 `[Critical]`로 표시된 우선 검증 대상).

## 프로젝트 목적

md(마크다운) 초안 파일을 입력받아 네이버 블로그 로그인 → 글쓰기 에디터 진입 → 제목/본문/이미지 자동 입력 → 발행까지 수행하는 Python CLI 자동화 도구.

## 문서 기반 워크플로우 (이 저장소 고유)

이 저장소는 코드보다 문서가 먼저 성숙하는 방식으로 진행된다. 새로운 기능/요구사항 변경 시 아래 문서 체인의 정합성을 유지해야 한다.

1. `doc/PRD_PROMPT.md` — PRD 생성을 위한 원본 요구사항 프롬프트 템플릿
2. `doc/PRD.md` — 기능 ID(F001, F002...) 단위로 정의된 실제 명세. 모든 기능은 기능 명세 표 → 명령어 구조 → 모듈별 상세 명세 3곳에서 상호 참조되어야 함(`.claude/agents/prd-generator.md`의 정합성 원칙)
3. `doc/ROADMAP.md` — PRD의 기능 ID를 Task 단위로 매핑한 구현 계획. Phase/Task 상태는 체크박스(`✅`)와 `- 우선순위` 표기로 관리
4. `history.md` — 모든 요청의 원문과 처리 결과 요약 append 로그(위 기록 규칙 참조)

`doc/ROADMAP.md`를 갱신할 때는 `/docs:update-roadmap` 커맨드 사용을 우선 고려한다. 단, 이 커맨드 정의(`.claude/commands/docs/update-roadmap.md`)는 `docs/ROADMAP.md` 경로를 전제로 작성되어 있으나 실제 파일은 `doc/ROADMAP.md`(단수)에 있으므로 경로 불일치에 주의한다.

## 서브에이전트 (`.claude/agents/`)

- **prd-validator**: PRD를 Chain-of-Thought로 기술 검증(FACT/INFERENCE/UNCERTAIN 태깅, 5단계 판정). 서브에이전트 타입으로 정상 등록되어 `Agent` 툴에서 바로 사용 가능.
- **development-planner**: PRD를 분석해 `doc/ROADMAP.md`를 생성/갱신. "구조 우선 접근법"(골격 → 데이터 계약 → 핵심 기능 → 안정화) 기반으로 Phase/Task를 구성. 서브에이전트 타입으로 정상 등록됨.
- **prd-generator**: `.claude/agents/prd-generator.md` 파일은 존재하지만 서브에이전트 타입으로는 **등록되어 있지 않다**(`Agent` 툴 호출 시 "Agent type not found" 발생). PRD를 새로 작성할 때는 이 파일에 정의된 지침(기능 ID 부여, 4단계 정합성 검증 체크리스트, 마일스톤/우선순위 등 생성 금지 규칙)을 직접 따라 수동으로 작성한다.

## 계획된 아키텍처 (`doc/PRD.md` 기준, 구현 전)

브라우저 자동화 스택은 **Playwright for Python으로 확정**되었다(Selenium 미채택 — CDP/WebSocket 통신으로 `$cdc_` 계열 DOM 마커를 남기지 않아 기본 탐지 흔적이 적음). 계획된 모듈 구성:

| 모듈 | 역할 | 관련 기능 ID |
|------|------|------|
| `main.py` | 진입점, 각 모듈 오케스트레이션, 로그/종료 코드 | F001, F013 |
| `config.py` | `.env`(NAVER_ID/PW/BLOG_ID/CATEGORY) 로드 및 검증 | F010 |
| `naver_login.py` | 세션 재사용(`storage_state.json`) 우선, 필요 시 클립보드 붙여넣기(pyperclip) 로그인, CAPTCHA/2FA 등 인증 챌린지 감지 | F002, F009, F011 |
| `md_parser.py` | md에서 제목(첫 H1)/본문 줄 리스트/이미지 경로 추출(mistune) | F004 |
| `editor.py` | `#mainFrame` iframe 전환, 팝업 정리, 제목/본문 지연 타이핑, 발행 처리 | F003, F005, F006, F008, F012 |
| `image_uploader.py` | 본문 내 이미지 참조 라인 감지 후 로컬 이미지 업로드 | F007 |

핵심 설계 원칙(기술 검증 결과 Critical로 반영됨, `doc/PRD.md` 참조):

- 로그인 시 매번 재로그인하지 않고 `context.storage_state()`로 세션을 저장·재사용하여 CAPTCHA/2FA 트리거 빈도를 낮춘다.
- 고정 `sleep` 대신 `wait_for_url`/`wait_for_selector` 등 명시적 대기를 사용한다.
- 발행 버튼 등은 해시 클래스 셀렉터(예: `header__Ceaap`처럼 빌드 시마다 바뀌는 CSS Modules 클래스)를 사용하지 않고, 텍스트/role 기반 셀렉터(`get_by_role("button", name="발행")` 등)를 사용한다.
- 발행은 단일 클릭이 아니라 "발행 버튼 → 카테고리/공개설정 패널 → 최종 발행 버튼"의 2단계 흐름으로 처리한다.

## 향후 개발/테스트 명령 (구현 후 유효, 현재는 미구현)

`doc/PRD.md`의 명령어 구조에 정의된 계획된 CLI 인터페이스:

```
pip install -r requirements.txt
playwright install
python main.py --md <path> [--blog-id <id>] [--headless] [--session-file <path>]
```

## 사용 가능한 프로젝트 슬래시 커맨드 (`.claude/commands/`)

- `git:commit`, `git:branch`, `git:merge`, `git:pr` — 표준 git 워크플로우
- `docs:update-roadmap` — `doc/ROADMAP.md` Task 완료 체크 및 진행률 갱신(대화형, 경로 불일치 주의는 위 참조)
