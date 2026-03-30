from __future__ import annotations

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
from .staging import DEFAULT_MAX_BASE_LEN, MIN_BASE_LEN
from .zotero import ZoteroRepository


class ZoteroPdfGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ZoteroPDF_2_MD")
        self.root.geometry("1180x860")

        default_zotero = detect_default_zotero_data_dir()

        self.zotero_data_dir = tk.StringVar(value=str(default_zotero) if default_zotero else "")
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "md_output"))
        self.model_cache_dir = tk.StringVar(value="")
        self.collection_display = tk.StringVar(value="")
        self.profile_display = tk.StringVar(value="")
        self.pdf_status = tk.StringVar(value="No PDF scan yet")

        self.include_subcollections = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=True)
        self.use_cuda = tk.BooleanVar(value=True)
        self.disable_batch_multiprocessing = tk.BooleanVar(value=False)
        self.keep_staging = tk.BooleanVar(value=False)
        self.export_mode = tk.StringVar(value=ExportMode.CLASSIC.value)

        self.max_base_len = tk.StringVar(value=str(DEFAULT_MAX_BASE_LEN))

        self.collection_lookup: dict[str, str] = {}
        self.profile_lookup: dict[str, str] = {}
        self.pdf_candidates: list[PdfCandidate] = []
        self.pdf_selection_vars: dict[str, tk.BooleanVar] = {}

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.runner = MarkerRunner()

        self._build_ui()
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
        tk.Checkbutton(options, text="Keep staging folder (debug)", variable=self.keep_staging).pack(anchor="w")

        mode_frame = tk.Frame(options)
        mode_frame.pack(anchor="w", pady=(6, 0))
        tk.Label(mode_frame, text="Export mode:").pack(anchor="w")
        for spec in all_export_mode_specs():
            tk.Radiobutton(
                mode_frame,
                text=spec.label,
                variable=self.export_mode,
                value=spec.mode.value,
            ).pack(anchor="w")

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

    def _current_export_mode(self) -> ExportMode:
        return parse_export_mode(self.export_mode.get())

    def _current_artifact_extension(self) -> str:
        return get_export_mode_spec(self._current_export_mode()).artifact_extension

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

        try:
            repo = ZoteroRepository(Path(zotero_data_dir))
            collections = repo.get_collections()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to load collections: {exc}")
            return

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
            discover_started_at = perf_counter()
            discovery = discover_collection_pdfs(
                zotero_data_dir=self.zotero_data_dir.get().strip(),
                collection_key=collection_key,
                include_subcollections=self.include_subcollections.get(),
                output_dir=output_dir,
                artifact_extension=self._current_artifact_extension(),
                log=self._log,
            )
            self._log(f"[timer] gui.scan.discover_collection_pdfs: {perf_counter() - discover_started_at:.2f}s")
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Error", f"Failed to scan PDFs: {exc}")
            return False

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

        output_dir_path = Path(self.output_dir.get().strip()).expanduser().resolve()
        selected_pdf_path_objs = [Path(path) for path in selected_pdf_paths]
        current_export_mode = self._current_export_mode()
        artifact_extension = get_export_mode_spec(current_export_mode).artifact_extension

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

        options_started_at = perf_counter()
        options = PipelineOptions(
            zotero_data_dir=self.zotero_data_dir.get().strip(),
            collection_key=collection_key,
            include_subcollections=self.include_subcollections.get(),
            output_dir=self.output_dir.get().strip(),
            skip_existing=run_skip_existing,
            use_cuda=self.use_cuda.get(),
            model_cache_dir=self.model_cache_dir.get().strip() or None,
            max_base_len=max_base_len,
            disable_batch_multiprocessing=self.disable_batch_multiprocessing.get(),
            cleanup_staging=not self.keep_staging.get(),
            selected_source_pdf_paths=[str(path) for path in selected_pdf_path_objs],
            skip_existing_source_pdf_paths=None if not skip_existing_override else [],
            export_mode=current_export_mode.value,
        )
        self._log(f"[timer] gui.run.build_pipeline_options: {perf_counter() - options_started_at:.2f}s")
        self._log(f"[timer] gui.run.pre_worker_total: {perf_counter() - run_prepare_started_at:.2f}s")
        self._log(f"GUI selected export mode: {current_export_mode.value}")

        self.stop_event.clear()
        self._log("\n=== run started ===")

        def worker() -> None:
            try:
                summary = run_pipeline(
                    options=options,
                    runner=self.runner,
                    log=self._queue_log,
                    is_cancelled=self.stop_event.is_set,
                )
                self._queue_log("=== run finished ===")
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
                if summary.export_mode == ExportMode.ZOTERO.value:
                    self._queue_log(
                        "Zotero HTML attachments: "
                        f"attached={summary.zotero_html_attached_total}, "
                        f"failed={summary.zotero_html_failed_total}, "
                        f"queued={summary.zotero_html_queued_total}, "
                        f"pending_total={summary.zotero_pending_total}"
                    )
                self._queue_log(f"Output dir: {summary.output_dir}")
                self._queue_log(f"Filename map: {summary.filename_map_path}")
            except Exception as exc:
                self._queue_log(f"ERROR: {exc}")
            finally:
                self.worker_thread = None

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _stop(self) -> None:
        self.stop_event.set()
        self.runner.terminate_current()
        self._log("Stop requested.")

    def _retry_pending_zotero(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showwarning("Busy", "Another task is already running.")
            return

        zotero_data_dir = self.zotero_data_dir.get().strip()
        output_dir = self.output_dir.get().strip()
        if not zotero_data_dir:
            messagebox.showerror("Error", "Set Zotero data directory first.")
            return
        if not output_dir:
            messagebox.showerror("Error", "Set output directory first.")
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
                self.worker_thread = None

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()


def main() -> None:
    root = tk.Tk()
    ZoteroPdfGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
