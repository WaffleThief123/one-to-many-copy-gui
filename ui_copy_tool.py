import os
import shutil
import json
import logging
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
from logging.handlers import RotatingFileHandler
import platform
import subprocess
import sys

def get_log_path():
    base = Path.home() / ".one_to_many_logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "sync_log.txt"

def resource_path(name):
    if hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS) / name
    return Path(__file__).parent / name

LOG_FILE = get_log_path()
MACHINE_LIST_FILE = resource_path("machine_list.json")
IGNORED_EXTENSIONS_FILE = resource_path("ignored_extensions.json")



logger = logging.getLogger()
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=2)
file_handler.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

def load_ignored_extensions():
    try:
        with open(IGNORED_EXTENSIONS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load ignored extensions: {e}")
        return []

def files_are_identical(src_file: Path, dst_file: Path) -> bool:
    try:
        return (
            src_file.stat().st_size == dst_file.stat().st_size and
            int(src_file.stat().st_mtime) == int(dst_file.stat().st_mtime)
        )
    except FileNotFoundError:
        return False

def count_total_files(src: Path) -> int:
    total = 0
    for _, _, files in os.walk(src):
        total += len(files)
    return total

def copy_recursively(src: Path, dst: Path, ignored_exts=None, progress_callback=None):
    total_files = count_total_files(src)
    copied = 0

    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        dst_root = dst / rel_root

        for d in dirs:
            (dst_root / d).mkdir(parents=True, exist_ok=True)

        for f in files:
            if ignored_exts and any(f.lower().endswith(ext.lower()) for ext in ignored_exts):
                logger.info(f"Skipped (ignored extension): {f}")
                continue

            src_file = Path(root) / f
            dst_file = dst_root / f

            try:
                if not dst_file.exists() or not files_are_identical(src_file, dst_file):
                    shutil.copy2(src_file, dst_file)
                    logger.info(f"Copied: {src_file} -> {dst_file}")
                else:
                    logger.debug(f"Skipped (identical): {src_file}")
            except Exception as e:
                logger.error(f"Error copying {src_file} to {dst_file}: {e}")

            copied += 1
            if progress_callback:
                progress_callback(copied, total_files)

def ensure_path_mapped(share_path: str, host_type: str = "smb") -> bool:
    if host_type == "local":
        return os.path.exists(share_path)

    if os.path.exists(share_path):
        return True

    system = platform.system()

    if system == "Windows":
        logger.info(f"Trying to map Windows share: {share_path}")

        username = simpledialog.askstring("Credentials", f"Enter username for {share_path} (DOMAIN\\\\user):", show=None)
        password = simpledialog.askstring("Credentials", f"Enter password for {username}:", show='*')

        if not username or not password:
            messagebox.showerror("Error", "Username and password required to map share.")
            return False

        try:
            result = subprocess.run(
                ["net", "use", share_path, password, f"/user:{username}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True
            )
            if result.returncode == 0:
                logger.info(f"Successfully mapped: {share_path}")
                return True
            else:
                logger.warning(f"Failed to map {share_path}:\n{result.stderr}")
        except Exception as e:
            logger.error(f"Exception during 'net use': {e}")

    elif system in ("Linux", "Darwin"):
        logger.warning(f"UNC path '{share_path}' not accessible on {system}. Must be mounted manually.")

    return os.path.exists(share_path)

class HostManager(tk.Toplevel):
    def __init__(self, parent, machine_vars):
        super().__init__(parent)
        self.title("Manage Hosts")
        self.machine_vars = machine_vars
        self.modified = False

        self.geometry("720x350")
        self.resizable(False, False)

        self.host_listbox = tk.Listbox(self, width=100)
        self.host_listbox.pack(padx=10, pady=10, fill="both", expand=True)

        form_frame = tk.Frame(self)
        form_frame.pack(pady=5)

        tk.Label(form_frame, text="Machine Name").grid(row=0, column=0, padx=10, sticky="w")
        tk.Label(form_frame, text="Path").grid(row=0, column=1, padx=10, sticky="w")
        tk.Label(form_frame, text="Type").grid(row=0, column=2, padx=10, sticky="w")

        self.name_entry = tk.Entry(form_frame, width=25)
        self.name_entry.grid(row=1, column=0, padx=10)

        self.path_entry = tk.Entry(form_frame, width=45)
        self.path_entry.grid(row=1, column=1, padx=10)

        self.type_var = tk.StringVar()
        self.type_var.set("smb")
        self.type_menu = ttk.Combobox(form_frame, textvariable=self.type_var, values=["smb", "local"], state="readonly", width=10)
        self.type_menu.grid(row=1, column=2, padx=10)

        button_frame = tk.Frame(self)
        button_frame.pack(pady=5)

        tk.Button(button_frame, text="Add Host", command=self.add_host).pack(side="left", padx=5)
        tk.Button(button_frame, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=5)
        tk.Button(button_frame, text="Save & Close", command=self.save_and_close).pack(side="left", padx=5)

        self.load_existing()

    def load_existing(self):
        self.host_listbox.delete(0, tk.END)
        for name, (_, path, htype) in self.machine_vars.items():
            self.host_listbox.insert(tk.END, f"{name} | {path} | {htype}")

    def add_host(self):
        name = self.name_entry.get().strip()
        path = self.path_entry.get().strip()
        htype = self.type_var.get().strip()
        if not name or not path:
            messagebox.showerror("Error", "Name and path are required.")
            return
        if name in self.machine_vars:
            messagebox.showerror("Error", "Name already exists.")
            return

        var = tk.BooleanVar()
        self.machine_vars[name] = (var, path, htype)
        self.host_listbox.insert(tk.END, f"{name} | {path} | {htype}")
        self.modified = True
        self.name_entry.delete(0, tk.END)
        self.path_entry.delete(0, tk.END)
        self.type_var.set("smb")

    def remove_selected(self):
        sel = self.host_listbox.curselection()
        if not sel:
            return
        line = self.host_listbox.get(sel[0])
        name = line.split(" | ")[0]
        if name in self.machine_vars:
            del self.machine_vars[name]
        self.host_listbox.delete(sel[0])
        self.modified = True

    def save_and_close(self):
        if self.modified:
            try:
                new_list = [{"name": k, "path": v[1], "type": v[2]} for k, v in self.machine_vars.items()]
                with open(MACHINE_LIST_FILE, "w") as f:
                    json.dump(new_list, f, indent=2)
                logger.info("Updated machine_list.json")
            except Exception as e:
                logger.error(f"Failed to save machine list: {e}")
                messagebox.showerror("Error", f"Failed to save machine list:\n{e}")
                return
        self.destroy()

class ExtensionManager(tk.Toplevel):
    def __init__(self, parent, extension_list):
        super().__init__(parent)
        self.title("Manage Ignored Extensions")
        self.geometry("400x300")
        self.resizable(False, False)

        self.extension_list = extension_list
        self.modified = False

        self.listbox = tk.Listbox(self, width=50)
        self.listbox.pack(padx=10, pady=10, fill="both", expand=True)

        self.entry = tk.Entry(self, width=20)
        self.entry.pack(padx=10, pady=5)

        btn_frame = tk.Frame(self)
        btn_frame.pack()

        tk.Button(btn_frame, text="Add", command=self.add_extension).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Save & Close", command=self.save_and_close).pack(side="left", padx=5)

        self.load_existing()

    def load_existing(self):
        self.listbox.delete(0, tk.END)
        for ext in self.extension_list:
            self.listbox.insert(tk.END, ext)

class CopyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("One-to-Many Sync Tool")

        self.source_path = None
        self.machine_vars = {}
        self.loaded_machines = []
        self.ignored_extensions = load_ignored_extensions()

        tk.Button(root, text="Select Source Folder", command=self.select_source).pack(pady=5)
        self.source_label = tk.Label(root, text="No source selected", fg="blue")
        self.source_label.pack()

        self.machine_frame = tk.LabelFrame(root, text="Select Destination Machines")
        self.machine_frame.pack(padx=10, pady=10, fill="both", expand=True)

        self.refresh_machine_list()

        self.progress = ttk.Progressbar(root, length=400, mode="determinate")
        self.progress.pack(pady=10)

        tk.Button(root, text="Start Sync", command=self.start_copy).pack(pady=5)
        tk.Button(root, text="Manage Hosts", command=self.open_host_manager).pack(pady=5)
        tk.Button(root, text="Manage Ignored Extensions", command=self.open_extension_manager).pack(pady=5)

    def refresh_machine_list(self):
        for widget in self.machine_frame.winfo_children():
            widget.destroy()
        try:
            with open(MACHINE_LIST_FILE, "r") as f:
                machines = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load machine list: {e}")
            machines = []

        self.loaded_machines = machines
        self.machine_vars.clear()
        for machine in machines:
            name = machine["name"]
            path = machine["path"]
            htype = machine.get("type", "smb")
            var = tk.BooleanVar()
            cb = tk.Checkbutton(self.machine_frame, text=f"{name} ({path}, type={htype})", variable=var)
            cb.pack(anchor="w")
            self.machine_vars[name] = (var, path, htype)

    def open_host_manager(self):
        HostManager(self.root, self.machine_vars).wait_window()
        self.refresh_machine_list()

    def select_source(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.source_path = Path(folder)
            self.source_label.config(text=str(self.source_path))

    def update_progress(self, current, total):
        if total == 0:
            return
        self.progress["value"] = int((current / total) * 100)
        self.root.update_idletasks()

    def start_copy(self):
        if not self.source_path or not self.source_path.exists():
            messagebox.showerror("Error", "Source path is not valid.")
            return

        selected = [(name, Path(path), htype) for name, (var, path, htype) in self.machine_vars.items() if var.get()]
        if not selected:
            messagebox.showerror("Error", "No destination machines selected.")
            return

        for name, dst_base, htype in selected:
            if not ensure_path_mapped(str(dst_base), htype):
                logger.error(f"Cannot access or map destination: {dst_base}")
                messagebox.showerror("Mapping Failed", f"Could not access or map: {dst_base}")
                continue

            try:
                self.progress["value"] = 0
                logger.info(f"Starting sync to {name}: {dst_base}")
                copy_recursively(self.source_path, dst_base, ignored_exts=self.ignored_extensions, progress_callback=self.update_progress)
                logger.info(f"Completed sync to {name}")
            except Exception as e:
                logger.error(f"Error copying to {dst_base}: {e}")
                messagebox.showerror("Copy Failed", f"{name} failed:\n{e}")

        messagebox.showinfo("Done", "Sync completed.")
        self.progress["value"] = 0
    def open_extension_manager(self):
        ExtensionManager(self.root, self.ignored_extensions).wait_window()


# ---- Run App ----
if __name__ == "__main__":
    root = tk.Tk()
    app = CopyApp(root)
    root.mainloop()
