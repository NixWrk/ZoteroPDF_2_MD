from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .marker_runner import MarkerRunner
from .paths import detect_default_zotero_data_dir
from .pipeline import PipelineOptions, run_pipeline
from .staging import DEFAULT_MAX_BASE_LEN, MIN_BASE_LEN
from .zotero import ZoteroRepository


class ZoteroPdfGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ZoteroPDF_2_MD")
        self.root.geometry("1100x760")

        default_zotero = detect_default_zotero_data_dir()

        self.zotero_data_dir = tk.StringVar(value=str(default_zotero) if default_zotero else "")
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "md_output"))
        self.model_cache_dir = tk.StringVar(value="")
        self.collection_display = tk.StringVar(value="")

        self.include_subcollections = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=True)
        self.use_cuda = tk.BooleanVar(value=True)
        self.disable_batch_multiprocessing = tk.BooleanVar(value=False)
        self.keep_staging = tk.BooleanVar(value=False)

        self.max_base_len = tk.StringVar(value=str(DEFAULT_MAX_BASE_LEN))

        self.collection_lookup: dict[str, str] = {}
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
        tk.Entry(frame, textvariable=self.zotero_data_dir, width=110).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_zotero_data_dir).grid(row=row, column=1, padx=6)
        tk.Button(frame, text="Load collections", command=self._load_collections).grid(row=row, column=2)

        row += 1
        tk.Label(frame, text="Collection").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        self.collection_combo = ttk.Combobox(
            frame,
            textvariable=self.collection_display,
            width=105,
            state="readonly",
        )
        self.collection_combo.grid(row=row, column=0, sticky="we")

        row += 1
        tk.Label(frame, text="Output folder").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        tk.Entry(frame, textvariable=self.output_dir, width=110).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_output_dir).grid(row=row, column=1, padx=6)

        row += 1
        tk.Label(frame, text="Model cache folder (optional)").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        tk.Entry(frame, textvariable=self.model_cache_dir, width=110).grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Browse", command=self._pick_model_cache_dir).grid(row=row, column=1, padx=6)

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
        self.log = scrolledtext.ScrolledText(frame, width=140, height=28)
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew")

        frame.grid_columnconfigure(0, weight=1)
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
