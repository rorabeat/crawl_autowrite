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
import tempfile
from collections.abc import Callable
from pathlib import Path

CANCELED_RC = -2
TIMEOUT_RC = -1

logger = logging.getLogger(__name__)

_SENTINEL = object()


_spawn_lock = threading.Lock()


def external_popen(*args, **kwargs):
    """Do not pass the GUI's private Windows DLL directory to external apps."""
    if os.name != "nt":
        return subprocess.Popen(*args, **kwargs)
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    with _spawn_lock:
        size = kernel.GetDllDirectoryW(0, None)
        previous = ctypes.create_unicode_buffer(size + 1)
        kernel.GetDllDirectoryW(len(previous), previous)
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return subprocess.Popen(*args, **kwargs)
        finally:
            if not kernel.SetDllDirectoryW(previous.value or None):
                raise ctypes.WinError(ctypes.get_last_error())


def _reader_thread(stream, line_queue: queue.Queue, is_stderr: bool = False) -> None:
    try:
        for line in stream:
            line_queue.put((is_stderr, line.rstrip("\n")))
    finally:
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
    log_output: bool | None = None,
    on_input_sent: Callable[[], None] | None = None,
    on_diagnostic_line: Callable[[str, bool], None] | None = None,
) -> int:
    """인자 리스트로 서브프로세스를 실행하고 종료 코드를 반환한다.

    표준출력/표준에러는 합쳐서 UTF-8로 실시간 스트리밍하며, 각 줄마다 on_output(있으면)과
    로거에 전달한다. timeout(초) 경과 시(출력이 없어 블로킹 중이더라도) 프로세스를 강제
    종료하고 -1을 반환한다.

    on_diagnostic_line이 있으면 stdout과 stderr를 별도로 읽고 (줄, stderr 여부)를
    전달한다. 호출자가 필요한 진단 필드만 추출할 수 있도록 log_output과 별도로 동작한다.

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
    # AI output and the read-only style crawler can contain entire source posts.
    # Keep their result artifacts, but do not forward raw text into application logs.
    if log_output is None:
        log_output = input_text is None and not any(
            Path(str(arg)).name == 'crawl_worker.py' for arg in args)
    effective_env = dict(env) if env is not None else dict(os.environ)
    effective_env.setdefault("PYTHONIOENCODING", "utf-8")
    cancel_path = None
    if effective_env.get("NAVER_FLUSH_UNKNOWN"):
        fd, cancel_path = tempfile.mkstemp(prefix="naver-cancel-")
        os.close(fd)
        os.unlink(cancel_path)
        effective_env["NAVER_CANCEL_FILE"] = cancel_path
        effective_env["NAVER_CANCEL_ACK"] = cancel_path + '.ack'

    # Windows의 CreateProcess는 cmd.exe와 달리 PATHEXT(.cmd/.bat 등)를 자동 탐색하지
    # 않으므로, npm 등으로 설치되어 확장자 없는 이름(예: "codex")이 실제로는 .cmd
    # 배치 파일인 경우 shell=False 상태로는 FileNotFoundError(WinError 2)가 발생한다.
    # shutil.which로 PATHEXT까지 반영한 실제 경로를 미리 찾아 치환한다.
    resolved = shutil.which(args[0])
    if resolved:
        args = [resolved, *args[1:]]

    proc = external_popen(
        args,
        cwd=cwd,
        env=effective_env,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if on_diagnostic_line is not None else subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    line_queue: queue.Queue = queue.Queue()
    readers = [threading.Thread(target=_reader_thread, args=(proc.stdout, line_queue), daemon=True)]
    if proc.stderr is not None:
        readers.append(threading.Thread(target=_reader_thread, args=(proc.stderr, line_queue, True), daemon=True))
    for reader in readers:
        reader.start()

    if input_text is not None:
        # Prompts can contain full crawled/generated posts. Log only their size.
        assert proc.stdin is not None
        try:
            proc.stdin.write(input_text)
            proc.stdin.close()
        except BrokenPipeError:
            if on_diagnostic_line is None:
                raise
        else:
            logger.info("AI 입력 전달: %d자 (본문 로그 생략)", len(input_text))
            if on_input_sent is not None:
                on_input_sent()

    tee_file = None
    if tee_path is not None:
        tee_path.parent.mkdir(parents=True, exist_ok=True)
        # 자식이 도중에 kill돼도(타임아웃 등) 그때까지의 출력이 남도록 줄 단위 버퍼링으로 연다.
        tee_file = open(tee_path, "w", encoding="utf-8", buffering=1)

    try:
        start = time.monotonic()
        remaining_readers = len(readers)
        while True:
            if timeout is not None:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    if cancel_path:
                        Path(effective_env["NAVER_FLUSH_UNKNOWN"]).touch()
                        logger.error("시간 초과로 프로필 저장 상태가 불명입니다.")
                    proc.kill()
                    proc.wait()
                    if log_output:
                        logger.warning("subprocess_runner.run 타임아웃: args=%s", args)
                    else:
                        logger.warning("외부 작업 시간 초과 (%s초)", timeout)
                    return TIMEOUT_RC
                wait_for = min(remaining, 0.5)
            elif cancel_event is not None:
                wait_for = 0.5
            else:
                wait_for = None

            if cancel_event is not None and cancel_event.is_set():
                if cancel_path:
                    Path(cancel_path).touch()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        Path(effective_env["NAVER_FLUSH_UNKNOWN"]).touch()
                        logger.error("브라우저 종료와 프로필 저장 상태가 불명입니다. 프로필 재사용이 차단됩니다.")
                        proc.kill()
                    else:
                        if not Path(cancel_path + '.ack').exists():
                            Path(effective_env["NAVER_FLUSH_UNKNOWN"]).touch()
                            logger.error("브라우저 정상 종료 확인이 없어 프로필 재사용을 차단합니다.")
                else:
                    proc.kill()
                proc.wait()
                logger.info("외부 작업 취소됨")
                return CANCELED_RC

            if remaining_readers == 0:
                try:
                    proc.wait(timeout=wait_for)
                except subprocess.TimeoutExpired:
                    continue
                break

            try:
                output = line_queue.get(timeout=wait_for)
            except queue.Empty:
                continue

            if output is _SENTINEL:
                remaining_readers -= 1
                continue

            is_stderr, line = output
            if on_diagnostic_line is not None:
                on_diagnostic_line(line, is_stderr)

            if tee_file is not None:
                tee_file.write(line + "\n")
            if log_output:
                if on_output:
                    on_output(line)
                logger.info(line)

        rc = proc.wait()
        if not log_output:
            logger.log(logging.INFO if rc == 0 else logging.ERROR,
                       "외부 작업 종료: rc=%d (본문 출력 생략)", rc)
        if cancel_path and not Path(cancel_path + '.ack').exists():
            Path(effective_env["NAVER_FLUSH_UNKNOWN"]).touch()
            logger.error("브라우저 정상 종료 확인이 없어 프로필 재사용을 차단합니다.")
        return rc
    finally:
        if cancel_path:
            Path(cancel_path).unlink(missing_ok=True)
            Path(cancel_path + '.ack').unlink(missing_ok=True)
        if tee_file is not None:
            tee_file.close()
