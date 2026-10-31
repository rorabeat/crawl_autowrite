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

## 스타일 분석 멀티작업

1. **스타일 분석 멀티작업** 탭에서 **새 태스크**를 눌러 키워드와 필요한 추가 요청을 입력하고 Codex 모델, 발행 계정과 로그인 방식을 선택한다. 검색 글 수는 기본 3건이며 변경할 수 있다.
2. 태스크를 저장한 뒤 **대기열에 추가** 또는 **전체 대기열에 추가**를 누른다. 여러 키워드는 일괄 생성할 수 있고, 저장된 태스크는 편집·삭제·순서 변경할 수 있다. 기존 멀티 작업과 같은 대기열에서 순서대로 실행된다.
3. 검색 결과 중 첫 네이버 블로그 글의 말투·본문 분량·사진 수를 분석해 작업별 `AGENTS.md`를 만든다. 새 글은 방문 전 조사 관점으로 작성하며 원문 문장을 복제하지 않도록 검증한다. 본문 분량은 기준 글의 ±10%를 목표로 하고, 사진 수는 기준 글에 맞추되 최대 15장이다(기준 사진이 없으면 0장). 첫 글을 분석할 수 없으면 중단한다. 이미지 생성 지원을 위해 Codex만 선택할 수 있다.
4. 생성 결과가 검증을 통과하면 네이버 블로그에 **임시저장**한다. 네이버에서 내용을 직접 확인한 뒤 발행한다. 검증 실패 또는 취소 시 임시저장하지 않는다.

작업 정의는 `style_multitask/style_tasks.json`에 저장된다. 실행 산출물은 저장소의 `style_multitask/runs/<날짜시간>_<ID>/` 아래에 있으며, 작업별 `AGENTS.md`, 검색 결과와 `source_manifest.json`, 생성 시도(`attempts/`), 최종 Markdown·`images/`, `result.json`을 확인할 수 있다. 생성에 실패하면 최종 산출물은 없을 수 있다. 실제 AI 이미지 생성과 네이버 임시저장까지의 전체 흐름은 아직 라이브 검증하지 않았다.

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

## 모델 및 이미지 도구 (2026-10-27 갱신)

- Codex: GPT-6.1 Sol(기본), GPT-6.1 Terra, GPT-6 Astra, GPT-6 Sol, GPT-6 Luna. 설치된 CLI 0.164.0의 목록과 공식 문서를 대조했다. Terra는 CLI 0.159.0 이상이 필요하다.
- 각 선택 항목에 `imagegen`과 GPT Image 2.5 Flare 요청을 표시한다. Luna는 Low 품질 요청을 유지한다. 이미지 모델·품질은 프롬프트 요청이며 내장 도구에서 고정 지원을 보장하지 않는다. 별도 API 호출을 추가하지 않았다.
- Claude는 기존 `sonnet`/`haiku` 최신 별칭을 유지하고 공식 현재 버전 5.5를 표시한다. 로컬 별칭 재정의에 따라 실제 모델은 달라질 수 있다. 이미지 경로는 WebSearch/WebFetch 검색·다운로드이며 AI 생성이 아니다.
- 이전 GPT-5.5/5.6 및 Tera/Runa 저장값은 최신 선택값으로 해석한다. 사용자 저장 파일은 일괄 덮어쓰지 않는다. 실행 중인 앱은 다시 열어야 목록이 갱신된다.
- 근거: https://learn.chatgpt.com/docs/models · https://developers.openai.com/api/docs/guides/image-generation · https://platform.claude.com/docs/en/models/overview · https://code.claude.com/docs/en/model-config

### 스타일 작업 설정 안내
‘스타일 분석 멀티작업’ 탭의 ‘새 스타일 분석 작업’에서 생성한다. 크롤링은 항상 사용(기본 3건), 이미지 수는 첫 글 기준 최대 15장, AGENTS.md는 작업별 자동 생성하므로 수동 선택 항목이 없다. 일반 작업 창은 ‘새 일반 태스크’로 구분하며 기존 수동 설정을 유지한다. 작업 상세에도 자동 설정으로 표시한다.
