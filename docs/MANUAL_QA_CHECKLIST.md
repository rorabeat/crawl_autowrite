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

### 8. 태스크 저장/관리 (멀티 작업 탭, Task 014)
- [ ] "새 태스크" 버튼으로 다이얼로그를 열어 키워드/이미지/설정을 채우고 확인하면 목록에 추가된다
- [ ] 키워드를 비운 채 확인을 누르면 경고가 뜨고 저장되지 않는다
- [ ] 앱을 완전히 종료했다가 다시 켜도 만들어 둔 태스크 목록이 그대로 남아있다(`tasks.json` 영속화 확인)
- [ ] 태스크를 선택해 "편집"하면 기존 값(이미지 포함)이 미리 채워져 있고, 수정 후 목록에 반영된다
- [ ] "삭제"를 누르면 선택된 태스크만 목록에서 사라지고 `tasks.json`에도 반영된다
- [ ] "위로"/"아래로"로 순서를 바꿀 수 있고, 맨 위/맨 아래에서는 아무 변화가 없다
- [ ] "대기열에 추가"를 누르면 "대기 중 작업" 목록에 나타나고, 태스크 자체는 "저장된 태스크" 목록에 그대로 남는다
- [ ] "전체 대기열에 추가"를 누르면 저장된 모든 태스크가 순서대로 대기열에 들어간다

### 9. AI 이미지 생성 개수 지정 및 대기 작업 취소 (Task 015)
- [ ] 입력 탭에서 "AI 실사 이미지 생성" 체크박스를 켜면 옆 스핀박스가 활성화되고, 끄면 비활성화된다(태스크 편집 다이얼로그도 동일)
- [ ] 스핀박스 값을 2~3으로 바꿔 "AI 실사 이미지 생성"을 켠 상태로 태스크를 저장하면, "멀티 작업" 탭 목록 요약에 지정한 장수("AI이미지 N장")가 표시된다
- [ ] 스핀박스 값을 늘려 실제 실행하면(실 codex exec 필요) `images/`에 지정한 장수만큼 이미지가 생긴다
- [ ] "대기 중 작업" 목록에서 항목을 선택해 "선택한 대기 작업 삭제"를 누르면 그 작업만 대기열에서 사라지고, 진행 중인 작업이나 나머지 대기 작업에는 영향이 없다
- [ ] 아무것도 선택하지 않고 "선택한 대기 작업 삭제"를 누르면 안내 로그만 뜨고 오류 없이 무시된다

### 10. 이미지 생성을 글 작성 이후로 재배치 (Task 016, 이후 Task 017로 대체됨)
- [ ] "2. AI 글작성만 실행"을 건너뛰고 곧바로 "3. 이미지 생성만 실행"을 누르면, 로그에 "아직 작성된 글이 없어 참고/삽입 없이 이미지만 생성합니다" 안내가 뜨고 이미지만 생성된다(오류로 중단되지 않음)
- 이 섹션의 나머지 항목(전체 실행 시 크롤링→AI생성→이미지생성→발행 순서로 codex exec를 두 번 호출)은 Task 017에서 한 번의 codex exec 호출로 합쳐지며 대체되었다. "3. 이미지 생성만 실행" 버튼 자체는 이미지 재시도용으로 남아 있어 위 항목은 여전히 유효하다.

### 11. 글 작성·이미지 생성 codex exec 호출 통합 (Task 017)

사용자가 실제 실행 중 "이미지 생성: running" 상태에서 계속 멈추는 문제를 리포트해(코덱스 CLI 쪽 `codex_models_manager` 캐시 오류 로그와 함께 관찰됨) codex exec 2차 호출 자체를 없애고 한 번으로 합쳤다.

- [ ] "AI 실사 이미지 생성"을 켠 상태로 "전체 실행"하면, 진행 상태가 크롤링 → AI 생성 → 이미지 생성 → 발행 순서로 표시되되, "AI 생성"이 끝나는 즉시 "이미지 생성" 상태도 함께 표시된다(별도의 "이미지 생성: running" 대기 구간이 없음 — codex exec를 한 번만 호출하기 때문)
- [ ] 생성된 글(md)을 열어보면 codex가 글을 쓰면서 바로 `images/파일명` 형식으로 이미지를 본문에 포함시켰는지 확인한다(별도 삽입 왕복 없음)
- [ ] "2. AI 글작성만 실행" 버튼을 눌렀을 때도 체크박스가 켜져 있으면 같은 방식으로 이미지가 함께 생성되고, 로그에 "이미지 N장 함께 생성됨"이 표시된다
- [ ] "AI 실사 이미지 생성"을 끈 상태로 실행하면 이미지 생성 관련 지시 없이 글만 작성되고 "이미지 생성" 단계는 "skipped"로 표시된다(기존과 동일)

### 12. 메인 창 좌/우 분할 영역 자유 리사이즈 (사용자 리포트)

"멀티 작업"/"실행·로그" 탭의 버튼 행(7개/6개 버튼)이 요구하는 너비 때문에 왼쪽 창(탭 영역)을
줄이는 데 제한이 있다는 리포트에 따라, 왼쪽 영역의 가로 sizePolicy를 Ignored로 바꿔 분할
막대(QSplitter handle)를 자유롭게 드래그할 수 있도록 했다.

- [ ] 메인 창의 좌/우 분할 막대를 마우스로 드래그하면 왼쪽 탭 영역이 버튼 텍스트 너비와
      무관하게 매끄럽게(갑자기 접히지 않고) 좁아진다
- [ ] 왼쪽 영역을 아주 좁게 줄여도 버튼이 겹쳐 보일 뿐 앱이 죽거나 예외가 발생하지 않는다
- [ ] 분할 막대를 다시 오른쪽으로 드래그하면 정상 크기로 복원된다
- [ ] "멀티 작업" 탭과 "실행·로그" 탭을 각각 띄운 채로 위 동작을 확인한다(두 탭 모두 버튼 행이 많음)

### 13. 로그 하이라이트/줄바꿈 및 이미지 생성 실패 시 우회 스크립트 금지 (Task 018)

- [ ] "AI 실사 이미지 생성"을 켠 상태로 실행하면, 상세 로그 패널(우측)에 codex exec로 보내는
      프롬프트 줄들이 `[CODEX 입력]` 마커와 함께 노란색 배경으로 표시된다(일반 로그 줄과 구분됨)
- [ ] 긴 로그 줄(크롤링 원문, codex 프롬프트 등)이 패널 폭에 맞춰 자동 줄바꿈되고, 가로
      스크롤바가 생기지 않는다
- [ ] (실 codex exec 필요) `$imagegen`이 실패하는 상황을 재현했을 때, codex가 대체 이미지
      생성용 파이썬 스크립트 등 우회 프로그램을 작성하지 않고 이미지 없이 글만 완성하는지
      확인한다(work_dir 안에 낯선 `.py` 스크립트가 새로 생기지 않는지 확인)

### 14. 선택적 실 환경 스모크 테스트 (자동화 대상 아님)

`docs/ROADMAP.md` Task 011 원문이 "선택"으로 명시한 항목으로, 이번 Task에서 실행하지 않았다.
실제 네이버 계정·크롤링 API 키가 준비된 환경에서 필요 시 수행한다.

- [ ] (선택) Windows 로컬에서 실제 3개 서브프로세스(크롤링/`codex exec`/발행)를 사용해 1회 엔드투엔드 실행
- [ ] (선택) 실제 네이버 로그인 중 CAPTCHA/2FA가 발생했을 때 `NaverAutoWrite`의 5분 대기 동작 확인
