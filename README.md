# 네이버 블로그 자동 작성 오케스트레이터

이미지·키워드·코멘트 입력 한 번으로 크롤링 → AI 글 생성 → 네이버 블로그 발행까지
순차 자동화하는 Python 데스크톱 GUI 오케스트레이터.

기존에 독립적으로 존재하던 3개의 도구(`NaverBlogCrawlingByPlayWright`, `codex exec`,
`NaverAutoWrite`)를 하나의 GUI에서 서브프로세스로 순서대로 호출한다. 자세한 요구사항은
`docs/PRD.md`, 구현 계획은 `docs/ROADMAP.md`를 참고한다.

## 기술 스택

- Python + [PySide6](https://doc.qt.io/qtforpython/)(GUI 프레임워크 — 선정 근거는 `docs/PRD.md` 7절 참조)
- 웹 프레임워크·데이터베이스·REST/GraphQL API 서버는 사용하지 않는다(순수 데스크톱 앱)
- 테스트: `pytest` + `pytest-qt`

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
python app.py
```

## 테스트

```bash
pytest tests/
```

Windows에서 GUI 없이(헤드리스) 테스트를 실행하려면 `QT_QPA_PLATFORM=offscreen` 환경변수를
설정한다.

## 구조

| 모듈 | 역할 |
|---|---|
| `app.py` | GUI 진입점, 4탭(입력/AGENTS.md 편집/실행·로그/결과) 구성 |
| `pipeline.py` | 크롤링→AI 생성→발행 오케스트레이션, `PipelineContext` 데이터 모델 |
| `agents_editor.py` | `PostResult/AGENTS.md` 조회/편집(내용 자체는 재구성하지 않음) |
| `image_input.py` | 이미지 드래그 앤 드롭/썸네일 뷰어 |
| `subprocess_runner.py` | 3개 외부 프로그램 공통 서브프로세스 실행기(UTF-8 스트리밍, 타임아웃 처리) |
| `config.py` | 3개 서브 프로젝트 CLI 계약, 인터프리터 경로 설정, PostResult 폴더 규칙 |

3개 기존 서브 프로젝트(`NaverBlogCrawlingByPlayWright/`, `NaverAutoWrite/`, `PostResult/AGENTS.md`)는
독립된 완성 프로그램이며 이 오케스트레이터가 코드를 직접 수정하거나 import하지 않고
서브프로세스로만 호출한다.

## 설정값 (잠정값 — 필요 시 조정)

`config.py`에 있는 아래 값들은 실측 근거 없이 정한 잠정값이다. 실제 사용 패턴에 맞춰
`config.py`에서 직접 조정한다.

- `CRAWLER_COUNT_DEFAULT`(현재 3, 사용자 확정값): 크롤링 개수 기본값
- `PROMPT_MAX_CHARS`(현재 20000): `codex exec` 프롬프트에 결합하는 크롤링 결과 텍스트의 문자 수 상한(`docs/PRD.md` 7절 M-2 참조)
- `DEFAULT_INTERPRETER_CONFIG`(현재 각각 `"python"`): 크롤링/발행 서브프로세스를 실행할 파이썬 인터프리터 경로. 각 서브 프로젝트가 독립 `.venv`를 쓰는 환경이라면 해당 `.venv`의 `python.exe` 절대경로로 교체한다.

`subprocess_runner.run()`은 `timeout`(초) 인자를 받으므로, 서브프로세스별 타임아웃이
필요하면 호출부에서 지정한다(현재 파이프라인 함수들은 무제한 대기가 기본값).

## 배포 방식 (PyInstaller onefile)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --distpath . --workpath build --specpath build --name app app.py
```

`--distpath .`로 exe를 저장소 루트에 바로 생성한다 — `config.py`의 `_WORK_ROOT`가
(얼어붙은 실행 파일일 때는) exe 자신의 위치를 기준으로 `NaverBlogCrawlingByPlayWright/`,
`NaverAutoWrite/`, `PostResult/`를 찾으므로, exe가 `dist/` 등 다른 위치에 있으면 이 폴더들을
찾지 못한다. 빌드된 `app.exe`는 저장소 루트(이 폴더들과 같은 위치)에 그대로 둬야 한다.

`NaverBlogCrawlingByPlayWright/`, `NaverAutoWrite/`는 각각 별도의 완성 프로그램이므로
오케스트레이터 exe에 번들되지 않는다 — exe 옆에 그대로 있어야 하고, 각 폴더의 `.env`와
파이썬 실행 환경(현재는 오케스트레이터 venv에 두 서브 프로젝트 의존성을 모두 설치해
`python`으로 실행)이 그대로 필요하다.

## 관련 문서

- `docs/PRD.md` — 제품 요구사항 명세
- `docs/ROADMAP.md` — Phase/Task 단위 구현 계획 및 진행 현황
- `docs/MANUAL_QA_CHECKLIST.md` — 데스크톱 GUI 수동 QA 체크리스트
