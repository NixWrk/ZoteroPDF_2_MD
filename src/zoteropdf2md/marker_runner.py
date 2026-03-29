from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


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
        log("$ " + " ".join(f'"{part}"' if " " in part else part for part in command))
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

        with self._lock:
            self._current_process = process

        try:
            assert process.stdout is not None
            line_buffer: list[str] = []
            while True:
                ch = process.stdout.read(1)
                if ch == "":
                    break
                if ch in ("\n", "\r"):
                    if line_buffer:
                        log("".join(line_buffer))
                        line_buffer = []
                else:
                    line_buffer.append(ch)
            if line_buffer:
                log("".join(line_buffer))

            exit_code = process.wait()
            return RunResult(command=command, exit_code=exit_code)
        finally:
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
            "--PdfProvider_pdftext_workers",
            "1",
        ]
        return self._run(cmd, env, log)
