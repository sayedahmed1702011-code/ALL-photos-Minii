from __future__ import annotations

import io
import os
import queue
import string
import threading
import time
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

APP_NAME = "All Photos Mini"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".ico"}
THUMB_SIZE = (132, 98)
PREVIEW_SIZE = (430, 390)
TILE_W, TILE_H = 158, 145
CACHE_LIMIT = 180


class AllPhotosMini:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1120x730")
        self.root.minsize(850, 560)
        self.root.configure(bg="#f4f6f8")

        self.paths: list[str] = []
        self.visible_paths: list[str] = []
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="اختر مجلدًا أو افحص الأقراص لبدء البحث")
        self.selected_path: str | None = None
        self.scan_id = 0
        self.scan_cancel: threading.Event | None = None
        self.scan_queue: queue.Queue = queue.Queue()
        self.image_queue: queue.Queue = queue.Queue()
        self.result_queue: queue.Queue = queue.Queue()
        self.pending_images: set[tuple[str, str]] = set()
        self.thumb_cache: OrderedDict[str, ImageTk.PhotoImage] = OrderedDict()
        self.failed_thumbnails: set[str] = set()
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.drawn_items: list[int] = []
        self.columns = 1
        self.search_after: str | None = None
        self.closed = False

        self._style()
        self._build_ui()
        threading.Thread(target=self._image_worker, daemon=True, name="thumbnail-worker").start()
        self.root.after(100, self._poll_results)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=("Segoe UI", 9), padding=(8, 6))
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"), padding=(9, 7))
        style.configure("TEntry", padding=7)
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#ffffff", height=70, padx=18, pady=12)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="All Photos Mini", bg="#ffffff", fg="#17212b",
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        tk.Label(header, text="عارض صور محلي وخفيف", bg="#ffffff", fg="#718096",
                 font=("Segoe UI", 9)).pack(side="left", padx=(12, 24), pady=(8, 0))
        search_wrap = tk.Frame(header, bg="#f1f4f7", highlightthickness=1, highlightbackground="#e1e6eb")
        search_wrap.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=3)
        tk.Label(search_wrap, text="⌕", bg="#f1f4f7", fg="#748090", font=("Segoe UI", 15)).pack(side="left", padx=(10, 3))
        self.search_entry = ttk.Entry(search_wrap, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.search_entry.insert(0, "")
        self.search_entry.bind("<KeyRelease>", self._schedule_search)
        ttk.Button(header, text="⟳  تحديث", command=self._rescan, style="Accent.TButton").pack(side="right")

        body = tk.Frame(self.root, bg="#f4f6f8", padx=12, pady=12)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(body, bg="#ffffff", width=220, padx=12, pady=12)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        sidebar.grid_propagate(False)
        tk.Label(sidebar, text="مجلدات الفحص", bg="#ffffff", fg="#293746",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.roots_list = tk.Listbox(sidebar, selectmode="extended", height=10, bg="#f8fafb", fg="#364453",
                                     relief="flat", highlightthickness=1, highlightbackground="#e7ebef",
                                     font=("Segoe UI", 8), activestyle="none")
        self.roots_list.pack(fill="both", expand=True)
        ttk.Button(sidebar, text="＋  اختيار مجلد", command=self._add_folder).pack(fill="x", pady=(10, 5))
        ttk.Button(sidebar, text="−  إزالة المحدد", command=self._remove_roots).pack(fill="x", pady=3)
        ttk.Button(sidebar, text="فحص المجلدات المحددة", command=self._scan_selected,
                   style="Accent.TButton").pack(fill="x", pady=(12, 5))
        ttk.Button(sidebar, text="فحص جميع الأقراص", command=self._scan_all_drives).pack(fill="x", pady=3)
        tk.Label(sidebar, text="الفحص للقراءة فقط؛ لن يُحذف شيء دون تأكيدك.",
                 bg="#ffffff", fg="#84909c", font=("Segoe UI", 8), wraplength=190,
                 justify="left").pack(anchor="w", pady=(15, 0))

        gallery = tk.Frame(body, bg="#ffffff")
        gallery.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        gallery.grid_rowconfigure(0, weight=1)
        gallery.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(gallery, bg="#ffffff", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(gallery, orient="vertical", command=self._scroll_canvas)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Expose>", lambda _e: self._draw_gallery())
        self.canvas.configure(scrollregion=(0, 0, 1, 1))

        details = tk.Frame(body, bg="#ffffff", width=255, padx=13, pady=12)
        details.grid(row=0, column=2, sticky="nsew")
        details.grid_propagate(False)
        tk.Label(details, text="المعاينة والتفاصيل", bg="#ffffff", fg="#293746",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))
        self.preview_label = tk.Label(details, text="اختر صورة لعرضها", bg="#f3f5f7", fg="#8b96a1",
                                      width=29, height=14, font=("Segoe UI", 9), relief="flat")
        self.preview_label.pack(fill="x")
        self.name_label = tk.Label(details, text="—", bg="#ffffff", fg="#24313d", font=("Segoe UI", 9, "bold"),
                                   wraplength=225, justify="left", anchor="w")
        self.name_label.pack(fill="x", anchor="w", pady=(14, 6))
        self.size_label = tk.Label(details, text="الحجم: —", bg="#ffffff", fg="#65727f", font=("Segoe UI", 9),
                                   wraplength=225, justify="left", anchor="w")
        self.size_label.pack(fill="x", anchor="w", pady=3)
        self.path_label = tk.Label(details, text="المسار: —", bg="#ffffff", fg="#65727f", font=("Segoe UI", 8),
                                   wraplength=225, justify="left", anchor="w")
        self.path_label.pack(fill="x", anchor="w", pady=3)
        self.delete_button = ttk.Button(details, text="حذف الصورة المحددة", command=self._delete_selected, state="disabled")
        self.delete_button.pack(fill="x", side="bottom", pady=(12, 0))

        footer = tk.Frame(self.root, bg="#e9edf1", height=30, padx=16)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, textvariable=self.status_var, bg="#e9edf1", fg="#52606d",
                 font=("Segoe UI", 8), anchor="w").pack(fill="both", expand=True)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="اختر مجلدًا لفحص الصور")
        if folder:
            normalized = os.path.normcase(os.path.abspath(folder))
            existing = {os.path.normcase(os.path.abspath(self.roots_list.get(i))) for i in range(self.roots_list.size())}
            if normalized not in existing:
                self.roots_list.insert(tk.END, os.path.abspath(folder))

    def _remove_roots(self) -> None:
        for index in reversed(self.roots_list.curselection()):
            self.roots_list.delete(index)

    def _scan_selected(self) -> None:
        selected = self.roots_list.curselection()
        indices = selected if selected else range(self.roots_list.size())
        roots = [self.roots_list.get(i) for i in indices]
        if not roots:
            messagebox.showinfo(APP_NAME, "أضف مجلدًا واحدًا على الأقل، أو اختر فحص جميع الأقراص.")
            return
        self._start_scan(roots)

    def _scan_all_drives(self) -> None:
        drives = [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]
        if not drives:
            messagebox.showinfo(APP_NAME, "لم يتم العثور على أقراص Windows. أضف مجلدًا يدويًا.")
            return
        self.roots_list.delete(0, tk.END)
        for drive in drives:
            self.roots_list.insert(tk.END, drive)
        self._start_scan(drives)

    def _rescan(self) -> None:
        if self.roots_list.size():
            self._scan_selected()
        elif self.paths:
            self._start_scan([os.path.splitdrive(p)[0] + "\\" if os.name == "nt" else os.path.dirname(p) for p in self.paths[:1]])
        else:
            self._scan_all_drives()

    def _start_scan(self, roots: list[str]) -> None:
        if self.scan_cancel:
            self.scan_cancel.set()
        self.scan_id += 1
        scan_id = self.scan_id
        cancel = threading.Event()
        self.scan_cancel = cancel
        self.paths.clear()
        self.visible_paths.clear()
        self.selected_path = None
        self.thumb_cache.clear()
        self.failed_thumbnails.clear()
        self.pending_images.clear()
        self._clear_details()
        self._draw_gallery()
        self.status_var.set("جارٍ الفحص في الخلفية…")
        threading.Thread(target=self._scan_worker, args=(scan_id, roots[:], cancel), daemon=True,
                         name=f"scanner-{scan_id}").start()

    def _scan_worker(self, scan_id: int, roots: list[str], cancel: threading.Event) -> None:
        batch: list[str] = []
        count = 0
        last_update = time.monotonic()
        seen: set[str] = set()
        for root in roots:
            if cancel.is_set():
                break
            if not os.path.exists(root):
                continue
            for current, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=lambda _e: None):
                if cancel.is_set():
                    break
                # Avoid following directory symlinks/reparse-point-like links.
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(current, d))]
                for filename in files:
                    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    path = os.path.abspath(os.path.join(current, filename))
                    key = os.path.normcase(path)
                    if key in seen:
                        continue
                    seen.add(key)
                    batch.append(path)
                    count += 1
                    if len(batch) >= 120:
                        self.result_queue.put(("scan_batch", scan_id, batch))
                        batch = []
                    now = time.monotonic()
                    if now - last_update > 1.0:
                        self.result_queue.put(("scan_progress", scan_id, count, current))
                        last_update = now
        if batch and not cancel.is_set():
            self.result_queue.put(("scan_batch", scan_id, batch))
        self.result_queue.put(("scan_done", scan_id, count, cancel.is_set()))

    def _image_worker(self) -> None:
        while True:
            task = self.image_queue.get()
            if task is None:
                return
            kind, path = task
            try:
                size = THUMB_SIZE if kind == "thumb" else PREVIEW_SIZE
                with Image.open(path) as source:
                    # Decode only the requested bounded rendition, never retain the source image.
                    try:
                        source.draft("RGB", size)
                    except Exception:
                        pass
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail(size, Image.Resampling.LANCZOS)
                    if image.mode not in ("RGB", "RGBA"):
                        image = image.convert("RGBA")
                    output = io.BytesIO()
                    image.save(output, format="PNG", optimize=False)
                    data = output.getvalue()
                self.result_queue.put(("image", kind, path, data, None))
            except Exception as exc:
                self.result_queue.put(("image", kind, path, None, str(exc)))

    def _poll_results(self) -> None:
        if self.closed:
            return
        try:
            while True:
                item = self.result_queue.get_nowait()
                kind = item[0]
                if kind == "scan_batch":
                    _, scan_id, batch = item
                    if scan_id == self.scan_id:
                        self.paths.extend(batch)
                        self._apply_search(reset_scroll=False)
                        self._draw_gallery()
                elif kind == "scan_progress":
                    _, scan_id, count, current = item
                    if scan_id == self.scan_id:
                        self.status_var.set(f"جارٍ الفحص… عُثر على {count:,} صورة | {current}")
                elif kind == "scan_done":
                    _, scan_id, count, cancelled = item
                    if scan_id == self.scan_id:
                        self._apply_search(reset_scroll=False)
                        self._draw_gallery()
                        if cancelled:
                            self.status_var.set(f"توقف الفحص. عُثر على {len(self.paths):,} صورة حتى الآن.")
                        else:
                            self.status_var.set(f"اكتمل الفحص — {len(self.paths):,} صورة")
                elif kind == "image":
                    _, image_kind, path, data, error = item
                    self.pending_images.discard((image_kind, path))
                    if data:
                        photo = ImageTk.PhotoImage(Image.open(io.BytesIO(data)))
                        if image_kind == "thumb":
                            self.failed_thumbnails.discard(path)
                            self.thumb_cache[path] = photo
                            self.thumb_cache.move_to_end(path)
                            while len(self.thumb_cache) > CACHE_LIMIT:
                                self.thumb_cache.popitem(last=False)
                            self._draw_gallery()
                        elif path == self.selected_path:
                            self.preview_photo = photo
                            self.preview_label.configure(image=photo, text="")
                    else:
                        if image_kind == "thumb":
                            self.failed_thumbnails.add(path)
                            self._draw_gallery()
                        elif image_kind == "preview" and path == self.selected_path:
                            self.preview_label.configure(image="", text="تعذرت قراءة الصورة")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_results)

    def _schedule_search(self, _event=None) -> None:
        if self.search_after:
            self.root.after_cancel(self.search_after)
        self.search_after = self.root.after(160, self._apply_search)

    def _apply_search(self, reset_scroll: bool = True) -> None:
        self.search_after = None
        query = self.search_var.get().strip().casefold()
        self.visible_paths = [p for p in self.paths if query in os.path.basename(p).casefold()] if query else self.paths[:]
        if self.selected_path not in self.visible_paths:
            self.selected_path = None
            self._clear_details()
        if reset_scroll:
            self.canvas.yview_moveto(0)
        self._draw_gallery()
        suffix = f" — بحث: {len(self.visible_paths):,}" if query else ""
        if not (self.scan_cancel and not self.scan_cancel.is_set() and self.status_var.get().startswith("جارٍ")):
            self.status_var.set(f"{len(self.visible_paths):,} صورة{suffix}")

    def _on_canvas_configure(self, _event=None) -> None:
        width = max(1, self.canvas.winfo_width())
        columns = max(1, width // TILE_W)
        if columns != self.columns:
            self.columns = columns
        self._draw_gallery()

    def _on_mousewheel(self, event) -> None:
        delta = -1 * int(event.delta / 120) if event.delta else 0
        if delta:
            self.canvas.yview_scroll(delta * 3, "units")
            self._draw_gallery()

    def _scroll_canvas(self, *args) -> None:
        self.canvas.yview(*args)
        self._draw_gallery()

    def _draw_gallery(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        total = len(self.visible_paths)
        if not total:
            self.canvas.configure(scrollregion=(0, 0, max(1, self.canvas.winfo_width()), 1))
            if self.paths:
                self.canvas.create_text(max(100, self.canvas.winfo_width() // 2), 70, text="لا توجد نتائج مطابقة للبحث",
                                        fill="#7d8995", font=("Segoe UI", 10))
            return
        width = max(1, self.canvas.winfo_width())
        cols = max(1, width // TILE_W)
        self.columns = cols
        rows = (total + cols - 1) // cols
        full_height = rows * TILE_H + 14
        self.canvas.configure(scrollregion=(0, 0, width, full_height))
        top = max(0, int(self.canvas.canvasy(0) // TILE_H))
        bottom = min(rows, int((self.canvas.canvasy(self.canvas.winfo_height()) // TILE_H) + 2))
        start = max(0, top * cols)
        end = min(total, bottom * cols)
        for index in range(start, end):
            row, col = divmod(index, cols)
            x = col * TILE_W + 9
            y = row * TILE_H + 8
            path = self.visible_paths[index]
            selected = path == self.selected_path
            self.canvas.create_rectangle(x, y, x + TILE_W - 12, y + TILE_H - 8,
                                         fill="#eef5ff" if selected else "#ffffff",
                                         outline="#4c91e2" if selected else "#e8edf1", width=1)
            photo = self.thumb_cache.get(path)
            image_x = x + (TILE_W - 12) // 2
            image_y = y + 53
            if photo:
                self.canvas.create_image(image_x, image_y, image=photo, anchor="center")
            else:
                self.canvas.create_rectangle(image_x - 46, image_y - 34, image_x + 46, image_y + 34,
                                             fill="#f1f4f6", outline="")
                failed = path in self.failed_thumbnails
                self.canvas.create_text(image_x, image_y, text="×" if failed else "…",
                                        fill="#c07878" if failed else "#9aa5af", font=("Segoe UI", 18))
                if not failed:
                    self._request_image("thumb", path)
            name = os.path.basename(path)
            self.canvas.create_text(x + 7, y + 100, text=name, width=TILE_W - 28, anchor="nw",
                                    fill="#344252", font=("Segoe UI", 8), justify="center")

    def _request_image(self, kind: str, path: str) -> None:
        key = (kind, path)
        if key not in self.pending_images:
            self.pending_images.add(key)
            self.image_queue.put((kind, path))

    def _on_canvas_click(self, event) -> None:
        if not self.visible_paths:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        col = int(x // TILE_W)
        row = int(y // TILE_H)
        index = row * self.columns + col
        if 0 <= index < len(self.visible_paths) and col < self.columns:
            self._select_path(self.visible_paths[index])
            self._draw_gallery()

    def _select_path(self, path: str) -> None:
        self.selected_path = path
        self.preview_photo = None
        self.preview_label.configure(image="", text="جارٍ تحميل المعاينة…")
        self.name_label.configure(text=os.path.basename(path))
        self.path_label.configure(text=f"المسار: {path}")
        try:
            size = os.path.getsize(path)
            self.size_label.configure(text=f"الحجم: {self._format_size(size)}")
            self.delete_button.configure(state="normal")
        except OSError:
            self.size_label.configure(text="الحجم: غير متاح")
            self.delete_button.configure(state="disabled")
        self._request_image("preview", path)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("بايت", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "بايت" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} بايت"

    def _clear_details(self) -> None:
        if hasattr(self, "preview_label"):
            self.preview_photo = None
            self.preview_label.configure(image="", text="اختر صورة لعرضها")
            self.name_label.configure(text="—")
            self.size_label.configure(text="الحجم: —")
            self.path_label.configure(text="المسار: —")
            self.delete_button.configure(state="disabled")

    def _delete_selected(self) -> None:
        path = self.selected_path
        if not path:
            return
        if not messagebox.askyesno(APP_NAME, f"هل أنت متأكد من حذف هذه الصورة نهائيًا؟\n\n{path}", icon="warning"):
            return
        try:
            os.remove(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"تعذر حذف الصورة:\n{exc}")
            return
        self.paths = [p for p in self.paths if os.path.normcase(p) != os.path.normcase(path)]
        self.thumb_cache.pop(path, None)
        self.selected_path = None
        self._clear_details()
        self._apply_search(reset_scroll=False)
        self.status_var.set("تم حذف الصورة بعد تأكيدك.")

    def _close(self) -> None:
        self.closed = True
        if self.scan_cancel:
            self.scan_cancel.set()
        self.image_queue.put(None)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AllPhotosMini(root)
    root.mainloop()


if __name__ == "__main__":
    main()
