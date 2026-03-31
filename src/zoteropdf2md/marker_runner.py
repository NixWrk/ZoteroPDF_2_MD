from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


@dataclass(frozen=True)
class RunResult:
    command: list[str]
    exit_code: int


class MarkerRunner:
    def __init__(self) -> None:
        self._current_process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def terminate_current(self) -> None:
        with self._lock:
            proc = self._current_process
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _run(
        self,
        command: list[str],
        env: dict[str, str],
        log: callable,
    ) -> RunResult:
        run_started_at = perf_counter()
        log("$ " + " ".join(f'"{part}"' if " " in part else part for part in command))
        spawn_started_at = perf_counter()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,
        )
        log(f"[timer] runner.spawn_process: {perf_counter() - spawn_started_at:.2f}s")
        log(f"Runner process started: pid={process.pid}")

        with self._lock:
            self._current_process = process

        first_output_at: float | None = None
        last_output_at: float | None = None
        max_output_gap = 0.0
        line_count = 0
        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(10):
                elapsed = perf_counter() - run_started_at
                if first_output_at is None:
                    log(f"[timer] runner.waiting_first_output: {elapsed:.2f}s")
                else:
                    since_last_output = 0.0 if last_output_at is None else perf_counter() - last_output_at
                    log(
                        "[timer] runner.process_alive: "
                        f"elapsed={elapsed:.2f}s, since_last_output={since_last_output:.2f}s"
                    )

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()

        try:
            assert process.stdout is not None
            line_buffer: list[str] = []
            while True:
                ch = process.stdout.read(1)
                if ch == "":
                    break

                now = perf_counter()
                if first_output_at is None:
                    first_output_at = now
                    log(f"[timer] runner.first_output: {first_output_at - run_started_at:.2f}s")

                if ch in ("\n", "\r"):
                    if line_buffer:
                        line = "".join(line_buffer)
                        if last_output_at is not None:
                            max_output_gap = max(max_output_gap, now - last_output_at)
                        last_output_at = now
                        line_count += 1
                        log(line)
                        line_buffer = []
                else:
                    line_buffer.append(ch)
            if line_buffer:
                now = perf_counter()
                line = "".join(line_buffer)
                if last_output_at is not None:
                    max_output_gap = max(max_output_gap, now - last_output_at)
                last_output_at = now
                line_count += 1
                log(line)

            wait_started_at = perf_counter()
            exit_code = process.wait()
            log(f"[timer] runner.wait_after_stdout_eof: {perf_counter() - wait_started_at:.2f}s")
            if first_output_at is None:
                log("[timer] runner.first_output: no output before process exit")
            if last_output_at is not None:
                log(f"[timer] runner.last_output: {last_output_at - run_started_at:.2f}s")
            log(f"[timer] runner.total: {perf_counter() - run_started_at:.2f}s")
            log(
                "Runner diagnostics: "
                f"exit_code={exit_code}, "
                f"stdout_lines={line_count}, "
                f"max_gap_between_lines={max_output_gap:.2f}s"
            )
            return RunResult(command=command, exit_code=exit_code)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=0.2)
            with self._lock:
                self._current_process = None

    def run_batch(
        self,
        input_dir: Path,
        output_dir: Path,
        skip_existing: bool,
        disable_multiprocessing: bool,
        output_format: str,
        env: dict[str, str],
        log: callable,
    ) -> RunResult:
        cmd = [
            "marker",
            str(input_dir),
            "--output_dir",
            str(output_dir),
            "--output_format",
            output_format,
            "--drop_repeated_text",
            "--drop_repeated_table_text",
        ]
        if skip_existing:
            cmd.append("--skip_existing")
        if disable_multiprocessing:
            cmd.append("--disable_multiprocessing")
        return self._run(cmd, env, log)

    def run_single(
        self,
        pdf_path: Path,
        output_dir: Path,
        output_format: str,
        env: dict[str, str],
        log: callable,
    ) -> RunResult:
        cmd = [
            "marker_single",
            str(pdf_path),
            "--output_dir",
            str(output_dir),
            "--output_format",
            output_format,
            "--drop_repeated_text",
            "--drop_repeated_table_text",
            "--PdfProvider_pdftext_workers",
            "1",
        ]
        return self._run(cmd, env, log)
