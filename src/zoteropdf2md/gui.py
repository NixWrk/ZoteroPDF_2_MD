from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from time import perf_counter
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .export_modes import ExportMode, all_export_mode_specs, get_export_mode_spec, parse_export_mode
from .history import find_processed_elsewhere
from .marker_runner import MarkerRunner
from .output_state import detect_existing_results, normalize_source_path
from .paths import detect_default_zotero_data_dir, discover_zotero_profiles
from .pipeline import (
    PdfCandidate,
    PipelineOptions,
    discover_collection_pdfs,
    retry_pending_zotero_exports,
    run_pipeline,
)
from .runtime_temp import cleanup_runtime_temp_root, runtime_temp_root
from .staging import DEFAULT_MAX_BASE_LEN, MIN_BASE_LEN
from .translategemma import (
    DEFAULT_TRANSLATEGEMMA_MODEL,
    DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE,
    OFFICIAL_TRANSLATEGEMMA_MODEL_REPO,
    TRANSLATEGEMMA_LANGUAGE_CHOICES,
    language_name_for_code,
)
from .zotero import ZoteroRepository


def _derive_marker_single_cmd(marker_cmd: str) -> str:
    """Given a marker executable path/name, return the corresponding marker_single path."""
    p = Path(marker_cmd)
    # If it's just a bare name like "marker" → "marker_single"
    if p.parent == Path(".") and not marker_cmd.startswith(("/", ".", "\\")):
        stem = p.stem  # handles "marker" or "marker.exe"
        suffix = p.suffix
        return f"{stem}_single{suffix}" if stem == "marker" else f"{stem}_single{suffix}"
    # Full path: replace the filename
    stem = p.stem
    suffix = p.suffix
    single_name = f"{stem}_single{suffix}" if stem == "marker" else f"{stem}_single{suffix}"
    return str(p.parent / single_name)


class ZoteroPdfGui:
    @staticmethod
    def _default_output_dir() -> Path:
        # Use repo-local output folder by default to avoid C:\\Windows\\System32 when cwd is system dir.
        return (Path(__file__).resolve().parents[2] / "md_output").resolve(strict=False)

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ZoteroPDF_2_MD")
        self.root.geometry("1180x860")

        default_zotero = detect_default_zotero_data_dir()

        self.zotero_data_dir = tk.StringVar(value=str(default_zotero) if default_zotero else "")
        self.output_dir = tk.StringVar(value=str(self._default_output_dir()))
        self.model_cache_dir = tk.StringVar(value="")
        self.collection_display = tk.StringVar(value="")
        self.profile_display = tk.StringVar(value="")
        self.pdf_status = tk.StringVar(value="No PDF scan yet")

        self.include_subcollections = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=True)
        self.use_cuda = tk.BooleanVar(value=True)
        self.disable_batch_multiprocessing = tk.BooleanVar(value=False)
        self.export_mode_vars: dict[str, tk.BooleanVar] = {
            spec.mode.value: tk.BooleanVar(value=(spec.mode == ExportMode.CLASSIC))
            for spec in all_export_mode_specs()
        }
        self.translate_html_with_gemma = tk.BooleanVar(value=False)
        self.translation_target_language_code = tk.StringVar(
            value=DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE
        )
        self.translation_label = tk.StringVar(value="")
        self.translation_model_ref = tk.StringVar(value=DEFAULT_TRANSLATEGEMMA_MODEL)
        self.translation_hf_token = tk.StringVar(value="")

        self.max_base_len = tk.StringVar(value=str(DEFAULT_MAX_BASE_LEN))
        self.marker_executable = tk.StringVar(value="marker")

        self.collection_lookup: dict[str, str] = {}
        self.profile_lookup: dict[str, str] = {}
        self.pdf_candidates: list[PdfCandidate] = []
        self.pdf_selection_vars: dict[str, tk.BooleanVar] = {}

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.runner = MarkerRunner()

        self._build_ui()
        self._refresh_translation_label()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_profiles(initial=True)
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        frame = tk.Frame(self.root, padx=10, pady=10)
        frame.pack(fill="both", expand=True)

        row = 0
        tk.Label(frame, text="Detected Zotero profile").grid(row=row, column=0, sticky="w")
        row += 1
        self.profile_combo = ttk.Combobox(
            frame,
            textvariable=self.profile_display,
            width=108,
            state="readonly",
        )
        self.profile_combo.grid(row=row, column=0, sticky="we")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        tk.Button(frame, text="Refresh profiles", command=self._refresh_profiles).grid(row=row, column=1, padx=6)

        row += 1
        tk.Label(frame, text="Zotero data folder (.../zotero)").grid(row=row, column=0, sticky="w")
        row += 1
        tk.Entry(frame, textvariable=self.zotero_data_dir, width=115).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_zotero_data_dir).grid(row=row, column=1, padx=6)
        tk.Button(frame, text="Load collections", command=self._load_collections).grid(row=row, column=2)

        row += 1
        tk.Label(frame, text="Collection").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        self.collection_combo = ttk.Combobox(
            frame,
            textvariable=self.collection_display,
            width=108,
            state="readonly",
        )
        self.collection_combo.grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Scan PDFs", command=self._scan_pdfs).grid(row=row, column=1, padx=6)

        row += 1
        tk.Label(frame, text="Output folder").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        tk.Entry(frame, textvariable=self.output_dir, width=115).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_output_dir).grid(row=row, column=1, padx=6)

        row += 1
        tk.Label(frame, text="Model cache folder (optional)").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        tk.Entry(frame, textvariable=self.model_cache_dir, width=115).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_model_cache_dir).grid(row=row, column=1, padx=6)

        row += 1
        tk.Label(frame, text="marker executable (name or full path)").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        tk.Entry(frame, textvariable=self.marker_executable, width=115).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_marker_executable).grid(row=row, column=1, padx=6)

        row += 1
        pdf_head = tk.Frame(frame)
        pdf_head.grid(row=row, column=0, columnspan=3, sticky="we", pady=(12, 4))
        tk.Label(pdf_head, text="PDF Selection (checkboxes)").pack(side="left")
        tk.Button(pdf_head, text="Select all", command=self._select_all_pdfs).pack(side="left", padx=(10, 0))
        tk.Button(pdf_head, text="Select none", command=self._select_no_pdfs).pack(side="left", padx=(6, 0))
        tk.Label(pdf_head, textvariable=self.pdf_status).pack(side="left", padx=(12, 0))

        row += 1
        pdf_holder = tk.Frame(frame, bd=1, relief="sunken")
        pdf_holder.grid(row=row, column=0, columnspan=3, sticky="nsew")

        self.pdf_canvas = tk.Canvas(pdf_holder, height=220)
        self.pdf_canvas.pack(side="left", fill="both", expand=True)

        pdf_scrollbar = tk.Scrollbar(pdf_holder, orient="vertical", command=self.pdf_canvas.yview)
        pdf_scrollbar.pack(side="right", fill="y")
        self.pdf_canvas.configure(yscrollcommand=pdf_scrollbar.set)

        self.pdf_list_frame = tk.Frame(self.pdf_canvas)
        self.pdf_list_window = self.pdf_canvas.create_window((0, 0), window=self.pdf_list_frame, anchor="nw")

        self.pdf_list_frame.bind(
            "<Configure>",
            lambda _: self.pdf_canvas.configure(scrollregion=self.pdf_canvas.bbox("all")),
        )
        self.pdf_canvas.bind(
            "<Configure>",
            lambda event: self.pdf_canvas.itemconfigure(self.pdf_list_window, width=event.width),
        )

        row += 1
        options = tk.Frame(frame)
        options.grid(row=row, column=0, columnspan=3, sticky="w", pady=(12, 8))

        tk.Checkbutton(options, text="Include subcollections", variable=self.include_subcollections).pack(anchor="w")
        tk.Checkbutton(options, text="Skip existing outputs", variable=self.skip_existing).pack(anchor="w")
        tk.Checkbutton(options, text="Use CUDA (TORCH_DEVICE=cuda)", variable=self.use_cuda).pack(anchor="w")
        tk.Checkbutton(options, text="Disable marker batch multiprocessing", variable=self.disable_batch_multiprocessing).pack(anchor="w")

        mode_frame = tk.Frame(options)
        mode_frame.pack(anchor="w", pady=(6, 0))
        tk.Label(mode_frame, text="Export modes:").pack(anchor="w")
        for spec in all_export_mode_specs():
            tk.Checkbutton(
                mode_frame,
                text=spec.label,
                variable=self.export_mode_vars[spec.mode.value],
            ).pack(anchor="w")

        translation_frame = tk.LabelFrame(options, text="TranslateGemma (HTML outputs)")
        translation_frame.pack(anchor="w", fill="x", pady=(8, 0))
        tk.Checkbutton(
            translation_frame,
            text="Translate HTML outputs before saving/attaching",
            variable=self.translate_html_with_gemma,
        ).pack(anchor="w")

        translation_lang_row = tk.Frame(translation_frame)
        translation_lang_row.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(translation_lang_row, textvariable=self.translation_label).pack(side="left")
        tk.Button(
            translation_lang_row,
            text="Choose language",
            command=self._pick_translation_language,
        ).pack(side="left", padx=(8, 0))

        translation_model_row = tk.Frame(translation_frame)
        translation_model_row.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(translation_model_row, text="Model path or HF repo:").pack(side="left")
        tk.Entry(
            translation_model_row,
            textvariable=self.translation_model_ref,
            width=68,
        ).pack(side="left", padx=(6, 0))

        translation_token_row = tk.Frame(translation_frame)
        translation_token_row.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(translation_token_row, text="HF token (for gated model):").pack(side="left")
        tk.Entry(
            translation_token_row,
            textvariable=self.translation_hf_token,
            width=52,
            show="*",
        ).pack(side="left", padx=(6, 0))

        limits = tk.Frame(options)
        limits.pack(anchor="w", pady=(6, 0))
        tk.Label(limits, text="Max source base-name length:").pack(side="left")
        tk.Entry(limits, textvariable=self.max_base_len, width=8).pack(side="left", padx=(6, 0))

        row += 1
        actions = tk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 10))
        tk.Button(actions, text="Run", command=self._run).pack(side="left")
        tk.Button(actions, text="Stop", command=self._stop).pack(side="left", padx=(8, 0))
        tk.Button(actions, text="Retry pending Zotero", command=self._retry_pending_zotero).pack(side="left", padx=(8, 0))
        tk.Button(actions, text="Copy selected log", command=self._copy_log_selection).pack(side="left", padx=(14, 0))
        tk.Button(actions, text="Copy all log", command=self._copy_log_all).pack(side="left", padx=(6, 0))

        row += 1
        self.log = scrolledtext.ScrolledText(frame, width=150, height=22)
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew")
        self.log.bind("<Control-c>", self._on_log_ctrl_c)
        self.log.bind("<Control-C>", self._on_log_ctrl_c)
        self.log.bind("<Control-a>", self._on_log_ctrl_a)
        self.log.bind("<Control-A>", self._on_log_ctrl_a)
        self.log.bind("<Button-3>", self._show_log_context_menu)

        self.log_context_menu = tk.Menu(self.root, tearoff=0)
        self.log_context_menu.add_command(label="Copy selected", command=self._copy_log_selection)
        self.log_context_menu.add_command(label="Copy all", command=self._copy_log_all)
        self.log_context_menu.add_separator()
        self.log_context_menu.add_command(label="Select all", command=self._select_all_log_text)

        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(row - 4, weight=1)
        frame.grid_rowconfigure(row, weight=1)

    def _pick_zotero_data_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select Zotero data directory")
        if folder:
            self.zotero_data_dir.set(folder)
            if self.profile_display.get().strip():
                self.profile_display.set("")
            self._log(f"Manual Zotero data folder selected: {folder}")

    def _pick_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select output directory")
        if folder:
            self.output_dir.set(folder)

    def _pick_model_cache_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select model cache directory")
        if folder:
            self.model_cache_dir.set(folder)

    def _pick_marker_executable(self) -> None:
        path = filedialog.askopenfilename(
            title="Select marker executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.marker_executable.set(path)

    def _log(self, text: str) -> None:
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)

    def _queue_log(self, text: str) -> None:
        self.log_queue.put(text)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._log(line)
        self.root.after(100, self._drain_log_queue)

    def _copy_to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def _selected_log_text(self) -> str:
        try:
            return self.log.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def _copy_log_selection(self, notify_if_empty: bool = True) -> None:
        text = self._selected_log_text()
        if not text:
            if notify_if_empty:
                messagebox.showinfo("Copy log", "Select text in the log first.")
            return
        self._copy_to_clipboard(text)
        self._log("Log: selected text copied to clipboard.")

    def _copy_log_all(self) -> None:
        text = self.log.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("Copy log", "Log is empty.")
            return
        self._copy_to_clipboard(text)
        self._log("Log: all text copied to clipboard.")

    def _select_all_log_text(self) -> None:
        self.log.focus_set()
        self.log.tag_add("sel", "1.0", "end-1c")
        self.log.mark_set("insert", "1.0")
        self.log.see("insert")

    def _on_log_ctrl_c(self, _event: tk.Event) -> str:
        self._copy_log_selection(notify_if_empty=False)
        return "break"

    def _on_log_ctrl_a(self, _event: tk.Event) -> str:
        self._select_all_log_text()
        return "break"

    def _show_log_context_menu(self, event: tk.Event) -> str:
        self.log.focus_set()
        try:
            self.log_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.log_context_menu.grab_release()
        return "break"

    def _refresh_translation_label(self) -> None:
        try:
            language_name = language_name_for_code(self.translation_target_language_code.get())
        except Exception:
            language_name = self.translation_target_language_code.get().strip() or "unknown"
        self.translation_label.set(f"Target language: {language_name}")

    def _pick_translation_language(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("TranslateGemma language")
        dialog.geometry("420x290")
        dialog.transient(self.root)
        dialog.grab_set()

        selected_code = tk.StringVar(value=self.translation_target_language_code.get())

        tk.Label(
            dialog,
            text=(
                "Choose language for translated HTML output.\n"
                "Translated files will be saved as '<name>.<lang>.html'."
            ),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 8))

        langs = tk.Frame(dialog)
        langs.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        for code, name in TRANSLATEGEMMA_LANGUAGE_CHOICES:
            tk.Radiobutton(
                langs,
                text=f"{name} ({code})",
                value=code,
                variable=selected_code,
                anchor="w",
                justify="left",
            ).pack(anchor="w")

        actions = tk.Frame(dialog)
        actions.pack(fill="x", padx=12, pady=(0, 12))

        def apply_selection() -> None:
            self.translation_target_language_code.set(selected_code.get().strip().lower())
            self._refresh_translation_label()
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        tk.Button(actions, text="Apply", command=apply_selection).pack(side="left")
        tk.Button(actions, text="Cancel", command=on_cancel).pack(side="left", padx=(8, 0))
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        dialog.wait_window()

    def _selected_export_modes(self) -> list[ExportMode]:
        return [
            parse_export_mode(key)
            for key, var in self.export_mode_vars.items()
            if var.get()
        ]

    def _current_artifact_extension(self) -> str:
        modes = self._selected_export_modes()
        if not modes:
            return ".md"
        return get_export_mode_spec(modes[0]).artifact_extension

    def _resolve_output_dir(self) -> Path:
        raw = self.output_dir.get().strip()
        if not raw:
            raise ValueError("Set output directory first.")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self._default_output_dir().parent / candidate
        resolved = candidate.resolve(strict=False)
        self.output_dir.set(str(resolved))
        return resolved

    def _runtime_temp_root(self) -> Path:
        return runtime_temp_root(self._resolve_output_dir())

    def _on_profile_selected(self, _event: tk.Event | None = None) -> None:
        selected = self.profile_display.get().strip()
        path = self.profile_lookup.get(selected)
        if not path:
            return
        self.zotero_data_dir.set(path)
        self._log(f"Profile selected: {selected}")

    def _refresh_profiles(self, initial: bool = False) -> None:
        previous_path = self.zotero_data_dir.get().strip()
        profiles = discover_zotero_profiles()

        self.profile_lookup.clear()
        displays: list[str] = []
        for profile in profiles:
            display = profile.display
            self.profile_lookup[display] = str(profile.zotero_data_dir)
            displays.append(display)

        self.profile_combo["values"] = displays
        if not displays:
            self.profile_display.set("")
            if initial:
                self._log("No Zotero profiles auto-detected. Manual path is still available.")
            else:
                self._log("Profile refresh: no Zotero profiles auto-detected.")
            return

        preferred_display = displays[0]
        for display in displays:
            if self.profile_lookup[display] == previous_path:
                preferred_display = display
                break

        self.profile_display.set(preferred_display)
        selected_path = self.profile_lookup[preferred_display]
        if not previous_path or previous_path != selected_path:
            self.zotero_data_dir.set(selected_path)

        if initial:
            self._log(f"Auto-detected Zotero profiles: {len(displays)}. Using: {preferred_display}")
        else:
            self._log(f"Profile refresh: found {len(displays)} profiles. Using: {preferred_display}")

    def _load_collections(self) -> None:
        zotero_data_dir = self.zotero_data_dir.get().strip()
        if not zotero_data_dir:
            messagebox.showerror("Error", "Set Zotero data directory first.")
            return

        temp_root: Path | None = None
        try:
            temp_root = self._runtime_temp_root()
            repo = ZoteroRepository(Path(zotero_data_dir), snapshot_temp_root=temp_root)
            collections = repo.get_collections()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to load collections: {exc}")
            return
        finally:
            if temp_root is not None:
                cleanup_runtime_temp_root(temp_root)

        self.collection_lookup.clear()
        displays = []
        for collection in collections:
            display = f"{collection.full_name} [{collection.key}]"
            self.collection_lookup[display] = collection.key
            displays.append(display)

        self.collection_combo["values"] = displays
        if displays:
            self.collection_display.set(displays[0])

        self._log(f"Loaded collections: {len(displays)}")

    def _scan_pdfs(self) -> None:
        if not self._scan_pdfs_internal(show_errors=True):
            return

    def _scan_pdfs_internal(self, show_errors: bool) -> bool:
        scan_started_at = perf_counter()
        display = self.collection_display.get().strip()
        collection_key = self.collection_lookup.get(display)
        if not collection_key:
            if show_errors:
                messagebox.showerror("Error", "Select a collection first.")
            return False

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            if show_errors:
                messagebox.showerror("Error", "Set output folder first.")
            return False

        try:
            resolved_output_dir = self._resolve_output_dir()
            temp_root = runtime_temp_root(resolved_output_dir)
            discover_started_at = perf_counter()
            discovery = discover_collection_pdfs(
                zotero_data_dir=self.zotero_data_dir.get().strip(),
                collection_key=collection_key,
                include_subcollections=self.include_subcollections.get(),
                output_dir=str(resolved_output_dir),
                artifact_extension=self._current_artifact_extension(),
                temp_root=temp_root,
                log=self._log,
            )
            self._log(f"[timer] gui.scan.discover_collection_pdfs: {perf_counter() - discover_started_at:.2f}s")
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Error", f"Failed to scan PDFs: {exc}")
            return False
        finally:
            if "temp_root" in locals():
                cleanup_runtime_temp_root(temp_root)

        rebuild_started_at = perf_counter()
        self.pdf_candidates = sorted(
            discovery.candidates,
            key=lambda candidate: (
                candidate.resolved_attachment.source_pdf_path.name.lower(),
                str(candidate.resolved_attachment.source_pdf_path).lower(),
            ),
        )
        self._rebuild_pdf_checkboxes()
        self._log(f"[timer] gui.scan.rebuild_checkboxes: {perf_counter() - rebuild_started_at:.2f}s")

        done_in_output = sum(1 for c in discovery.candidates if c.already_in_output)
        self._log(
            f"PDF scan: resolved={len(discovery.candidates)}, "
            f"already_in_output={done_in_output}, unresolved={discovery.unresolved_total}"
        )
        self._log(f"[timer] gui.scan.total: {perf_counter() - scan_started_at:.2f}s")
        return True

    def _rebuild_pdf_checkboxes(self) -> None:
        for child in self.pdf_list_frame.winfo_children():
            child.destroy()

        self.pdf_selection_vars.clear()

        for idx, candidate in enumerate(self.pdf_candidates, start=1):
            source_pdf_path = candidate.resolved_attachment.source_pdf_path
            normalized = normalize_source_path(source_pdf_path)
            var = tk.BooleanVar(value=True)
            self.pdf_selection_vars[normalized] = var

            suffix = " [already in output]" if candidate.already_in_output else ""
            text = f"{idx}. {source_pdf_path}{suffix}"
            chk = tk.Checkbutton(self.pdf_list_frame, text=text, variable=var, anchor="w", justify="left")
            if candidate.already_in_output:
                chk.configure(fg="#666666")
            chk.pack(anchor="w", fill="x")

        selected = sum(1 for var in self.pdf_selection_vars.values() if var.get())
        self.pdf_status.set(f"PDFs: {len(self.pdf_candidates)} | selected: {selected}")

    def _select_all_pdfs(self) -> None:
        for var in self.pdf_selection_vars.values():
            var.set(True)
        self.pdf_status.set(f"PDFs: {len(self.pdf_candidates)} | selected: {len(self.pdf_selection_vars)}")

    def _select_no_pdfs(self) -> None:
        for var in self.pdf_selection_vars.values():
            var.set(False)
        self.pdf_status.set(f"PDFs: {len(self.pdf_candidates)} | selected: 0")

    def _selected_pdf_paths(self) -> list[str]:
        selected: list[str] = []
        for candidate in self.pdf_candidates:
            source_pdf_path = candidate.resolved_attachment.source_pdf_path
            normalized = normalize_source_path(source_pdf_path)
            var = self.pdf_selection_vars.get(normalized)
            if var is not None and var.get():
                selected.append(str(source_pdf_path))
        return selected

    def _prompt_existing_in_output_selection(self, existing_paths: list[Path], output_dir_path: Path) -> set[str] | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Files already in output folder")
        dialog.geometry("1080x640")
        dialog.transient(self.root)
        dialog.grab_set()

        result: dict[str, set[str] | None] = {"skip_norms": None}

        header = tk.Label(
            dialog,
            text=(
                f"Found {len(existing_paths)} selected PDFs with existing results in:\n"
                f"{output_dir_path}\n\n"
                "Select files with checkboxes, then choose action:\n"
                "1) Skip selected (others will be reprocessed)\n"
                "2) Reprocess selected (others will be skipped)"
            ),
            justify="left",
            anchor="w",
        )
        header.pack(fill="x", padx=10, pady=(10, 6))

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=10, pady=(0, 6))
        selected_count_var = tk.StringVar(value="")

        canvas_holder = tk.Frame(dialog, bd=1, relief="sunken")
        canvas_holder.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        canvas = tk.Canvas(canvas_holder)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(canvas_holder, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        list_frame = tk.Frame(canvas)
        list_window = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        list_frame.bind(
            "<Configure>",
            lambda _: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(list_window, width=event.width),
        )

        path_vars: dict[str, tk.BooleanVar] = {}
        ordered_paths = sorted(existing_paths, key=lambda p: (p.name.lower(), str(p).lower()))

        def update_selected_count() -> None:
            selected_count = sum(1 for v in path_vars.values() if v.get())
            selected_count_var.set(f"Selected: {selected_count} / {len(path_vars)}")

        for idx, path in enumerate(ordered_paths, start=1):
            norm = normalize_source_path(path)
            var = tk.BooleanVar(value=True)
            path_vars[norm] = var
            chk = tk.Checkbutton(
                list_frame,
                text=f"{idx}. {path}",
                variable=var,
                anchor="w",
                justify="left",
                command=update_selected_count,
            )
            chk.pack(anchor="w", fill="x")

        def select_all() -> None:
            for var in path_vars.values():
                var.set(True)
            update_selected_count()

        def select_none() -> None:
            for var in path_vars.values():
                var.set(False)
            update_selected_count()

        tk.Button(controls, text="Select all", command=select_all).pack(side="left")
        tk.Button(controls, text="Select none", command=select_none).pack(side="left", padx=(6, 0))
        tk.Label(controls, textvariable=selected_count_var).pack(side="left", padx=(12, 0))
        update_selected_count()

        actions = tk.Frame(dialog)
        actions.pack(fill="x", padx=10, pady=(0, 10))

        def apply_skip_selected() -> None:
            result["skip_norms"] = {norm for norm, var in path_vars.items() if var.get()}
            dialog.destroy()

        def apply_reprocess_selected() -> None:
            selected = {norm for norm, var in path_vars.items() if var.get()}
            all_norms = set(path_vars.keys())
            result["skip_norms"] = all_norms - selected
            dialog.destroy()

        def on_cancel() -> None:
            result["skip_norms"] = None
            dialog.destroy()

        tk.Button(actions, text="Skip selected", command=apply_skip_selected).pack(side="left")
        tk.Button(actions, text="Reprocess selected", command=apply_reprocess_selected).pack(side="left", padx=(8, 0))
        tk.Button(actions, text="Cancel", command=on_cancel).pack(side="left", padx=(8, 0))

        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        dialog.wait_window()
        return result["skip_norms"]

    def _run(self) -> None:
        run_prepare_started_at = perf_counter()
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showwarning("Busy", "Conversion is already running.")
            return

        self.runner.cleanup_spawned_processes(log=self._log)

        display = self.collection_display.get().strip()
        collection_key = self.collection_lookup.get(display)
        if not collection_key:
            messagebox.showerror("Error", "Select a collection.")
            return

        try:
            max_base_len = int(self.max_base_len.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Max base-name length must be an integer.")
            return

        if max_base_len < MIN_BASE_LEN:
            messagebox.showerror("Error", f"Max base-name length must be >= {MIN_BASE_LEN}.")
            return

        if not self.pdf_candidates:
            if not self._scan_pdfs_internal(show_errors=True):
                return

        selected_pdf_paths = self._selected_pdf_paths()
        if not selected_pdf_paths:
            messagebox.showerror("Error", "Select at least one PDF.")
            return

        try:
            output_dir_path = self._resolve_output_dir()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        selected_modes = self._selected_export_modes()
        if not selected_modes:
            messagebox.showerror("Error", "Select at least one export mode.")
            return

        translation_enabled = self.translate_html_with_gemma.get()
        translation_target_code = (
            self.translation_target_language_code.get().strip().lower()
            or DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE
        )
        translation_model_ref = self.translation_model_ref.get().strip() or DEFAULT_TRANSLATEGEMMA_MODEL
        translation_hf_token = (
            self.translation_hf_token.get().strip()
            or os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGINGFACE_HUB_TOKEN")
            or os.environ.get("HUGGINGFACE_TOKEN")
            or None
        )
        if translation_enabled and not translation_model_ref:
            messagebox.showerror("Error", "Set TranslateGemma model path or HF repo.")
            return
        if (
            translation_enabled
            and translation_model_ref == OFFICIAL_TRANSLATEGEMMA_MODEL_REPO
            and not Path(translation_model_ref).expanduser().exists()
            and not translation_hf_token
        ):
            messagebox.showerror(
                "TranslateGemma token required",
                f"{OFFICIAL_TRANSLATEGEMMA_MODEL_REPO} is gated on Hugging Face.\n\n"
                "Accept the model license on HF and provide a token in the GUI,\n"
                "or set HF_TOKEN / HUGGINGFACE_HUB_TOKEN in environment,\n"
                "or point model path to a local downloaded folder.",
            )
            return
        if (
            translation_enabled
            and not self.translation_hf_token.get().strip()
            and translation_hf_token is not None
        ):
            self._log("TranslateGemma: HF token loaded from environment.")
        if translation_enabled and not any(
            get_export_mode_spec(mode).marker_output_format == "html"
            for mode in selected_modes
        ):
            self._log(
                "TranslateGemma note: selected export modes do not produce HTML; "
                "translation will be skipped."
            )

        selected_pdf_path_objs = [Path(path) for path in selected_pdf_paths]
        artifact_extension = get_export_mode_spec(selected_modes[0]).artifact_extension

        run_skip_existing = self.skip_existing.get()
        skip_existing_override = False
        existing_started_at = perf_counter()
        already_in_current_output = detect_existing_results(
            output_dir_path,
            selected_pdf_path_objs,
            artifact_extension=artifact_extension,
        )
        self._log(f"[timer] gui.run.detect_existing_in_current_output: {perf_counter() - existing_started_at:.2f}s")
        if already_in_current_output:
            existing_paths = [
                path for path in selected_pdf_path_objs
                if normalize_source_path(path) in already_in_current_output
            ]
            dialog_started_at = perf_counter()
            skip_norms = self._prompt_existing_in_output_selection(existing_paths, output_dir_path)
            self._log(f"[timer] gui.run.existing_results_dialog: {perf_counter() - dialog_started_at:.2f}s")
            if skip_norms is None:
                self._log("Run cancelled by user: existing-results prompt.")
                return

            selected_pdf_path_objs = [
                path for path in selected_pdf_path_objs
                if normalize_source_path(path) not in skip_norms
            ]
            self._log(
                f"Existing files decision applied: skip={len(skip_norms)}, "
                f"reprocess={len(existing_paths) - len(skip_norms)}"
            )
            if not selected_pdf_path_objs:
                messagebox.showinfo("No files to run", "All selected files were skipped by current-output decision.")
                self._log("Run skipped: no files left after current-output decision.")
                return

            # Existing-file handling already resolved by per-file decision dialog.
            run_skip_existing = False
            skip_existing_override = True

        history_started_at = perf_counter()
        processed_elsewhere = find_processed_elsewhere(
            selected_pdf_path_objs,
            output_dir_path,
        )
        self._log(f"[timer] gui.run.find_processed_elsewhere: {perf_counter() - history_started_at:.2f}s")
        if processed_elsewhere:
            preview_lines = []
            for idx, record in enumerate(processed_elsewhere.values(), start=1):
                preview_lines.append(
                    f"{idx}. {record.source_pdf_path}\n   when: {record.processed_at_utc}\n   saved to: {record.output_dir}"
                )
                if idx >= 8:
                    break

            more = ""
            if len(processed_elsewhere) > 8:
                more = f"\n... and {len(processed_elsewhere) - 8} more files"

            msg = (
                "Some selected PDFs were already processed earlier and saved to another output folder.\n\n"
                + "\n".join(preview_lines)
                + more
                + "\n\nRepeat processing anyway?"
            )
            if not messagebox.askyesno("Already processed elsewhere", msg):
                self._log("Run cancelled by user: already-processed-elsewhere warning.")
                return

        # Group selected modes by Marker output format so Marker runs only once per format.
        # e.g. Classic + LLM → one markdown run; Classic + Zotero → two runs.
        options_started_at = perf_counter()
        format_groups: dict[str, list[ExportMode]] = {}
        for mode in selected_modes:
            fmt = get_export_mode_spec(mode).marker_output_format
            format_groups.setdefault(fmt, []).append(mode)

        all_options: list[PipelineOptions] = []
        for modes_in_group in format_groups.values():
            export_mode_str = ",".join(m.value for m in modes_in_group)
            all_options.append(PipelineOptions(
                zotero_data_dir=self.zotero_data_dir.get().strip(),
                collection_key=collection_key,
                include_subcollections=self.include_subcollections.get(),
                output_dir=str(output_dir_path),
                skip_existing=run_skip_existing,
                use_cuda=self.use_cuda.get(),
                model_cache_dir=self.model_cache_dir.get().strip() or None,
                max_base_len=max_base_len,
                disable_batch_multiprocessing=self.disable_batch_multiprocessing.get(),
                cleanup_staging=True,
                selected_source_pdf_paths=[str(path) for path in selected_pdf_path_objs],
                skip_existing_source_pdf_paths=None if not skip_existing_override else [],
                export_mode=export_mode_str,
                translate_html_with_gemma=translation_enabled,
                translation_target_language_code=translation_target_code,
                translation_source_language="Auto",
                translation_model_ref=translation_model_ref,
                translation_hf_token=translation_hf_token,
            ))
        self._log(f"[timer] gui.run.build_pipeline_options: {perf_counter() - options_started_at:.2f}s")
        self._log(f"[timer] gui.run.pre_worker_total: {perf_counter() - run_prepare_started_at:.2f}s")
        self._log(f"GUI selected export modes: {', '.join(m.value for m in selected_modes)}")
        if translation_enabled:
            try:
                translation_target_name = language_name_for_code(translation_target_code)
            except Exception:
                translation_target_name = translation_target_code
            self._log(
                "TranslateGemma: enabled "
                f"(target={translation_target_name} [{translation_target_code}], "
                f"model={translation_model_ref})"
            )
        else:
            self._log("TranslateGemma: disabled")
        self._log(f"GUI pipeline groups: {len(all_options)}")
        for idx, options in enumerate(all_options, start=1):
            modes = options.export_modes_list
            marker_format = get_export_mode_spec(modes[0]).marker_output_format
            self._log(
                f"  group {idx}/{len(all_options)}: "
                f"modes={', '.join(m.value for m in modes)}, "
                f"marker_output_format={marker_format}"
            )

        marker_cmd = self.marker_executable.get().strip() or "marker"
        marker_single_cmd = _derive_marker_single_cmd(marker_cmd)
        self.runner = MarkerRunner(marker_cmd=marker_cmd, marker_single_cmd=marker_single_cmd)
        self._log(f"Marker executable: {marker_cmd}")
        self._log(f"Marker-single executable: {marker_single_cmd}")

        self.stop_event.clear()
        self._log("\n=== run started ===")

        def worker() -> None:
            try:
                for idx, options in enumerate(all_options, start=1):
                    if self.stop_event.is_set():
                        self._queue_log("Stopped before next format group.")
                        break
                    modes = options.export_modes_list
                    marker_format = get_export_mode_spec(modes[0]).marker_output_format
                    self._queue_log(
                        f"Starting group {idx}/{len(all_options)}: "
                        f"modes={', '.join(m.value for m in modes)}, "
                        f"marker_output_format={marker_format}"
                    )
                    group_started_at = perf_counter()
                    summary = run_pipeline(
                        options=options,
                        runner=self.runner,
                        log=self._queue_log,
                        is_cancelled=self.stop_event.is_set,
                    )
                    self._queue_log(
                        f"[timer] gui.worker.group_total.{idx}: {perf_counter() - group_started_at:.2f}s"
                    )
                    self._queue_log(
                        "Summary: "
                        f"mode={summary.export_mode}, "
                        f"attachments={summary.attachments_total}, "
                        f"resolved_pdfs={summary.pdfs_resolved}, "
                        f"staged={summary.staged_total}, "
                        f"converted={summary.converted_total}, "
                        f"skipped_existing={summary.skipped_existing}, "
                        f"failed={summary.failed_total}"
                    )
                    if summary.llm_bundle_dir is not None:
                        self._queue_log(
                            "LLM bundle: "
                            f"{summary.llm_bundle_dir} "
                            f"(md={summary.llm_bundle_markdown_files}, "
                            f"images={summary.llm_bundle_image_files})"
                        )
                    if summary.translated_html_total or summary.translated_html_failed_total:
                        language_label = (
                            summary.translated_html_language_name
                            or summary.translated_html_language_code
                            or "n/a"
                        )
                        self._queue_log(
                            "TranslateGemma: "
                            f"translated={summary.translated_html_total}, "
                            f"failed={summary.translated_html_failed_total}, "
                            f"language={language_label}"
                        )
                    if ExportMode.ZOTERO.value in summary.export_mode:
                        self._queue_log(
                            "Zotero HTML attachments: "
                            f"attached={summary.zotero_html_attached_total}, "
                            f"failed={summary.zotero_html_failed_total}, "
                            f"queued={summary.zotero_html_queued_total}, "
                            f"pending_total={summary.zotero_pending_total}"
                        )
                    self._queue_log(f"Output dir: {summary.output_dir}")
                    self._queue_log(f"Filename map: {summary.filename_map_path}")
                self._queue_log("=== run finished ===")
            except Exception as exc:
                self._queue_log(f"ERROR: {exc}")
            finally:
                self.runner.cleanup_spawned_processes(log=self._queue_log)
                self.worker_thread = None

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _stop(self) -> None:
        self.stop_event.set()
        self.runner.terminate_current()
        self._log("Stop requested.")

    def _on_close(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            should_close = messagebox.askyesno(
                "Exit",
                "Conversion is still running. Stop it and close the app?",
            )
            if not should_close:
                return
            self.stop_event.set()
            self.runner.terminate_current()
            if self.worker_thread is not None:
                self.worker_thread.join(timeout=3)

        self.runner.cleanup_spawned_processes(log=self._log)
        self.root.destroy()

    def _retry_pending_zotero(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showwarning("Busy", "Another task is already running.")
            return

        zotero_data_dir = self.zotero_data_dir.get().strip()
        if not zotero_data_dir:
            messagebox.showerror("Error", "Set Zotero data directory first.")
            return
        try:
            output_dir = str(self._resolve_output_dir())
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return

        self._log("\n=== retry pending Zotero started ===")

        def worker() -> None:
            try:
                retry_pending_zotero_exports(
                    zotero_data_dir=zotero_data_dir,
                    output_dir=output_dir,
                    log=self._queue_log,
                )
                self._queue_log("=== retry pending Zotero finished ===")
            except Exception as exc:
                self._queue_log(f"ERROR: {exc}")
            finally:
                self.runner.cleanup_spawned_processes(log=self._queue_log)
                self.worker_thread = None

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()


def main() -> None:
    root = tk.Tk()
    ZoteroPdfGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
