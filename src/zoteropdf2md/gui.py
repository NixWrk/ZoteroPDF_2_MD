from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .history import find_processed_elsewhere
from .marker_runner import MarkerRunner
from .output_state import normalize_source_path
from .paths import detect_default_zotero_data_dir
from .pipeline import PdfCandidate, PipelineOptions, discover_collection_pdfs, run_pipeline
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
        self.pdf_status = tk.StringVar(value="No PDF scan yet")

        self.include_subcollections = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=True)
        self.use_cuda = tk.BooleanVar(value=True)
        self.disable_batch_multiprocessing = tk.BooleanVar(value=False)
        self.keep_staging = tk.BooleanVar(value=False)

        self.max_base_len = tk.StringVar(value=str(DEFAULT_MAX_BASE_LEN))

        self.collection_lookup: dict[str, str] = {}
        self.pdf_candidates: list[PdfCandidate] = []
        self.pdf_selection_vars: dict[str, tk.BooleanVar] = {}

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.runner = MarkerRunner()

        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        frame = tk.Frame(self.root, padx=10, pady=10)
        frame.pack(fill="both", expand=True)

        row = 0
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

        limits = tk.Frame(options)
        limits.pack(anchor="w", pady=(6, 0))
        tk.Label(limits, text="Max source base-name length:").pack(side="left")
        tk.Entry(limits, textvariable=self.max_base_len, width=8).pack(side="left", padx=(6, 0))

        row += 1
        actions = tk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 10))
        tk.Button(actions, text="Run", command=self._run).pack(side="left")
        tk.Button(actions, text="Stop", command=self._stop).pack(side="left", padx=(8, 0))

        row += 1
        self.log = scrolledtext.ScrolledText(frame, width=150, height=22)
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew")

        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(row - 4, weight=1)
        frame.grid_rowconfigure(row, weight=1)

    def _pick_zotero_data_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select Zotero data directory")
        if folder:
            self.zotero_data_dir.set(folder)

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
            discovery = discover_collection_pdfs(
                zotero_data_dir=self.zotero_data_dir.get().strip(),
                collection_key=collection_key,
                include_subcollections=self.include_subcollections.get(),
                output_dir=output_dir,
            )
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Error", f"Failed to scan PDFs: {exc}")
            return False

        self.pdf_candidates = discovery.candidates
        self._rebuild_pdf_checkboxes()

        done_in_output = sum(1 for c in discovery.candidates if c.already_in_output)
        self._log(
            f"PDF scan: resolved={len(discovery.candidates)}, "
            f"already_in_output={done_in_output}, unresolved={discovery.unresolved_total}"
        )
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

    def _run(self) -> None:
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
        processed_elsewhere = find_processed_elsewhere(
            [Path(path) for path in selected_pdf_paths],
            output_dir_path,
        )
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

        options = PipelineOptions(
            zotero_data_dir=self.zotero_data_dir.get().strip(),
            collection_key=collection_key,
            include_subcollections=self.include_subcollections.get(),
            output_dir=self.output_dir.get().strip(),
            skip_existing=self.skip_existing.get(),
            use_cuda=self.use_cuda.get(),
            model_cache_dir=self.model_cache_dir.get().strip() or None,
            max_base_len=max_base_len,
            disable_batch_multiprocessing=self.disable_batch_multiprocessing.get(),
            cleanup_staging=not self.keep_staging.get(),
            selected_source_pdf_paths=selected_pdf_paths,
        )

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
                    f"attachments={summary.attachments_total}, "
                    f"resolved_pdfs={summary.pdfs_resolved}, "
                    f"staged={summary.staged_total}, "
                    f"converted={summary.converted_total}, "
                    f"skipped_existing={summary.skipped_existing}, "
                    f"failed={summary.failed_total}"
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


def main() -> None:
    root = tk.Tk()
    ZoteroPdfGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
