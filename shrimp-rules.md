# Development Guidelines

이 문서는 AI 에이전트가 `crawl_autowrite` 저장소에서 작업할 때 반드시 지켜야 할 프로젝트 고유 규칙만 정의한다. 일반적인 개발 지식(에러 처리, 커밋 컨벤션, 테스트 작성법 등)은 포함하지 않는다.

## 1. 프로젝트 개요

- Python 기반 네이버 블로그 자동 글쓰기 오케스트레이터. 웹 프레임워크(Next.js/React 등), 데이터베이스, REST/GraphQL API 서버는 사용하지 않는다.
- 현재 오케스트레이터 자체 코드(`app.py`, `pipeline.py` 등)는 존재하지 않는다. 문서(`docs/PRD.md`, `docs/ROADMAP.md`)만 확정된 상태다.
- 3개의 기존 완성 서브 프로젝트(`NaverBlogCrawlingByPlayWright`, `NaverAutoWrite`, `PostResult/AGENTS.md`)를 서브프로세스로 호출해 통합한다.

## 2. 프로젝트 아키텍처 — 서브프로세스 경계

- `NaverBlogCrawlingByPlayWright/`, `NaverAutoWrite/`는 각각 독립된 의존성을 가진 별도 실행 단위다. 오케스트레이터 코드에서 이 폴더의 모듈을 `import`로 직접 불러오지 말고, 반드시 `subprocess`로 별도 프로세스 실행한다(`docs/PRD.md` 6절).
- 오케스트레이터 모듈 구조는 `docs/PRD.md` 6절/`docs/ROADMAP.md` Task 001 기준을 따른다: `app.py`(GUI 진입점), `pipeline.py`(오케스트레이션), `agents_editor.py`(AGENTS.md 조회/편집), `image_input.py`(드래그 드롭/뷰어), `subprocess_runner.py`(서브프로세스 공통 실행기). 새 모듈을 추가할 때 이 5개 모듈 중 어디에도 속하지 않으면 먼저 `docs/PRD.md` 6절에 모듈을 추가하고 나서 코드를 작성한다.

## 3. 문서 체인 — 다중 파일 동시 수정 규칙

- `docs/PRD.md`의 4.1~4.7 기능 요구사항을 변경/추가하면, 반드시 같은 작업에서 `docs/ROADMAP.md`의 대응 Task도 함께 갱신한다. 둘 중 하나만 고치고 끝내지 않는다.
- `docs/ROADMAP.md`의 Task 완료 체크는 `/docs:update-roadmap` 커맨드(`.claude/commands/docs/update-roadmap.md`)를 사용한다. 이 커맨드는 `docs/ROADMAP.md`(복수형 `docs`)를 대상으로 하므로, 다른 문서에서 `doc/`(단수형)라는 경로를 보게 되면 그것은 오기이며 실제 경로는 `docs/`이다(`NaverAutoWrite/CLAUDE.md`가 이 오기를 포함하고 있음 — 그 파일 내용보다 루트 `CLAUDE.md`를 우선한다).
- `history.md`는 `.claude/settings.json`의 `UserPromptSubmit`/`Stop` 훅이 자동으로 append한다. 에이전트나 코드가 `history.md`에 직접 쓰기 작업을 수행하지 않는다(중복 기록 방지).

## 4. 기존 구성요소 CLI 계약 — 절대 변경 금지

아래 3개 계약은 `docs/PRD.md`의 "기존 구성요소 계약"에서 확정된 것이며, 오케스트레이터 구현 중 CLI 인자명·경로·파일명 형식·환경변수명을 임의로 바꾸지 않는다.

- **크롤링** (`NaverBlogCrawlingByPlayWright/blogcontentsClawring.py`): `python blogcontentsClawring.py --keyword <키워드> --count <1~100> --mode http [--headless]`. `.env`는 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`. 산출물은 스크립트 파일 위치 기준 `result/YYYYMMDD/{안전한제목}_{YYYYMMDD_HHMMSS}.txt`(실행 시 CWD 기준이 아님, `blogcontentsClawring.py`의 `get_base_dir()` 참조).
- **AI 글 지침** (`PostResult/AGENTS.md`): 페르소나·문체·글 구조·HTML 출력 규격·폴더 규칙(`PostResult/<날짜_제목>/{images, blog, output}/`)을 정의한다. 오케스트레이터 코드는 이 파일을 조회/편집 UI에서만 열고, 프로그램이 이 파일의 내용(폴더 규칙, 문체 등)을 자동 생성·덮어쓰기·재구성하지 않는다.
- **자동 발행** (`NaverAutoWrite/main.py`): `python main.py --md <md파일경로> [--blog-id <id>] [--headless] [--session-file <path>]`. `.env`는 `NAVER_ID`, `NAVER_PW`, `NAVER_BLOG_ID`, `NAVER_CATEGORY`(선택). md 규칙: 첫 번째 `# H1` = 제목, `![대체텍스트](경로)` = 이미지.

## 5. 서브프로세스 연동 시 필수 처리 (`subprocess_runner.py`, `pipeline.py` 구현 시)

- `NaverAutoWrite/main.py`에 `--md` 인자로 넘기는 md 파일 안의 이미지 경로(`![대체텍스트](경로)`)는 반드시 **절대경로**로 작성하거나, 발행 직전 절대경로로 치환한다. 이유: `NaverAutoWrite/image_uploader.py`는 `Path(image_path).exists()`를 **서브프로세스 실행 시 CWD 기준**으로 해석하며, 파일이 없어도 예외 없이 조용히 건너뛴다(로그만 남김). 상대경로를 그대로 쓰면 "발행 성공, 이미지 누락"이 조용히 발생한다.
- `output/{제목}.md` 파일명과 md 내부 `# H1` 제목은 동일한 원본 제목 문자열에서 파생시키고, 파일명 생성 시 Windows 예약 문자(`\ / : * ? " < > |`)를 제거/치환하는 안전화 처리를 반드시 거친다. `PostResult/<날짜>_<제목>/` 폴더명의 `<제목>`에도 동일한 안전화 규칙을 적용한다.
- 크롤링 산출물(`result/YYYYMMDD/*.txt`)을 `PostResult/<날짜_제목>/blog/`로 복사할 때는, 크롤링 서브프로세스 실행 **직전**의 해당 폴더 파일 목록을 스냅샷하고, 실행 후 신규 생성된 파일만 복사 대상으로 식별한다. 같은 날짜에 이미 존재하는 과거 실행 산출물을 이번 실행 결과로 착각해 복사하지 않는다.
- 모든 서브프로세스 호출은 인자를 리스트로 전달하고(`shell=True` 금지), 표준출력/표준에러 캡처 시 인코딩을 `UTF-8`로 명시한다. 한글·공백이 포함된 경로(`PostResult/<날짜_제목>/...`)를 다룰 때는 경로를 절대경로로 정규화한 뒤 전달한다.
- 동일한 `PostResult/<날짜>_<제목>/` 폴더가 이미 존재하면(같은 날짜에 같은 제목으로 재실행하는 경우 포함) 기존 폴더를 덮어쓰지 않고 순번 접미사(`_2`, `_3` 등)를 붙인 새 폴더를 생성한다.

## 6. 서브에이전트 등록 규칙 (`.claude/agents/*.md`)

- 새 서브에이전트 파일을 추가하거나 기존 파일을 수정할 때, frontmatter의 `description` 필드는 실제 줄바꿈을 넣지 말고 `\n` 이스케이프 문자를 사용한 한 줄 문자열로 작성한다. 실제 줄바꿈을 넣으면 `Agent` 툴에서 서브에이전트 타입으로 인식되지 않는 문제가 과거에 발생했다(`prd-generator.md` 사례).
- `development-planner.md`를 수정할 때는 Next.js/React/TypeScript/데이터베이스/REST·GraphQL API/Playwright MCP 등 웹 스택 전제를 다시 추가하지 않는다. 이 에이전트는 순수 Python 프로그램(CLI/데스크톱 GUI/서브프로세스 오케스트레이션) 기준으로 로드맵을 생성하도록 확정되어 있다.

## 7. 금지 행위

- `NaverBlogCrawlingByPlayWright/blogcontentsClawring.py`, `NaverAutoWrite/main.py`의 CLI 인자명·환경변수명·출력 파일명 형식을 오케스트레이터 구현 편의를 위해 임의로 변경하지 않는다(3개 서브 프로젝트는 완성된 상태이며 수정 대상이 아니다).
- `PostResult/AGENTS.md`의 내용(페르소나, 문체 규칙, 폴더 규칙, HTML 출력 규격)을 코드나 에이전트가 자동으로 재생성/덮어쓰기 하지 않는다.
- `history.md`에 코드나 프롬프트로 직접 쓰기 작업을 수행하지 않는다(훅이 전담).
- md 내 이미지 경로를 상대경로로 남겨둔 채 `NaverAutoWrite/main.py`를 호출하지 않는다(5절 참조).
- 오케스트레이터 구현 Task를 `docs/ROADMAP.md`에 반영하지 않고 임의 순서로 진행하지 않는다(Task 001부터 순서대로 진행, 우선순위 표기 준수).
