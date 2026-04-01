from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

try:
    import psutil
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None


@dataclass(frozen=True)
class RunResult:
    command: list[str]
    exit_code: int


class MarkerRunner:
    def __init__(
        self,
        marker_cmd: str = "marker",
        marker_single_cmd: str = "marker_single",
    ) -> None:
        self._marker_cmd = marker_cmd
        self._marker_single_cmd = marker_single_cmd
        self._current_process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._tracked_pids: set[int] = set()

    def _track_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        with self._lock:
            self._tracked_pids.add(pid)

    def _tracked_snapshot(self) -> list[int]:
        with self._lock:
            return sorted(self._tracked_pids)

    def _register_child_pids(self, root_pid: int) -> None:
        if psutil is None or root_pid <= 0:
            return
        try:
            process = psutil.Process(root_pid)
            children = process.children(recursive=True)
        except Exception:
            return
        for child in children:
            self._track_pid(child.pid)

    @staticmethod
    def _kill_pid_tree(pid: int) -> bool:
        if pid <= 0:
            return True

        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            return False

        if result.returncode == 0:
            return True

        output = (result.stdout or "").lower()
        return (
            "not found" in output
            or "no running instance" in output
            or "not running" in output
        )

    def cleanup_spawned_processes(self, log: Callable[[str], None] | None = None) -> None:
        tracked = self._tracked_snapshot()
        if not tracked:
            return

        killed = 0
        remaining: list[int] = []
        for pid in tracked:
            if self._kill_pid_tree(pid):
                killed += 1
            else:
                remaining.append(pid)

        with self._lock:
            self._tracked_pids = set(remaining)

        if log is not None:
            log(
                "Runner cleanup: "
                f"tracked={len(tracked)}, killed={killed}, remaining={len(remaining)}"
            )
            if remaining:
                log(f"Runner cleanup remaining PIDs: {', '.join(str(pid) for pid in remaining)}")

    def terminate_current(self) -> None:
        with self._lock:
            proc = self._current_process
        if proc is not None and proc.poll() is None:
            self._track_pid(proc.pid)
            self._register_child_pids(proc.pid)
            with suppress(Exception):
                proc.terminate()
            with suppress(Exception):
                proc.wait(timeout=2)
            self._kill_pid_tree(proc.pid)
        self.cleanup_spawned_processes()

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
        self._track_pid(process.pid)

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
                self._register_child_pids(process.pid)
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
            self._register_child_pids(process.pid)
            with self._lock:
                self._current_process = None
                self._tracked_pids.discard(process.pid)

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
            self._marker_cmd,
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
            self._marker_single_cmd,
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
