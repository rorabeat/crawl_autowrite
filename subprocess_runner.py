"""3개 외부 프로그램(크롤링/codex exec/NaverAutoWrite) 서브프로세스 공통 실행기.

docs/PRD.md 6절, docs/ROADMAP.md Task 005에서 실제 로직을 구현했다. 인자는 항상
리스트로만 받고(shell=True 금지), 표준출력/표준에러는 UTF-8로 실시간 스트리밍하며,
타임아웃 시 강제 종료한다. cwd/경로 정규화는 호출부 책임이다(이 함수는 정규화하지 않음).
"""

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

CANCELED_RC = -2

logger = logging.getLogger(__name__)

_SENTINEL = object()


def _reader_thread(stream, line_queue: queue.Queue) -> None:
    for line in stream:
        line_queue.put(line.rstrip("\n"))
    line_queue.put(_SENTINEL)


def run(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    on_output: Callable[[str], None] | None = None,
    input_text: str | None = None,
    tee_path: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """인자 리스트로 서브프로세스를 실행하고 종료 코드를 반환한다.

    표준출력/표준에러는 합쳐서 UTF-8로 실시간 스트리밍하며, 각 줄마다 on_output(있으면)과
    로거에 전달한다. timeout(초) 경과 시(출력이 없어 블로킹 중이더라도) 프로세스를 강제
    종료하고 -1을 반환한다.

    자식이 파이썬 프로세스인 경우 Windows에서 기본적으로 콘솔 코드페이지(cp949 등)로
    stdout을 인코딩해 UTF-8 디코딩과 어긋날 수 있으므로, PYTHONIOENCODING=utf-8을
    주입해 자식도 UTF-8로 출력하도록 강제한다(호출부가 이미 지정했다면 존중).

    input_text가 주어지면 stdin으로 흘려보낸 뒤 즉시 닫는다. Windows의 명령줄 길이
    제한(약 8191자)을 넘는 긴 프롬프트를 argv로 넘기면 "The command line is too long."
    오류가 나므로, 그런 경우 호출부는 인자 대신 이 옵션으로 전달해야 한다.

    tee_path가 주어지면 표준출력 전체를 그 파일에도 그대로 적는다. codex exec는
    --output-last-message로 최종 응답만 별도 파일에 저장해주지만, claude CLI에는
    대응하는 옵션이 없어(사용자 요청으로 추가한 claude 백엔드) 이 옵션으로 대체한다
    (전체 stdout이라 codex의 "최종 응답만"과는 의미가 정확히 같지는 않지만, 둘 다
    "1순위 결과 파일을 못 찾았을 때의 폴백"일 뿐이라 호출부 입장에서는 동일하게 쓸 수 있다).

    cancel_event가 주어지고 실행 도중 set()되면(사용자 요청: 진행 중인 작업 삭제 기능)
    타임아웃과 동일하게 프로세스를 강제 종료하되, 구분을 위해 CANCELED_RC(-2)를
    반환한다. timeout이 없어도 cancel_event가 있으면 0.5초마다 깨어나 취소 여부를
    확인한다(원래 timeout이 없으면 출력이 없는 동안 무한정 블로킹돼 취소를 확인할
    타이밍이 없었음).
    """
    effective_env = dict(env) if env is not None else dict(os.environ)
    effective_env.setdefault("PYTHONIOENCODING", "utf-8")

    # Windows의 CreateProcess는 cmd.exe와 달리 PATHEXT(.cmd/.bat 등)를 자동 탐색하지
    # 않으므로, npm 등으로 설치되어 확장자 없는 이름(예: "codex")이 실제로는 .cmd
    # 배치 파일인 경우 shell=False 상태로는 FileNotFoundError(WinError 2)가 발생한다.
    # shutil.which로 PATHEXT까지 반영한 실제 경로를 미리 찾아 치환한다.
    resolved = shutil.which(args[0])
    if resolved:
        args = [resolved, *args[1:]]

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=effective_env,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if input_text is not None:
        # stdin으로 넘기는 프롬프트는 codex exec에게 "명령"으로 전달되는 부분이므로, GUI
        # 로그(LogPanel.append_line)가 이를 알아보고 하이라이트할 수 있게 고정 마커를 붙여
        # 한 줄씩 로깅한다(사용자 요청). 프로세스 stdout과 뒤섞이지 않도록 stdin에 쓰기
        # 직전에 로깅한다.
        for prompt_line in input_text.splitlines() or [""]:
            logger.info("[CODEX 입력] %s", prompt_line)
        assert proc.stdin is not None
        proc.stdin.write(input_text)
        proc.stdin.close()

    line_queue: queue.Queue = queue.Queue()
    thread = threading.Thread(target=_reader_thread, args=(proc.stdout, line_queue), daemon=True)
    thread.start()

    tee_file = None
    if tee_path is not None:
        tee_path.parent.mkdir(parents=True, exist_ok=True)
        # 자식이 도중에 kill돼도(타임아웃 등) 그때까지의 출력이 남도록 줄 단위 버퍼링으로 연다.
        tee_file = open(tee_path, "w", encoding="utf-8", buffering=1)

    try:
        start = time.monotonic()
        while True:
            if timeout is not None:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    proc.kill()
                    proc.wait()
                    thread.join(timeout=1)
                    logger.warning("subprocess_runner.run 타임아웃: args=%s", args)
                    return -1
                wait_for = min(remaining, 0.5)
            elif cancel_event is not None:
                wait_for = 0.5
            else:
                wait_for = None

            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait()
                thread.join(timeout=1)
                logger.info("subprocess_runner.run 취소됨: args=%s", args)
                return CANCELED_RC

            try:
                line = line_queue.get(timeout=wait_for)
            except queue.Empty:
                continue

            if line is _SENTINEL:
                break

            if tee_file is not None:
                tee_file.write(line + "\n")
            if on_output:
                on_output(line)
            logger.info(line)

        thread.join(timeout=1)
        return proc.wait()
    finally:
        if tee_file is not None:
            tee_file.close()
