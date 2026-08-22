# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 저장소 현황

이 저장소는 **문서/설계 단계**의 오케스트레이터 프로젝트다. 기존에 독립적으로 동작하는 3개의 Python 서브 프로젝트(아래 "기존 구성요소" 참조)를 하나의 Python 데스크톱 GUI로 통합하는 것이 목표이며, 오케스트레이터 자체의 소스 코드(`app.py`, `pipeline.py` 등)는 아직 작성되지 않았다. 지금까지 진행된 것은 요구사항 정리 → PRD 작성 → 기술 검증 → 로드맵 수립까지다.

구현에 착수할 때는 `docs/ROADMAP.md`의 Task 001부터 순서대로 진행한다.

## 문서 기반 워크플로우 (이 저장소 고유)

이 저장소는 코드보다 문서가 먼저 성숙하는 방식으로 진행된다. 새 기능/요구사항 변경 시 아래 문서 체인의 정합성을 유지해야 한다.

1. `docs/PRD_PROMPT.md` — PRD 생성을 위한 원본 요구사항 프롬프트
2. `docs/PRD.md` — 실제 제품 요구사항 명세(개요/목표/사용자 시나리오/기능 요구사항 4.1~4.7/비기능 요구사항/기술 설계 개요/리스크 및 오픈 이슈/마일스톤). "리스크 및 오픈 이슈" 절에는 실제 기존 코드(`image_uploader.py`, `md_parser.py` 등)와 대조 검증된 치명적 정합성 요구사항(이미지 절대경로 처리, 제목/파일명 안전화 규칙, 프롬프트 길이 상한 등)이 포함되어 있으므로 구현 시 반드시 확인한다.
3. `docs/ROADMAP.md` — PRD를 Phase/Task 단위로 매핑한 구현 계획. Task 상태는 `- 우선순위` / `✅ - 완료` 표기로 관리하며, `/docs:update-roadmap` 슬래시 커맨드로 갱신한다.
4. `history.md` — 모든 사용자 프롬프트와 처리 결과 요약이 자동으로 append되는 로그. **`.claude/settings.json`의 `UserPromptSubmit`/`Stop` 훅이 자동으로 기록하므로, 수동으로 `history.md`에 쓸 필요가 없다.**

> `NaverAutoWrite/CLAUDE.md`에도 유사한 문서 워크플로우 설명이 있으나, 그 파일은 `doc/`(단수) 경로와 "수동으로 history.md에 기록" 규칙을 명시하고 있어 실제 경로(`docs/`, 복수)와 실제 동작 방식(훅 자동 기록)에 맞지 않는 오래된 내용이다. 그 서브 프로젝트 폴더 안에서 작업할 때도 이 루트 CLAUDE.md의 내용을 우선 신뢰한다.

## 서브에이전트 (`.claude/agents/`)

- **prd-validator**: `docs/PRD.md`를 Chain-of-Thought로 기술 검증(FACT/INFERENCE 태깅, 치명적/중요/경미 리스크 판정). 서브에이전트 타입으로 정상 등록되어 있어 `Agent` 툴로 바로 호출 가능.
- **development-planner**: PRD를 분석해 `docs/ROADMAP.md`를 생성/갱신. 순수 Python 프로그램(CLI/데스크톱 GUI/서브프로세스 오케스트레이션) 기준으로 작성하도록 되어 있다 — Next.js/React/DB/REST·GraphQL API/Playwright MCP 같은 웹 스택을 전제하지 않는다. 서브에이전트 타입으로 정상 등록됨.
- **prd-generator**: 파일은 있으나 서브에이전트 타입으로는 **등록되어 있지 않다**(`Agent` 툴 호출 시 실패). PRD를 새로 작성할 때는 이 파일에 정의된 지침(정합성 원칙 등)을 참고해 직접 따르되, `Agent(subagent_type: "prd-generator")` 호출은 정상 동작한다(확인됨 — 등록 여부는 재시작 등으로 바뀔 수 있으므로 먼저 시도해보고, 실패 시 지침을 수동 적용).

## 기존 구성요소 (변경 금지 — CLI 계약 그대로 사용)

오케스트레이터는 아래 3개를 각각 **독립 서브프로세스**로 호출하는 구조로 설계되어 있다(각 서브 프로젝트가 독립된 의존성을 가지므로 같은 파이썬 프로세스로 import하지 않는다).

### A. 크롤링 — `NaverBlogCrawlingByPlayWright/blogcontentsClawring.py`
```
python blogcontentsClawring.py --keyword <키워드> --count <1~100> --mode http [--headless]
```
- `.env`(해당 폴더 기준): `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`
- 출력: `result/YYYYMMDD/{안전한제목}_{YYYYMMDD_HHMMSS}.txt` (스크립트 파일 기준 경로, 실행 시 CWD 아님)
- 같은 날짜에 여러 번 실행하면 과거 산출물과 섞이므로, 이번 실행분만 골라내려면 실행 전후 스냅샷 비교나 타임스탬프 파싱이 필요하다.

### B. AI 글 작성 지침 — `PostResult/AGENTS.md`
- 페르소나/문체/글 구조/HTML 출력 규격을 정의하는 파일. **오케스트레이터는 이 파일의 내용을 자동으로 바꾸거나 재생성하지 않는다** — 조회/편집 UI로만 노출한다.
- 이 파일이 정의하는 폴더 규칙: 작업 산출물은 `PostResult/<날짜_제목>/{images, blog, output}/` 구조를 따르며, `output/{제목}.md`와 `output/{제목}_naver.html` 두 가지를 생성한다.

### C. 자동 발행 — `NaverAutoWrite/main.py`
```
pip install -r requirements.txt
playwright install
python main.py --md <md파일경로> [--blog-id <id>] [--headless] [--session-file <path>]
```
- `.env`(해당 폴더 기준): `NAVER_ID`, `NAVER_PW`, `NAVER_BLOG_ID`, `NAVER_CATEGORY`(선택)
- md 규칙: 첫 번째 `# H1`을 제목으로 사용, 본문 내 `![대체텍스트](경로)`를 이미지로 인식(로컬 경로/원격 URL 모두 허용)
- 이미지 존재 확인(`image_uploader.py`)은 **프로세스 CWD 기준 상대경로 해석**을 사용하며, 파일이 없어도 예외 없이 조용히 건너뛴다 — 오케스트레이터에서 md에 이미지 경로를 넣을 때는 절대경로를 쓰거나 발행 직전 절대경로로 치환해야 한다(`docs/PRD.md` 7절 치명적 리스크 C-1).
- 세션은 `--session-file`(기본 `.naver_session/storage_state.json`)로 재사용되어 매 실행마다 재로그인하지 않는다. CAPTCHA/2FA 등 자동 처리 불가 인증이 감지되면 headed 브라우저에서 최대 5분간 수동 인증을 대기한다.
- 브라우저 자동화는 Playwright for Python(Selenium 아님 — CDP 통신이라 `$cdc_` 계열 탐지 흔적을 남기지 않음).

## 테스트

저장소 전체에 아직 테스트 코드가 없다(오케스트레이터 미구현, 서브 프로젝트에도 `test_*.py`/`pytest.ini`/`conftest.py` 없음). 오케스트레이터 구현 시 `docs/ROADMAP.md`의 각 Task에 정의된 `pytest` 단위/통합 테스트 체크리스트를 따른다(외부 서브프로세스는 fake/mock으로 격리). 웹 브라우저 E2E 도구(Playwright MCP 등)는 이 프로젝트(데스크톱 GUI, 웹 페이지 없음)에는 적용 대상이 아니다.

## 기타

- `.mcp.json`(루트)은 `shrimp-task-manager` MCP 서버를 등록하지만 경로가 다른 워크스페이스(`C:/gitRepo/worksapce/...`)를 가리키고 있어 이 저장소 환경에서는 그대로 쓰기 어려울 수 있다.
- `.claude/settings.local.json`에 `npm run lint`/`npm run build`/Supabase MCP 관련 권한이 있으나, 이 저장소는 순수 Python 프로젝트이므로 해당 항목은 이 프로젝트와 무관한(다른 템플릿에서 남은) 설정이다.


## 작업관리
memory-bank 폴더내에 planAndTask.md 파일을 만들고 작업계획을 입력해줘
작업계획이 이전에 진행되던 내용과 다르면 별도의 소제목으로 생성하고 신규 번호를 할당해줘 
완료된것은 strikeout 을 표시해줘
