# 네이버 블로그 자동 작성 오케스트레이터 개발 로드맵

이미지·키워드·코멘트 입력 한 번으로 크롤링 → AI 글 생성 → 네이버 블로그 발행까지 무중단 자동화하는 Python 데스크톱 GUI 오케스트레이터.

## 개요

본 프로젝트는 반복적으로 여행/제품 후기 글을 작성하는 1인 블로거를 위한 **Python 데스크톱 GUI 오케스트레이터**로, 개별적으로 존재하던 3개의 도구를 하나의 프로그램에서 순차 실행합니다. 웹 프레임워크·데이터베이스·REST/GraphQL API 서버는 포함하지 않으며, 화면은 GUI 프레임워크의 탭/윈도우로, 영속 데이터는 파일시스템(`PostResult/` 및 각 서브 프로젝트 `.env`)에 저장합니다.

- **입력 통합 UI**: 이미지 드래그 드롭·뷰어, 키워드/코멘트 입력, 크롤링 사용 여부 토글, `AGENTS.md` 편집을 하나의 GUI로 제공
- **서브프로세스 오케스트레이션**: 크롤링(`blogcontentsClawring.py`), AI 생성(`codex exec`), 발행(`NaverAutoWrite/main.py`) 3개 외부 프로그램을 독립 서브프로세스로 순차 호출
- **작업 단위 산출물 관리**: `PostResult/<날짜_제목>/{images, blog, output}/` 구조로 산출물을 저장하고 단계별 진행 상황을 실시간 로그로 표시

## 개발 워크플로우

1. **작업 계획**

- 기존 코드베이스(3개 서브 프로젝트 및 `.env`, CLI 계약)를 학습하고 현재 상태를 파악
- 새로운 작업을 포함하도록 `ROADMAP.md` 업데이트
- 우선순위 작업은 마지막 완료된 작업 다음에 삽입

2. **작업 생성**

- 고수준 명세서, 관련 파일(모듈), 수락 기준, 구현 단계 포함
- 서브프로세스 연동·파이프라인 로직 작업 시 "## 테스트 체크리스트" 섹션 필수 포함 (pytest 단위/통합 테스트 시나리오 작성)
- 웹 브라우저 E2E 도구(Playwright MCP 등)는 사용하지 않으며, GUI 자체 검증은 수동 QA 체크리스트로 대체

3. **작업 구현**

- 작업 파일의 명세서를 따라 기능 구현
- 서브프로세스 연동 및 비즈니스 로직 구현 시 pytest 기반 단위/통합 테스트 수행 필수(외부 프로세스는 mock/fake로 격리)
- 각 단계 후 작업 파일 내 단계 진행 상황 업데이트

4. **로드맵 업데이트**

- 로드맵에서 완료된 작업을 ✅로 표시

## 개발 단계

### Phase 1: 애플리케이션 골격 및 계약 확정 ✅

- **Task 001: GUI 프레임워크 결정 및 프로젝트 골격 구성** ✅ - 완료
  - ✅ GUI 프레임워크 결정: **PySide6** 채택(근거는 `docs/PRD.md` 7절 참조 — 드래그 드롭/이미지 뷰어/탭이 네이티브 위젯으로 지원됨)
  - ✅ 6절 모듈 구조에 따른 스켈레톤 파일 생성: `app.py`, `pipeline.py`, `agents_editor.py`, `image_input.py`, `subprocess_runner.py`
  - ✅ 4탭 골격 구성: 입력 탭 / AGENTS.md 편집 탭 / 실행·로그 탭 / 결과 탭(`QTabWidget`, 내용 없는 빈 껍데기)
  - ✅ 의존성 관리 파일(`requirements.txt`) 및 실행 진입점(`app.py`) 정리
  - ✅ pytest 테스트 디렉터리 골격 구성(`tests/`, `pytest.ini`, `pytest-qt` 포함), 스모크 테스트 2건 통과 확인

- **Task 002: 데이터 모델 및 서브프로세스 계약 정의** ✅ - 완료
  - ✅ 파이프라인 실행 컨텍스트 `PipelineContext` dataclass 정의(`pipeline.py`): 키워드, 코멘트, 이미지 경로 목록, 크롤링 사용 여부, 작업 폴더 경로, 단계별 상태
  - ✅ 3개 서브프로세스 CLI 계약 정의(`config.py`): 크롤링(`--keyword/--count/--mode/--headless`), `codex exec`(`codex exec --help` 실측 확인 — `-i/--image`, `-C/--cd`, `-o/--output-last-message`, `--json`, `-s/--sandbox` 반영), 발행(`--md/--blog-id/--headless/--session-file`)
  - ✅ 서브프로세스 인터프리터 경로 결정 방식 정의(`config.py`의 `InterpreterConfig`/`DEFAULT_INTERPRETER_CONFIG`, 리스크 "인터프리터 경로 미확정" 해소)
  - ✅ `PostResult/<날짜_제목>/{images, blog, output}/` 폴더 규칙 함수(`config.py`) 및 `result.json` 메타데이터 스키마(`ResultJson` TypedDict) 정의, pytest 7건으로 인스턴스 생성/직렬화 검증

### Phase 2: 입력 UI 및 편집 기능 (더미/로컬 파일 활용) ✅

- **Task 003: 입력 수집 UI 구현 (4.1)** ✅ - 완료
  - ✅ 이미지 다중 드래그 앤 드롭 및 썸네일/뷰어 표시 구현(`image_input.py`의 `ImageDropList`), 이미지 0장 허용
  - ✅ 키워드 입력 필드, 사용자 코멘트 입력 필드(선택, 비어 있어도 진행 차단 없음)(`app.py`의 `InputTab`)
  - ✅ 크롤링 사용 여부 토글: 키워드 필드 옆 배치, 라벨 "크롤링 사용", 기본값 "사용"(체크됨), "미사용" 전환 가능
  - ✅ 완료 조건: 이미지·코멘트가 모두 비어 있어도 `to_pipeline_context()`가 예외 없이 `PipelineContext`를 반환, 토글 값이 `use_crawling` 필드로 전달됨(pytest-qt 3건 통과)

- **Task 004: AGENTS.md 조회/편집 UI 구현 (4.2)** ✅ - 완료
  - ✅ `PostResult/AGENTS.md` 로드/편집/저장 텍스트 영역 구현(`agents_editor.py`의 `load_agents_md`/`save_agents_md`, `app.py`의 `AgentsEditorTab`), UTF-8 인코딩 명시
  - ✅ 저장 시점 정책 결정 및 구현: **명시적 저장 버튼** 방식 채택(자동 저장은 채택하지 않음, 리스크 "저장 시점 미확정" 해소, `docs/PRD.md` 7절 참조)
  - ✅ 편집 취소 시 마지막 로드/저장 상태로 복원(`_original_content` 캐시)
  - 편집 가드레일(폴더 규칙 훼손 방지)과 파이프라인 실행 중 동시 편집 시점 정책은 이번 Task 범위 밖으로 Task 007/009에서 실제 적용
  - ✅ 완료 조건: 실제 `PostResult/AGENTS.md`(2,816자) 로드 확인, 저장/취소 동작 pytest-qt 검증, 프로그램이 문체/폴더 규칙 내용을 자동 대체·재생성하지 않음(read/write만 수행)

### Phase 3: 서브프로세스 오케스트레이션 및 핵심 파이프라인 ✅

- **Task 005: 공통 서브프로세스 실행기 구현 (subprocess_runner.py)** ✅ - 완료
  - ✅ 인자 리스트 전달(셸 미경유), 표준출력/표준에러 스레드+큐 기반 실시간 스트리밍(`on_output` 콜백), 타임아웃 시 폴링 방식으로 강제 종료(-1 반환)
  - ✅ 표준출력/표준에러 UTF-8 명시 캡처: 자식 프로세스에 `PYTHONIOENCODING=utf-8` 주입(Windows 콘솔 코드페이지로 인한 한글 깨짐 실측 발견 및 수정)
  - ✅ 작업별 인터프리터 경로 주입(`args[0]`)과 작업 디렉터리(`cwd`) 지정 지원
  - ## 테스트 체크리스트: fake 실행 스크립트(정상 종료/느린 종료/한글 stdout/비정상 종료 코드) 4건 pytest로 실제 `subprocess.Popen` 경로 검증 완료

- **Task 006: 크롤링 연동 및 산출물 식별·복사 구현 (4.3)** ✅ - 완료
  - ✅ 크롤링 사용 토글 분기(`pipeline.run_crawling`): "사용" 시에만 크롤링 서브프로세스 호출, "미사용" 시 서브프로세스 미호출 및 `blog/` 폴더 미생성
  - ✅ `blogcontentsClawring.py`를 `NaverBlogCrawlingByPlayWright` 폴더 CWD 기준으로 호출(`config.build_crawler_args`), `--count` 기본값은 `config.CRAWLER_COUNT_DEFAULT`(잠정값)
  - ✅ **산출물 식별 스냅샷**: 실행 직전/직후 `result/` 하위 `*.txt` 집합을 비교해 신규 파일만 `PostResult/<날짜_제목>/blog/`로 복사(과거 산출물과 혼동되지 않음을 pytest로 검증)
  - ✅ 크롤링 0건 시에도 `success`로 처리(정상 흐름), "미사용" 시 `blog/` 폴더 자체가 생성되지 않음
  - ✅ 테스트 체크리스트: 과거 산출물이 섞인 폴더에서 신규 파일만 식별·복사, 토글 미사용 분기, 0건 케이스, 실패 종료 코드 케이스 총 4건 pytest 통과(크롤러는 monkeypatch fake로 대체)

- **Task 007: AI 글 생성 연동 및 제목/경로 안전화 구현 (4.4)** ✅ - 완료
  - ✅ 4~5가지 입력(키워드·코멘트·`AGENTS.md`·크롤링 txt 결합 프롬프트 + 이미지 경로는 별도 `-i/--image` 인자) 조합(`pipeline._build_generation_prompt`), "미사용" 시 크롤링 txt 제외
  - ✅ **이미지 절대경로 강제**: `context.image_paths`를 `Path.resolve()`로 절대경로화해 `-i/--image`로 전달 + 생성된 md도 `config.normalize_image_paths_in_md`로 후처리 치환(이중 방어, `image_uploader.py`의 CWD 기준 상대경로 해석 문제 반영)
  - ✅ **제목/파일명 단일 출처 + 안전화**: `config.safe_title()`이 Windows 예약 문자(`\ / : * ? " < > |`) 제거(크롤러 `_safe_folder_name`과 동일 사상)
  - ✅ **프롬프트 길이 상한**: `config.PROMPT_MAX_CHARS`(잠정값 20000자) 초과 시 크롤링 txt 앞부분만 사용(리스크 M-2 해소, 상한값은 잠정치로 문서에 표시)
  - ✅ `codex exec` 실행(`config.build_codex_exec_args`) 및 `output/{제목}.md` 저장: codex가 직접 쓰지 않으면 `--output-last-message` 결과를 저장(생성 주체 결정), `{제목}_naver.html`은 codex 프롬프트 지시 범위로 유지
  - ✅ 테스트 체크리스트: 예약 문자 안전화, 상대→절대경로 치환(원격 URL 제외), 프롬프트 상한 절단, 절대경로 이미지 인자 전달을 pytest 5건으로 검증

- **Task 008: 자동 발행 연동 및 이미지 첨부 검증 구현 (4.5)** ✅ - 완료
  - ✅ `main.py --md <output/{제목}.md>`를 `NaverAutoWrite` 폴더 CWD 기준으로 호출(`pipeline.run_publish`, `config.build_publisher_args`)
  - ✅ **이미지 첨부 누락 검증**: 발행 종료 코드가 0이어도 md 내 로컬 이미지 참조 경로가 실제 존재하는지 정규식으로 재확인해 누락 시 경고 문자열 반환(원격 URL은 검사 제외)
  - 로그인 실패/CAPTCHA 리스크는 종료 코드 비정상(`failed`)으로 계승 처리(구체적 GUI 표시는 Task 010)
  - ✅ 테스트 체크리스트: 실패 종료 코드, 이미지 누락 경고(로컬만, 원격 제외), 누락 없을 때 경고 없음 3건 pytest 통과
  - 발견한 이슈: 누락 경로 리스트를 `f"{missing}"`으로 표시하면 Windows 경로의 백슬래시가 이중 이스케이프되어 가독성이 떨어져 `", ".join(missing)`으로 수정

- **Task 009: 파이프라인 오케스트레이션 및 결과 저장 구현 (pipeline.py, 4.6)** ✅ - 완료
  - ✅ Task 006~008을 순차 연결하는 파이프라인 조립(`pipeline.run_pipeline`): 입력 수집 → (크롤링) → 이미지 복사 → AI 생성 → 발행 → 결과 기록
  - ✅ **폴더 충돌 순번 처리**(`pipeline._resolve_work_dir`): 동일 `<날짜_제목>` 폴더 존재 시 `_2`, `_3` 순번 접미사로 새 폴더 생성
  - ✅ **부분 실패 재개 정책**(`pipeline.retry_publish`): 기존 `output/*.md`를 재생성 없이 발행 단계만 재시도, `result.json`의 `publish` 항목만 갱신
  - ✅ `images/`(원본 이미지 복사), `output/`(md) 구조 생성 및 `result.json` 메타데이터 기록(`config.ResultJson` 스키마)
  - 단계 실패 시 후속 처리는 "생성 실패 시 발행 skipped"로 단순화(자동 중단 vs 사용자 확인 GUI 정책은 Task 010에서 다룸, 과잉 구현 방지)
  - ✅ 테스트 체크리스트: 폴더 충돌 순번 생성, 파이프라인 단계 순서(crawl→generate→publish) 및 `result.json` 기록, 생성 실패 시 발행 스킵, 이미지 복사, 발행만 재시도(정상/파일 없음 예외) 총 6건 pytest 통합 테스트 통과(3개 서브프로세스 모두 monkeypatch)

### Phase 4: 진행 표시·통합 검증 및 마무리 ✅

- **Task 010: 진행 상황/로그 표시 및 결과 탭 구현 (4.7)** ✅ - 완료
  - ✅ `pipeline.run_pipeline`에 `on_step(step_name, status)` 콜백 추가(하위 호환 유지, 기존 호출부 변경 없음)
  - ✅ 실행·로그 탭(`RunLogTab`): 실행 버튼 클릭 시 `PipelineWorker`(QThread)가 백그라운드에서 파이프라인 실행, 크롤링/AI 생성/발행 각 단계 상태를 Qt 시그널로 안전하게 GUI에 전달·표시, 실행 중 버튼 비활성화
  - ✅ 결과 탭(`ResultTab`): `PostResult/` 하위 작업 폴더 목록 표시, 폴더 선택 시 `result.json` 요약 표시
  - 서브프로세스 표준출력 실시간 스트리밍(subprocess_runner의 `on_output`)까지 GUI 로그창에 직접 연결하는 것은 과잉 설계로 판단해 이번 Task 범위에서 제외(단계 상태 표시로 대체, 필요 시 향후 개선)
  - ✅ 테스트 체크리스트: `on_step` 콜백 호출 순서(각 단계 running→최종상태) 3건, `RunLogTab` 실행 흐름(워커 완료 후 로그/버튼 상태) 1건, `ResultTab` 폴더 목록·요약 표시 1건 총 5건 pytest-qt/pytest 통과

- **Task 011: 통합 테스트 및 수동 QA 체크리스트 작성** ✅ - 완료
  - ✅ pytest 기반 전체 스위트 재실행 확인: 42개 테스트 전원 통과(3개 서브프로세스 fake, 크롤링 사용/미사용·부분 실패 재개·폴더 충돌 시나리오 포함)
  - ✅ 데스크톱 GUI 수동 QA 체크리스트 작성(`docs/MANUAL_QA_CHECKLIST.md`): 탭 전환, 드래그 드롭, 키워드/코멘트/토글, AGENTS.md 편집 저장/취소, 실행 단계별 상태 표시, 최소 입력 케이스, 결과 탭
  - ✅ 에러 핸들링·엣지 케이스 커버리지 매핑 표 작성: 크롤링 0건/실패/미사용, 서브프로세스 타임아웃·한글 인코딩, 발행 실패, 이미지 첨부 누락, 생성 실패 시 발행 스킵, 폴더 충돌, 발행만 재시도, 프롬프트 상한, 제목 안전화, 이미지 경로 정규화가 각각 어느 pytest로 커버되는지 매핑
  - 실제 환경 스모크 테스트(선택): 체크리스트 8번 항목으로 남기고 이번 Task에서는 미실행(ROADMAP 원문상 선택 사항)

- **Task 012: 성능·안정성 개선 및 배포 준비** ✅ - 완료
  - ✅ 저장소 루트 `README.md` 작성: 개요, 설치/실행/테스트 명령, 모듈 구조 표, 3개 서브 프로젝트 연동 방식 요약
  - ✅ 설정값 문서화: `config.CRAWLER_COUNT_DEFAULT`/`PROMPT_MAX_CHARS`/`DEFAULT_INTERPRETER_CONFIG`가 잠정값임과 조정 방법을 README에 명시
  - ✅ `subprocess_runner.run()`의 `timeout` 인자 사용법을 README에 문서화(타임아웃 튜닝은 호출부에서 지정하는 기존 인터페이스로 충분, 코드 변경 불필요)
  - ✅ 배포 방식(`PyInstaller --onefile`) 방향을 README에 문서화(실제 패키징 실행/CI 구축은 스코프 밖으로 명시)

### Phase 5: 단독 실행 파일 및 태스크 관리 (사용자 요청) ✅

- **Task 013: PyInstaller 단독 실행 exe 빌드 실제 수행** ✅ - 완료
  - ✅ `config.py`의 `_WORK_ROOT`가 `sys.frozen`일 때 `Path(__file__)` 대신 `Path(sys.executable).resolve().parent`를 쓰도록 수정(onefile로 묶으면 `__file__`이 매 실행 새로 풀리는 임시 `sys._MEIPASS`를 가리켜, exe 옆의 `NaverBlogCrawlingByPlayWright/`·`NaverAutoWrite/`·`PostResult/`를 못 찾는 문제 방지)
  - ✅ `pyinstaller --onefile --windowed --distpath . --workpath build --specpath build --name app app.py`로 빌드, exe를 저장소 루트에 직접 생성(NaverBlogCrawlingByPlayWright/NaverAutoWrite/PostResult와 같은 위치에 있어야 `_WORK_ROOT` 기준 경로가 맞음)
  - ✅ 콘솔 미상속(더블클릭과 동일 조건, PowerShell `Start-Process`로 검증) 상태로 실제 실행해 크래시 없이 5탭 모두 정상 초기화되고 `PostResult/AGENTS.md`를 정확히 찾는 것 확인, 창 제목·응답 상태 정상
  - ✅ `requirements.txt`/`README.md`에 빌드 명령과 exe 배치 위치 제약(저장소 루트) 문서화
  - `.gitignore`에 `*.exe`/`build/`/`dist/`/`*.spec` 추가(컴파일 산출물은 저장소에 커밋하지 않음)

- **Task 014: 태스크 저장/관리 기능 구현 (4.8)** ✅ - 완료
  - ✅ `pipeline.py`: `TaskItem` dataclass(`PipelineContext`와 유사하나 `work_dir`/`reuse_work_dir` 없음), `to_pipeline_context()` 변환 메서드, `load_tasks()`/`save_tasks()`(`.json.tmp` → `os.replace` 원자적 교체) 추가
  - ✅ `config.py`: `TASKS_JSON_PATH` 경로 상수 추가(`_WORK_ROOT` 기준이라 exe로 빌드해도 exe 옆에 생성됨)
  - ✅ `image_input.py`: `ImageDropList.load_images()` 추가(태스크 편집 시 기존 이미지 목록 프리필용)
  - ✅ `app.py`: `TaskManager`(QObject, tasks.json CRUD + `changed` 시그널), `TaskEditDialog`(입력 탭과 동일한 필드 구성의 새 태스크/편집 다이얼로그), `MultiTaskTab`을 태스크 관리자로 확장(태스크 목록·새 태스크/편집/삭제/위로/아래로/대기열에 추가/전체 대기열에 추가) — 기존 `JobQueueManager` 실행 엔진은 변경 없이 재사용(태스크는 `to_pipeline_context()`로 변환해 `enqueue()`에 그대로 전달)
  - ✅ `tests/test_tasks.py` 신규 작성(5건): `TaskItem`↔JSON 라운드트립(한글 키워드·이미지 경로 리스트 포함), 손상된/누락된 tasks.json 폴백, `TaskItem.to_pipeline_context()` 필드 매핑, `TaskManager.add/update/remove/move`(경계값 포함)와 `changed` 시그널·디스크 반영 확인 — 전체 스위트 57건 전원 통과(기존 42건 + 이번에 추가된 5건 + 이전 세션에서 image_gen 단계 반영 안 됐던 3건 재정합)
  - GUI 자동화 도구 부재로 실제 마우스 클릭 기반 E2E는 수동 QA로 대체(`docs/MANUAL_QA_CHECKLIST.md`에 태스크 생성→재시작 후 유지→편집/삭제/순서변경→대기열 실행 체크리스트 추가)

- **Task 015: AI 이미지 생성 개수 지정 및 대기 작업 취소 (사용자 요청, 4.8 세부)** ✅ - 완료
  - ✅ `pipeline.py`: `PipelineContext`/`TaskItem`에 `image_gen_count: int = 1` 추가, `_build_image_generation_prompt`가 "정확히 N장만" 문구로 요청 장수를 명시, `run_image_generation`이 codex가 더 많이 만들어도 먼저 생성된 순서로 요청 장수만 `images/`에 채택(`max(1, count)`로 방어)
  - ✅ `app.py`: `InputTab`/`TaskEditDialog`에 "AI 실사 이미지 생성" 체크박스 옆 `QSpinBox`(1~10, 기본 1) 추가, 체크박스와 연동해 활성화/비활성화, `to_pipeline_context()`/`get_task_item()`에 반영. `MultiTaskTab` 태스크 목록 요약에 장수 표시("AI이미지 N장")
  - ✅ `app.py`: `JobQueueManager.remove_pending(job_id)` 추가(대기 중인, 아직 시작 안 된 작업만 취소 가능 — 진행 중 작업은 대상 아님). `MultiTaskTab`에 "선택한 대기 작업 삭제" 버튼 추가
  - ✅ `tests/test_image_generation.py` 신규 작성(4건), `tests/test_tasks.py`에 2건 추가, `tests/test_job_queue.py`에 `remove_pending` FIFO 취소 시나리오 1건 추가 — 전체 스위트 63건 전원 통과
  - GUI 헤드리스 스모크 테스트로 스핀박스 활성화 연동·`to_pipeline_context` 값 반영·대기 작업 삭제 배선을 실제 `MainWindow` 구성 후 확인(`pipeline.run_pipeline`을 가짜로 교체해 실제 서브프로세스는 호출하지 않음)

## 일정 및 마일스톤

- PRD 8절에 명시된 대로 구체적 일정은 **TBD**이며, 위 Phase 순서가 제안 마일스톤(입력 GUI 골격 → AGENTS.md 편집 → 크롤링 연동 → codex exec 연동 → 발행 연동 → PostResult 저장/로그 → 통합 테스트)을 반영합니다.
