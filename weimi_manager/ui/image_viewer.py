from __future__ import annotations

import io
import queue
import tkinter as tk
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PIL import Image, ImageOps, ImageTk

_IMAGE_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="langlang-image-view",
)
_DOWNLOAD_CACHE: dict[str, bytes] = {}
_PENDING: set[str] = set()


def valid_remote_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def request_image_bytes(
    owner: tk.Misc,
    url: str,
    callback: Callable[[bytes | None], None],
) -> None:
    url = str(url or "").strip()
    if not valid_remote_url(url):
        callback(None)
        return
    cached = _DOWNLOAD_CACHE.get(url)
    if cached is not None:
        owner.after(0, lambda: callback(cached))
        return
    if url in _PENDING:
        owner.after(
            80,
            lambda: request_image_bytes(owner, url, callback),
        )
        return
    _PENDING.add(url)
    results: queue.Queue[bytes | None] = queue.Queue(maxsize=1)

    def work() -> None:
        data: bytes | None = None
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                    )
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read(12 * 1024 * 1024 + 1)
            if len(data) > 12 * 1024 * 1024:
                data = None
            elif data:
                _DOWNLOAD_CACHE[url] = data
        except Exception:
            data = None
        results.put(data)

    _IMAGE_EXECUTOR.submit(work)

    def poll() -> None:
        if not owner.winfo_exists():
            return
        try:
            data = results.get_nowait()
        except queue.Empty:
            owner.after(80, poll)
            return
        _PENDING.discard(url)
        callback(data)

    owner.after(80, poll)


def photo_from_bytes(
    data: bytes,
    max_size: tuple[int, int],
) -> ImageTk.PhotoImage:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image = ImageOps.contain(
        image,
        max_size,
        method=Image.Resampling.LANCZOS,
    )
    return ImageTk.PhotoImage(image)


def bind_remote_image_label(
    label: tk.Label,
    url: str,
    *,
    size: tuple[int, int] = (76, 76),
) -> None:
    url = str(url or "").strip()
    if not valid_remote_url(url):
        return
    label.configure(cursor="hand2")
    label.bind(
        "<Button-1>",
        lambda event: open_image_viewer(
            label,
            url,
            title="商品图片",
        ),
    )
    bind_hover_preview(label, url)

    def loaded(data: bytes | None) -> None:
        if not data or not label.winfo_exists():
            return
        try:
            photo = photo_from_bytes(data, size)
        except Exception:
            return
        label.configure(
            image=photo,
            text="",
            width=size[0],
            height=size[1],
        )
        label._remote_photo = photo  # type: ignore[attr-defined]

    request_image_bytes(label, url, loaded)


def bind_hover_preview(widget: tk.Widget, url: str) -> None:
    state: dict[str, object] = {}

    def hide(_event=None) -> None:
        after_id = state.pop("after_id", None)
        if after_id is not None:
            try:
                widget.after_cancel(str(after_id))
            except tk.TclError:
                pass
        popup = state.pop("popup", None)
        if isinstance(popup, tk.Toplevel) and popup.winfo_exists():
            popup.destroy()

    def show() -> None:
        state.pop("after_id", None)
        if not widget.winfo_exists() or not valid_remote_url(url):
            return
        popup = tk.Toplevel(widget)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="#111827")
        x = widget.winfo_pointerx() + 18
        y = widget.winfo_pointery() + 18
        popup.geometry(f"+{x}+{y}")
        label = tk.Label(
            popup,
            text="正在加载…",
            bg="#111827",
            fg="white",
            width=34,
            height=13,
        )
        label.pack(padx=2, pady=2)
        state["popup"] = popup

        def loaded(data: bytes | None) -> None:
            if not popup.winfo_exists():
                return
            if not data:
                popup.destroy()
                return
            try:
                photo = photo_from_bytes(data, (320, 320))
            except Exception:
                popup.destroy()
                return
            label.configure(
                image=photo,
                text="",
                width=photo.width(),
                height=photo.height(),
            )
            label._preview_photo = photo  # type: ignore[attr-defined]

        request_image_bytes(popup, url, loaded)

    def enter(_event=None) -> None:
        hide()
        state["after_id"] = widget.after(280, show)

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Button-1>", lambda _event: hide(), add="+")


def bind_tree_row_images(
    tree: tk.Widget,
    url_for_row: Callable[[str], str],
    *,
    thumbnail_size: tuple[int, int] = (46, 46),
) -> None:
    photo_refs: dict[str, ImageTk.PhotoImage] = {}
    tree._tree_image_photo_refs = photo_refs  # type: ignore[attr-defined]
    hover_after: dict[str, str] = {"id": ""}
    hover_popup: dict[str, tk.Toplevel | None] = {"popup": None}

    def hide_popup(_event=None) -> None:
        after_id = hover_after.pop("id", "")
        if after_id:
            try:
                tree.after_cancel(after_id)
            except tk.TclError:
                pass
        popup = hover_popup.pop("popup", None)
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def show_popup(url: str) -> None:
        hover_after.pop("id", None)
        if not url:
            return
        popup = tk.Toplevel(tree)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="#111827")
        popup.geometry(
            f"+{tree.winfo_pointerx() + 16}+{tree.winfo_pointery() + 16}"
        )
        label = tk.Label(
            popup,
            text="正在加载…",
            bg="#111827",
            fg="white",
            width=32,
            height=12,
        )
        label.pack(padx=2, pady=2)
        hover_popup["popup"] = popup

        def loaded(data: bytes | None) -> None:
            if not popup.winfo_exists():
                return
            if not data:
                popup.destroy()
                return
            try:
                photo = photo_from_bytes(data, (300, 300))
            except Exception:
                popup.destroy()
                return
            label.configure(
                image=photo,
                text="",
                width=photo.width(),
                height=photo.height(),
            )
            label._preview_photo = photo  # type: ignore[attr-defined]

        request_image_bytes(popup, url, loaded)

    def motion(event) -> None:
        hide_popup()
        if getattr(tree, "identify_region", lambda *_args: "")(event.x, event.y) != "cell":
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        url = url_for_row(iid)
        if valid_remote_url(url):
            hover_after["id"] = tree.after(
                320,
                lambda current=url: show_popup(current),
            )

    def click(event) -> None:
        hide_popup()
        iid = tree.identify_row(event.y)
        if not iid:
            return
        column = tree.identify_column(event.x)
        if column not in {"#0", "#1"}:
            return
        url = url_for_row(iid)
        if valid_remote_url(url):
            open_image_viewer(tree, url, title="规格图片")

    tree.bind("<Motion>", motion, add="+")
    tree.bind("<Leave>", hide_popup, add="+")
    tree.bind("<Button-1>", click, add="+")


def set_tree_row_thumbnail(
    tree: tk.Widget,
    iid: str,
    url: str,
    *,
    size: tuple[int, int] = (46, 46),
) -> None:
    if not valid_remote_url(url):
        return

    def loaded(data: bytes | None) -> None:
        if not data or not tree.winfo_exists() or not tree.exists(iid):
            return
        try:
            photo = photo_from_bytes(data, size)
        except Exception:
            return
        tree.item(iid, image=photo)
        refs = getattr(tree, "_tree_image_photo_refs", None)
        if isinstance(refs, dict):
            refs[iid] = photo

    request_image_bytes(tree, url, loaded)


class ImageViewer(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        url: str,
        title: str = "图片预览",
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.original: Image.Image | None = None
        self.rotation = 0
        self.scale = 1.0
        self.fit_mode = True
        self._photo: ImageTk.PhotoImage | None = None
        self.title(title)
        host = parent.winfo_toplevel()
        self.geometry(
            f"{max(680, host.winfo_width() - 30)}x"
            f"{max(520, host.winfo_height() - 30)}"
            f"+{host.winfo_rootx() + 15}+{host.winfo_rooty() + 15}"
        )
        self.transient(host)
        self.configure(bg="#05070b")
        self.grab_set()
        self.focus_force()
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self,
            bg="#05070b",
            highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._render())
        self.canvas.bind("<MouseWheel>", self._wheel)
        toolbar = tk.Frame(self, bg="#1f2937", padx=12, pady=5)
        toolbar.place(relx=0.5, rely=1.0, anchor="s", y=-18)
        for text, command in (
            ("－", lambda: self._zoom(0.82)),
            ("＋", lambda: self._zoom(1.22)),
            ("适应", self._fit),
            ("⛶", self._toggle_fullscreen),
            ("↻", self._rotate),
            ("↺", self._reset),
        ):
            tk.Button(
                toolbar,
                text=text,
                command=command,
                bg="#1f2937",
                fg="white",
                activebackground="#374151",
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=12,
                pady=5,
                cursor="hand2",
                font=("Microsoft YaHei UI", 11),
            ).pack(side="left", padx=3)
        tk.Button(
            self,
            text="×",
            command=self.destroy,
            bg="#111827",
            fg="white",
            activebackground="#b91c1c",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=11,
            pady=3,
            cursor="hand2",
            font=("Microsoft YaHei UI", 12),
        ).place(relx=1.0, x=-14, y=14, anchor="ne")
        self.bind("<Escape>", lambda _event: self.destroy())
        request_image_bytes(self, url, self._loaded)

    def _loaded(self, data: bytes | None) -> None:
        if not data:
            self.canvas.create_text(
                320,
                220,
                text="图片加载失败",
                fill="white",
                font=("Microsoft YaHei UI", 14),
            )
            return
        try:
            self.original = ImageOps.exif_transpose(
                Image.open(io.BytesIO(data))
            ).convert("RGB")
        except Exception:
            self.canvas.create_text(
                320,
                220,
                text="图片格式不受支持",
                fill="white",
                font=("Microsoft YaHei UI", 14),
            )
            return
        self._render()

    def _render(self) -> None:
        if self.original is None:
            return
        width = max(320, self.canvas.winfo_width())
        height = max(240, self.canvas.winfo_height())
        image = self.original
        if self.rotation:
            image = image.rotate(self.rotation, expand=True)
        if self.fit_mode:
            ratio = min(width / image.width, (height - 80) / image.height)
        else:
            ratio = self.scale
        target = (
            max(1, int(image.width * ratio)),
            max(1, int(image.height * ratio)),
        )
        resized = image.resize(target, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(
            width / 2,
            (height - 20) / 2,
            image=self._photo,
            anchor="center",
        )

    def _zoom(self, factor: float) -> None:
        if self.original is None:
            return
        if self.fit_mode:
            width = max(320, self.canvas.winfo_width())
            height = max(240, self.canvas.winfo_height())
            self.scale = min(
                width / self.original.width,
                (height - 80) / self.original.height,
            )
            self.fit_mode = False
        self.scale = min(6.0, max(0.08, self.scale * factor))
        self._render()

    def _wheel(self, event) -> None:
        self._zoom(1.12 if event.delta > 0 else 0.89)

    def _fit(self) -> None:
        self.fit_mode = True
        self._render()

    def _toggle_fullscreen(self) -> None:
        fullscreen = bool(self.attributes("-fullscreen"))
        self.attributes("-fullscreen", not fullscreen)
        self.after(40, self._render)

    def _rotate(self) -> None:
        self.rotation = (self.rotation + 90) % 360
        self.fit_mode = True
        self._render()

    def _reset(self) -> None:
        self.rotation = 0
        self.scale = 1.0
        self.fit_mode = True
        self._render()


def open_image_viewer(
    parent: tk.Widget,
    url: str,
    *,
    title: str = "图片预览",
) -> ImageViewer | None:
    if not valid_remote_url(url):
        return None
    return ImageViewer(parent, url, title)
