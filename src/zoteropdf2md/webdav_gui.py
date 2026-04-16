"""tkinter GUI for managing WebDAV servers used by the ZoteroPDF2MD pipeline.

Depends only on the Python standard library (tkinter). All data-model and
upload logic lives in :mod:`webdav_config` / :mod:`webdav_uploader` so this
file can be swapped for a web UI without touching business logic.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .webdav_config import DEFAULT_CONFIG_PATH, WebDavConfig, WebDavServer
from .webdav_uploader import WebDavUploader


_DEFAULT_NEW_SERVER_NAME = "New Server"


class WebDavConfigApp:
    """Main window — a two-pane editor for a list of WebDAV servers."""

    def __init__(self, root: tk.Tk, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.root = root
        self.config_path = Path(config_path)
        self.config = WebDavConfig.load(self.config_path)
        self.uploader = WebDavUploader()

        # Tracks whether the in-memory config differs from disk.
        self._dirty = False
        # True while we programmatically update fields — prevents the trace
        # callbacks from marking the config dirty.
        self._suppress_trace = False
        # Index of the currently selected server in self.config.servers, or None.
        self._selected_index: int | None = None

        self._build_ui()
        self._refresh_listbox(select_index=0 if self.config.servers else None)
        self._update_title()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.root.minsize(640, 380)
        self.root.geometry("720x420")

        # ---- Top frame: list | form ---------------------------------------
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.BOTH, expand=True)

        # Left: list of servers + add/remove buttons.
        left = ttk.Frame(top)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

        ttk.Label(left, text="Servers", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.listbox = tk.Listbox(left, width=22, exportselection=False)
        self.listbox.pack(fill=tk.Y, expand=True, pady=(2, 4))
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        button_row = ttk.Frame(left)
        button_row.pack(fill=tk.X)
        ttk.Button(button_row, text="+", width=3, command=self._on_add).pack(side=tk.LEFT)
        ttk.Button(button_row, text="-", width=3, command=self._on_delete).pack(side=tk.LEFT, padx=(4, 0))

        # Right: detail form.
        right = ttk.LabelFrame(top, text="Server Details", padding=8)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # tkinter StringVar objects holding the form state.
        self.var_name = tk.StringVar()
        self.var_url = tk.StringVar()
        self.var_user = tk.StringVar()
        self.var_pass = tk.StringVar()
        self.var_root = tk.StringVar()
        self.var_enabled = tk.BooleanVar(value=True)

        for var in (self.var_name, self.var_url, self.var_user, self.var_pass, self.var_root):
            var.trace_add("write", self._on_field_changed)
        self.var_enabled.trace_add("write", self._on_field_changed)

        self._form_rows: list[tk.Widget] = []
        self._add_form_row(right, 0, "Name:", self.var_name)
        self._add_form_row(right, 1, "URL:", self.var_url)
        self._add_form_row(right, 2, "User:", self.var_user)
        self._add_form_row(right, 3, "Password:", self.var_pass, show="*")
        self._add_form_row(right, 4, "Remote root:", self.var_root)

        self.chk_enabled = ttk.Checkbutton(right, text="Enabled", variable=self.var_enabled)
        self.chk_enabled.grid(row=5, column=1, sticky="w", pady=(4, 0))
        self._form_rows.append(self.chk_enabled)

        self.btn_test = ttk.Button(right, text="Test Connection", command=self._on_test)
        self.btn_test.grid(row=6, column=1, sticky="w", pady=(8, 0))
        self._form_rows.append(self.btn_test)

        right.columnconfigure(1, weight=1)

        # ---- Bottom: status + save/close ----------------------------------
        bottom = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        bottom.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bottom, text="Status:").pack(side=tk.LEFT)
        ttk.Label(bottom, textvariable=self.status_var, foreground="#333").pack(
            side=tk.LEFT, padx=(4, 0)
        )

        ttk.Button(bottom, text="Close", command=self._on_close).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(bottom, text="Save", command=self._on_save).pack(side=tk.RIGHT)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _add_form_row(self, parent: tk.Widget, row: int, label: str, var: tk.StringVar, show: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var, show=show or "")
        entry.grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=2)
        self._form_rows.append(entry)

    # ----------------------------------------------------------- state sync

    def _update_title(self) -> None:
        dirty_marker = "*" if self._dirty else ""
        self.root.title(f"Zotero WebDAV Config{dirty_marker} — {self.config_path}")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _refresh_listbox(self, select_index: int | None = None) -> None:
        self.listbox.delete(0, tk.END)
        for server in self.config.servers:
            label = server.name or "(unnamed)"
            if not server.enabled:
                label = f"[off] {label}"
            self.listbox.insert(tk.END, label)

        if select_index is not None and 0 <= select_index < len(self.config.servers):
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(select_index)
            self.listbox.activate(select_index)
            self._load_server_into_form(select_index)
        else:
            self._selected_index = None
            self._clear_form()

    def _load_server_into_form(self, index: int) -> None:
        if not (0 <= index < len(self.config.servers)):
            return
        server = self.config.servers[index]
        self._selected_index = index
        self._suppress_trace = True
        try:
            self.var_name.set(server.name)
            self.var_url.set(server.url)
            self.var_user.set(server.username)
            self.var_pass.set(server.password)
            self.var_root.set(server.remote_root)
            self.var_enabled.set(server.enabled)
        finally:
            self._suppress_trace = False
        self._set_form_enabled(True)

    def _clear_form(self) -> None:
        self._suppress_trace = True
        try:
            self.var_name.set("")
            self.var_url.set("")
            self.var_user.set("")
            self.var_pass.set("")
            self.var_root.set("")
            self.var_enabled.set(True)
        finally:
            self._suppress_trace = False
        self._set_form_enabled(False)

    def _set_form_enabled(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        for widget in self._form_rows:
            try:
                widget.state(state)
            except (AttributeError, tk.TclError):
                pass

    # ------------------------------------------------------------- handlers

    def _on_listbox_select(self, _event: object) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        self._load_server_into_form(selection[0])

    def _on_field_changed(self, *_args: object) -> None:
        if self._suppress_trace or self._selected_index is None:
            return
        server = self.config.servers[self._selected_index]
        server.name = self.var_name.get()
        server.url = self.var_url.get()
        server.username = self.var_user.get()
        server.password = self.var_pass.get()
        server.remote_root = self.var_root.get()
        server.enabled = bool(self.var_enabled.get())
        self._dirty = True
        self._update_title()

        # Refresh list label if name/enabled changed without losing selection.
        idx = self._selected_index
        label = server.name or "(unnamed)"
        if not server.enabled:
            label = f"[off] {label}"
        self.listbox.delete(idx)
        self.listbox.insert(idx, label)
        self.listbox.selection_set(idx)
        self.listbox.activate(idx)

    def _on_add(self) -> None:
        new_server = WebDavServer(name=_DEFAULT_NEW_SERVER_NAME)
        self.config.servers.append(new_server)
        self._dirty = True
        self._refresh_listbox(select_index=len(self.config.servers) - 1)
        self._update_title()
        self._set_status("Added new server")

    def _on_delete(self) -> None:
        if self._selected_index is None:
            return
        index = self._selected_index
        server = self.config.servers[index]
        confirm = messagebox.askyesno(
            "Delete server",
            f"Delete server \"{server.name or '(unnamed)'}\"?",
            parent=self.root,
        )
        if not confirm:
            return
        del self.config.servers[index]
        self._dirty = True
        next_index: int | None
        if self.config.servers:
            next_index = min(index, len(self.config.servers) - 1)
        else:
            next_index = None
        self._refresh_listbox(select_index=next_index)
        self._update_title()
        self._set_status("Server deleted")

    def _on_test(self) -> None:
        if self._selected_index is None:
            self._set_status("No server selected")
            return
        server = self.config.servers[self._selected_index]
        self._set_status(f"Testing {server.name or server.url}...")
        self.btn_test.state(["disabled"])

        # Run the network call off the UI thread so the window stays responsive.
        def worker(srv: WebDavServer) -> None:
            ok, msg = self.uploader.test_connection(srv)
            prefix = "OK" if ok else "FAIL"
            self.root.after(0, self._finish_test, f"{prefix}: {msg}")

        thread = threading.Thread(target=worker, args=(server,), daemon=True)
        thread.start()

    def _finish_test(self, message: str) -> None:
        self._set_status(message)
        try:
            self.btn_test.state(["!disabled"])
        except tk.TclError:
            pass

    def _on_save(self) -> None:
        try:
            self.config.save(self.config_path)
        except OSError as exc:
            messagebox.showerror("Save failed", f"Could not save config:\n{exc}", parent=self.root)
            self._set_status(f"Save failed: {exc}")
            return
        self._dirty = False
        self._update_title()
        self._set_status(f"Saved to {self.config_path}")

    def _on_close(self) -> None:
        if self._dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved changes",
                "Save before closing?",
                parent=self.root,
            )
            if answer is None:
                return  # Cancel — stay open.
            if answer:
                self._on_save()
                if self._dirty:
                    # Save failed — keep the window open.
                    return
        self.root.destroy()


def main() -> None:
    """Entry point used by ``run_webdav_gui.py``."""
    root = tk.Tk()
    WebDavConfigApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
