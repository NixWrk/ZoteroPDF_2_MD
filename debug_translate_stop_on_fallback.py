from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent


def _build_arg_parser(default_repo: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run TranslateGemma on a single EN HTML file and abort immediately "
            "if global fallback to per-segment translation is triggered."
        )
    )
    parser.add_argument("--html", required=True, help="Path to source EN .html file")
    parser.add_argument(
        "--model",
        default=str(default_repo / "models" / "translategemma-4b-it"),
        help="Model directory/ref (default: <repo>/models/translategemma-4b-it)",
    )
    parser.add_argument(
        "--target-lang",
        default="ru",
        help="Target language code (default: ru)",
    )
    parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=1800,
        help="max_chunk_chars for segment fallback path (default: 1800)",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=900,
        help="max_new_tokens for model generation (default: 900)",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Optional log file path (default: <repo>/logs/translate_stop_on_fallback_<timestamp>.log)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output translated HTML path (default: <source>.<lang>.html)",
    )
    return parser


def main() -> int:
    repo_root = _resolve_repo_root()
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from zoteropdf2md.translategemma import (  # pylint: disable=import-error
        TranslateGemmaConfig,
        TranslateGemmaTranslator,
        normalize_language_code,
        polish_html_document,
        translate_html_text_nodes,
        translated_html_output_path,
    )

    parser = _build_arg_parser(repo_root)
    args = parser.parse_args()

    html_path = Path(args.html).expanduser().resolve(strict=False)
    if not html_path.is_file():
        print(f"[ERROR] HTML not found: {html_path}")
        return 2

    if args.log:
        log_path = Path(args.log).expanduser().resolve(strict=False)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = repo_root / "logs" / f"translate_stop_on_fallback_{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_fp:
        def log_line(msg: str) -> None:
            print(msg, flush=True)
            log_fp.write(msg + "\n")
            log_fp.flush()

        cfg = TranslateGemmaConfig(
            model_ref=args.model,
            target_language_code=args.target_lang,
            max_chunk_chars=max(256, int(args.max_chunk_chars)),
            max_new_tokens=max(64, int(args.max_new_tokens)),
        )
        translator = TranslateGemmaTranslator(cfg, log=log_line)
        source = html_path.read_text(encoding="utf-8", errors="replace")

        llm_calls = 0

        def translate_with_progress(text: str) -> str:
            nonlocal llm_calls
            llm_calls += 1
            started = time.perf_counter()
            log_line(f"[LLM {llm_calls}] start chars={len(text)}")

            next_pct = 10
            last_tokens = 0

            def on_token_progress(generated: int, target: int) -> None:
                nonlocal next_pct, last_tokens
                if target <= 0:
                    return
                pct = int((generated * 100) / target)
                should_log = False
                if generated >= target:
                    should_log = True
                elif generated - last_tokens >= 64:
                    should_log = True
                elif pct >= next_pct:
                    should_log = True
                    while pct >= next_pct:
                        next_pct += 10
                if not should_log:
                    return
                last_tokens = generated
                log_line(f"[LLM {llm_calls}] tokens {generated}/{target} ({pct}%)")

            out = translator.translate_text(text, on_token_progress=on_token_progress)
            log_line(f"[LLM {llm_calls}] done in {time.perf_counter() - started:.2f}s")
            return out

        def on_batch_fallback(reason: str) -> None:
            raise RuntimeError(f"[FALLBACK] file={html_path.name} reason={reason}")

        log_line(f"[RUN] html={html_path}")
        log_line(f"[RUN] model={args.model}")
        log_line(f"[RUN] log={log_path}")

        try:
            translated_html, translated_segments = translate_html_text_nodes(
                source,
                translate_text=translate_with_progress,
                max_chunk_chars=max(256, int(args.max_chunk_chars)),
                on_batch_fallback=on_batch_fallback,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            log_line(message)
            if "[FALLBACK]" not in message:
                tb = traceback.format_exc()
                log_line(tb.rstrip())
            log_line(f"ABORT. LOG={log_path}")
            return 1

        language_code = normalize_language_code(args.target_lang)
        if args.output:
            output_path = Path(args.output).expanduser().resolve(strict=False)
        else:
            output_path = translated_html_output_path(html_path, language_code)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        polished_html = polish_html_document(translated_html)
        output_path.write_text(polished_html, encoding="utf-8")

        log_line(
            f"[OK] no fallback, translated_segments={translated_segments}, llm_calls={llm_calls}"
        )
        log_line(f"OUTPUT_HTML={output_path}")
        log_line(f"LOG={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
