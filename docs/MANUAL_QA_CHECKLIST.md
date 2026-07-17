# 데스크톱 GUI 수동 QA 체크리스트

`docs/ROADMAP.md` Task 011에서 작성. `pytest tests/`(42건, 2026-07-17 기준 전체 통과)로
자동 검증되지 않는 실제 GUI 조작(마우스 드래그 드롭, 실제 창 렌더링, 실제 서브프로세스
실행)을 사람이 직접 눈으로 확인하기 위한 체크리스트다. 실행 전 `python app.py`로
프로그램을 띄운 상태를 전제로 한다.

## 실행 방법

```bash
pip install -r requirements.txt
python app.py
```

## 체크리스트

### 1. 탭 전환 (4개)
- [ ] "입력" 탭이 정상적으로 표시된다
- [ ] "AGENTS.md 편집" 탭이 정상적으로 표시된다
- [ ] "실행·로그" 탭이 정상적으로 표시된다
- [ ] "결과" 탭이 정상적으로 표시된다

### 2. 이미지 드래그 앤 드롭 (입력 탭)
- [ ] 이미지 파일 1장을 드래그 드롭하면 목록에 썸네일과 함께 추가된다
- [ ] 이미지 파일 여러 장을 동시에 드래그 드롭하면 모두 추가된다
- [ ] 이미지를 하나도 첨부하지 않아도 다른 입력(키워드 등)에 지장이 없다

### 3. 키워드/코멘트/크롤링 토글 (입력 탭)
- [ ] 키워드 입력 필드에 텍스트를 입력할 수 있다
- [ ] 코멘트 입력 필드를 비워둬도 오류가 발생하지 않는다
- [ ] "크롤링 사용" 체크박스가 기본적으로 체크되어 있다
- [ ] 체크박스를 해제하면 "미사용" 상태로 전환된다

### 4. AGENTS.md 편집 (AGENTS.md 편집 탭)
- [ ] 탭 진입 시 `PostResult/AGENTS.md`의 실제 내용이 표시된다
- [ ] 텍스트를 수정한 뒤 "저장" 버튼을 누르면 파일에 반영된다(파일을 다시 열어 확인)
- [ ] 저장하지 않은 상태에서 텍스트를 수정한 뒤 "취소" 버튼을 누르면 마지막 저장 내용으로 되돌아간다
- [ ] 저장 버튼을 누르기 전까지는 파일이 변경되지 않는다(자동 저장 없음)

### 5. 실행 및 단계별 상태 표시 (실행·로그 탭)
- [ ] "실행" 버튼을 누르면 즉시 비활성화되고, 크롤링/AI 생성/발행 상태 라벨이 "running"으로 바뀐다
- [ ] 각 단계가 끝날 때마다 상태 라벨과 로그 창에 진행 상황이 추가된다
- [ ] 전체 파이프라인이 끝나면 "실행" 버튼이 다시 활성화된다
- [ ] 실행 중에도 GUI가 멈추지 않고 다른 탭으로 전환할 수 있다(백그라운드 스레드 확인)

### 6. 최소 입력 케이스
- [ ] 이미지 0장 + 코멘트 없음 + 키워드만 입력한 상태로 실행해도 파이프라인이 끝까지 진행된다

### 7. 결과 탭
- [ ] "새로고침" 버튼을 누르면 `PostResult/` 하위 작업 폴더 목록이 표시된다
- [ ] 폴더를 선택하면 해당 `result.json` 내용이 요약 영역에 표시된다
- [ ] `result.json`이 없는 폴더를 선택하면 "result.json 없음"이 표시된다

## 에러 핸들링·엣지 케이스 커버리지 매핑

아래 항목은 사람이 매번 재현하기보다 자동화 테스트로 이미 검증되어 있음을 확인하는 목적의 표다.
실 서브프로세스(네이버 로그인 등)가 관여하는 항목만 8번 "선택적 스모크 테스트"로 남긴다.

| 엣지 케이스 | 커버하는 자동화 테스트 | 검증 방식 |
|---|---|---|
| 크롤링 0건 | `tests/test_pipeline_crawling.py::test_run_crawling_zero_results_is_success` | fake 크롤러가 신규 파일을 생성하지 않아도 `success`로 처리됨을 검증 |
| 크롤링 서브프로세스 실패(비정상 종료 코드) | `tests/test_pipeline_crawling.py::test_run_crawling_failed_rc_reports_failed` | 종료 코드 1일 때 `failed` 반환 검증 |
| 크롤링 "미사용" 분기 | `tests/test_pipeline_crawling.py::test_run_crawling_skipped_when_toggle_off` | 서브프로세스 미호출 및 `blog/` 미생성 검증 |
| 서브프로세스 타임아웃(예: `codex exec` 응답 지연) | `tests/test_subprocess_runner.py::test_run_kills_process_and_returns_minus_one_on_timeout` | 출력 없이 멈춘 fake 프로세스를 강제 종료하고 `-1` 반환 검증 |
| 서브프로세스 한글 로그 인코딩 | `tests/test_subprocess_runner.py::test_run_streams_korean_output_without_corruption` | `PYTHONIOENCODING=utf-8` 주입으로 한글 stdout이 깨지지 않음을 검증 |
| 발행 로그인 실패/CAPTCHA(비정상 종료 코드로 계승) | `tests/test_publish.py::test_run_publish_failed_rc_returns_failed_without_warning` | 종료 코드 비정상 시 `failed` 반환 검증(`NaverAutoWrite` 자체의 5분 대기 로직은 별도 스모크 테스트 대상) |
| 이미지 첨부 누락(종료 코드는 정상인데 이미지가 없는 경우) | `tests/test_publish.py::test_run_publish_success_reports_missing_local_images_only` | 로컬 이미지만 검사하고 원격 URL은 제외함을 검증 |
| AI 생성 실패 시 발행 건너뛰기 | `tests/test_pipeline_orchestration.py::test_run_pipeline_skips_publish_when_generation_failed`, `tests/test_pipeline_on_step.py::test_run_pipeline_on_step_reports_publish_skipped_when_generation_failed` | 생성 실패 시 발행 서브프로세스가 호출되지 않고 `skipped` 처리됨을 검증 |
| 동일 날짜·제목 재실행 시 폴더 충돌 | `tests/test_pipeline_orchestration.py::test_resolve_work_dir_appends_suffix_on_collision` | 기존 폴더가 있으면 `_2` 접미사가 붙은 새 폴더를 생성함을 검증 |
| 발행만 재시도(부분 실패 재개) | `tests/test_pipeline_orchestration.py::test_retry_publish_reinvokes_publish_and_updates_result_json`, `test_retry_publish_raises_when_no_output_md` | 기존 `output/*.md` 재사용 및 `output/*.md` 없을 때 예외 발생 검증 |
| 프롬프트 길이 상한 초과 | `tests/test_generation.py::test_build_generation_prompt_truncates_over_limit` | 크롤링 결과 텍스트가 상한을 넘으면 앞부분만 사용됨을 검증 |
| 제목의 파일시스템 예약 문자 | `tests/test_generation.py::test_safe_title_removes_reserved_characters` | `\ / : * ? " < > \|` 제거 검증 |
| 이미지 상대경로/원격 URL 혼재 | `tests/test_generation.py::test_normalize_image_paths_in_md_converts_relative_to_absolute` | 로컬 상대경로만 절대경로로 치환, `http(s)` URL은 보존 검증 |

### 8. 선택적 실 환경 스모크 테스트 (자동화 대상 아님)

`docs/ROADMAP.md` Task 011 원문이 "선택"으로 명시한 항목으로, 이번 Task에서 실행하지 않았다.
실제 네이버 계정·크롤링 API 키가 준비된 환경에서 필요 시 수행한다.

- [ ] (선택) Windows 로컬에서 실제 3개 서브프로세스(크롤링/`codex exec`/발행)를 사용해 1회 엔드투엔드 실행
- [ ] (선택) 실제 네이버 로그인 중 CAPTCHA/2FA가 발생했을 때 `NaverAutoWrite`의 5분 대기 동작 확인
