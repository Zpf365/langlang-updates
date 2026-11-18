from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR


class MobileAutomationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    score: float
    box: tuple[tuple[float, float], ...]

    @property
    def center(self) -> tuple[int, int]:
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return round(sum(xs) / len(xs)), round(sum(ys) / len(ys))

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))


def _find_adb() -> str:
    explicit = os.environ.get("LANGLANG_ADB", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    resolved = shutil.which("adb")
    if resolved:
        return resolved
    for root in (
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        r"D:\GPT\android-toolchain\android-sdk",
    ):
        if not root:
            continue
        candidate = Path(root) / "platform-tools" / (
            "adb.exe" if os.name == "nt" else "adb"
        )
        if candidate.is_file():
            return str(candidate)
    raise MobileAutomationError("找不到 adb，请确认安卓调试连接可用")


class AdbMobile:
    def __init__(self, adb_path: str = "", serial: str = "") -> None:
        self.adb_path = adb_path or _find_adb()
        self.serial = serial.strip()

    def _command(self, *args: str) -> list[str]:
        command = [self.adb_path]
        if self.serial:
            command.extend(["-s", self.serial])
        command.extend(args)
        return command

    def _run(
        self,
        *args: str,
        binary: bool = False,
        timeout: int = 30,
    ) -> bytes | str:
        process = subprocess.run(
            self._command(*args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if process.returncode != 0:
            error = process.stderr.decode("utf-8", errors="replace").strip()
            raise MobileAutomationError(error or "ADB 命令失败")
        if binary:
            return process.stdout
        return process.stdout.decode("utf-8", errors="replace")

    def devices(self) -> list[str]:
        output = str(self._run("devices"))
        return [
            line.split()[0]
            for line in output.splitlines()[1:]
            if line.strip().endswith("device")
        ]

    def unlocked(self) -> bool:
        output = str(self._run("shell", "dumpsys", "window"))
        return (
            "mDreamingLockscreen=false" in output
            and "isKeyguardShowing=false" in output
        )

    def screenshot(self) -> np.ndarray:
        raw = self._run("exec-out", "screencap", "-p", binary=True, timeout=20)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise MobileAutomationError("手机截图读取失败")
        return image

    def tap(self, x: float, y: float) -> None:
        self._run("shell", "input", "tap", str(round(x)), str(round(y)))

    def back(self) -> None:
        self._run("shell", "input", "keyevent", "KEYCODE_BACK")

    def paste(self) -> None:
        self._run("shell", "input", "keyevent", "279")

    def launch_wechat(self) -> None:
        self._run(
            "shell",
            "monkey",
            "-p",
            "com.tencent.mm",
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
            timeout=20,
        )

    def set_clipboard(self, text: str) -> None:
        remote_value = "'" + text.replace("'", "'\\''") + "'"
        self._run(
            "shell",
            "am",
            "broadcast",
            "-a",
            "com.langlang.mobile.SET_CLIPBOARD",
            "-n",
            "com.langlang.mobile/.ClipboardReceiver",
            "--es",
            "text",
            remote_value,
            timeout=20,
        )

    def complete_and_next(self) -> None:
        self._run(
            "shell",
            "am",
            "startservice",
            "-n",
            "com.langlang.mobile/.QueueOverlayService",
            "-a",
            "com.langlang.mobile.COMPLETE_AND_NEXT",
            timeout=20,
        )


class MobileWechatAutomation:
    def __init__(
        self,
        adb: AdbMobile | None = None,
        *,
        ocr: RapidOCR | None = None,
        progress: Callable[[str], None] | None = None,
        debug_dir: Path | None = None,
    ) -> None:
        self.adb = adb or AdbMobile()
        self.ocr = ocr or RapidOCR()
        self.progress = progress or (lambda _message: None)
        self.debug_dir = debug_dir

    def recognize(self) -> tuple[np.ndarray, list[OcrLine]]:
        image = self.adb.screenshot()
        raw, _elapsed = self.ocr(image)
        lines: list[OcrLine] = []
        for item in raw or []:
            try:
                box = tuple(
                    (float(point[0]), float(point[1]))
                    for point in item[0]
                )
                lines.append(
                    OcrLine(
                        text=str(item[1]).strip(),
                        score=float(item[2]),
                        box=box,
                    )
                )
            except (TypeError, ValueError, IndexError):
                continue
        return image, lines

    def _debug(self, image: np.ndarray, label: str) -> None:
        if self.debug_dir is None:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^0-9A-Za-z_-]+", "-", label).strip("-") or "screen"
        cv2.imwrite(str(self.debug_dir / f"{int(time.time())}-{name}.png"), image)

    @staticmethod
    def _matches(line: OcrLine, target: str, *, exact: bool = False) -> bool:
        if exact:
            return line.text == target
        return target in line.text

    def _find(
        self,
        lines: Iterable[OcrLine],
        target: str,
        *,
        exact: bool = False,
        min_y: float = 0,
    ) -> OcrLine | None:
        candidates = [
            line for line in lines
            if self._matches(line, target, exact=exact)
            and line.center[1] >= min_y
        ]
        return max(candidates, key=lambda line: line.score) if candidates else None

    def _wait_for(
        self,
        target: str,
        *,
        timeout: float = 12,
        exact: bool = False,
        min_y: float = 0,
        label: str = "",
    ) -> tuple[np.ndarray, list[OcrLine], OcrLine]:
        deadline = time.monotonic() + timeout
        last_image: np.ndarray | None = None
        last_lines: list[OcrLine] = []
        while time.monotonic() < deadline:
            image, lines = self.recognize()
            last_image, last_lines = image, lines
            match = self._find(lines, target, exact=exact, min_y=min_y)
            if match is not None:
                return image, lines, match
            time.sleep(0.7)
        if last_image is not None:
            self._debug(last_image, label or f"missing-{target}")
        visible = " / ".join(line.text for line in last_lines[-12:])
        raise MobileAutomationError(
            f"等待“{target}”超时。当前可见：{visible or '未识别到文字'}"
        )

    def run_queue(
        self,
        links: list[str],
        *,
        quantity: int = 1,
    ) -> dict[str, object]:
        links = [str(link).strip() for link in links if str(link).strip()]
        if not links:
            raise MobileAutomationError("没有可执行的链接")
        if not self.adb.unlocked():
            raise MobileAutomationError("手机已锁屏，请先解锁并保持屏幕常亮")
        if not self.adb.devices():
            raise MobileAutomationError("手机未连接或 USB 调试未授权")

        self.adb.launch_wechat()
        time.sleep(1.5)
        self._open_file_transfer_assistant()

        completed = 0
        for index, link in enumerate(links, 1):
            self.progress(f"第 {index}/{len(links)} 条：发送链接")
            self._send_link(link)
            self.progress(f"第 {index}/{len(links)} 条：打开商品")
            self._open_product()
            self.progress(f"第 {index}/{len(links)} 条：选择规格和数量")
            self._add_to_cart(max(1, int(quantity)))
            self.progress(f"第 {index}/{len(links)} 条：返回文件传输助手")
            self._return_to_file_transfer_assistant()
            if index < len(links):
                self.adb.complete_and_next()
                time.sleep(1.8)
                self.adb.set_clipboard(links[index])
            else:
                self.adb.complete_and_next()
            completed += 1
        return {"completed": completed, "total": len(links)}

    def _open_file_transfer_assistant(self) -> None:
        image, lines = self.recognize()
        title = self._find(lines, "文件传输助手")
        if title is not None and title.center[1] < 400:
            return

        home_mark = self._find(lines, "微信", exact=True)
        for _attempt in range(4):
            if home_mark is not None:
                break
            self.adb.back()
            time.sleep(0.7)
            image, lines = self.recognize()
            title = self._find(lines, "文件传输助手")
            if title is not None and title.center[1] < 400:
                return
            home_mark = self._find(lines, "微信", exact=True)
        search_result = self._find(lines, "文件传输助手", min_y=450)
        if home_mark is not None:
            height, width = image.shape[:2]
            self.progress("打开微信搜索")
            self.adb.tap(0.83 * width, 0.058 * height)
            time.sleep(0.8)
            self.adb.set_clipboard("文件传输助手")
            self.adb.paste()
            time.sleep(1.2)
            search_result = self._wait_for(
                "文件传输助手",
                timeout=8,
                min_y=350,
                label="file-helper-search",
            )[2]
        if search_result is None:
            raise MobileAutomationError("没有找到“文件传输助手”入口")
        self.adb.tap(*search_result.center)
        self._wait_for(
            "文件传输助手",
            timeout=8,
            label="file-helper-chat",
        )

    def _send_link(self, link: str) -> None:
        self.adb.set_clipboard(link)
        image, lines = self.recognize()
        height, width = image.shape[:2]
        send = self._find(lines, "发送", exact=True)
        if send is not None:
            self.adb.tap(0.45 * width, send.center[1])
        else:
            if self._find(lines, "按住 说话") is not None:
                self.adb.tap(0.048 * width, 0.945 * height)
                time.sleep(0.8)
            self.adb.tap(0.45 * width, 0.945 * height)
            time.sleep(0.6)
        self.adb.paste()
        time.sleep(0.8)
        _image, _lines, send = self._wait_for(
            "发送",
            timeout=5,
            exact=True,
            label="send-button",
        )
        self.adb.tap(*send.center)
        token = link.rsplit("/", 1)[-1]
        sent_line = self._wait_for(
            token,
            timeout=8,
            label="sent-link",
        )[2]
        time.sleep(0.4)
        self.adb.tap(*sent_line.center)

    def _open_product(self) -> None:
        _image, _lines, gift = self._wait_for(
            "送朋友",
            timeout=18,
            label="product-page",
        )
        image, _lines = self.recognize()
        height, width = image.shape[:2]
        # The cart button is immediately left of “送朋友”.
        self.adb.tap(gift.bounds[0] - 0.18 * width, gift.center[1])
        self._wait_for(
            "加入购物车",
            timeout=8,
            label="sku-panel",
        )

    def _add_to_cart(self, quantity: int) -> None:
        image, lines = self.recognize()
        height, width = image.shape[:2]
        sku = next(
            (
                line
                for line in sorted(lines, key=lambda item: item.center[1])
                if (
                    line.center[1] > 0.38 * height
                    and line.center[1] < 0.82 * height
                    and line.bounds[0] < 0.55 * width
                    and (
                        "【" in line.text
                        or "深灰" in line.text
                        or "棕色" in line.text
                        or "黑色" in line.text
                    )
                )
            ),
            None,
        )
        if sku is None:
            raise MobileAutomationError("规格面板中没有找到可选规格")
        self.adb.tap(*sku.center)
        time.sleep(0.35)

        quantity_label = self._find(lines, "数量")
        if quantity_label is None:
            raise MobileAutomationError("没有找到数量选择器")
        y = quantity_label.center[1]
        # Reset the selector to its minimum, then add the requested count.
        for _ in range(12):
            self.adb.tap(0.75 * width, y)
        for _ in range(max(0, quantity - 1)):
            self.adb.tap(0.93 * width, y)
        time.sleep(0.3)

        image, lines = self.recognize()
        add = self._find(lines, "加入购物车")
        if add is None:
            raise MobileAutomationError("没有找到“加入购物车”按钮")
        self.adb.tap(*add.center)
        time.sleep(2.2)
        _image, current = self.recognize()
        failures = ("库存不足", "请选择", "不可购买", "购买数量")
        if any(
            failure in line.text
            for line in current
            for failure in failures
        ):
            raise MobileAutomationError(
                "加入购物车失败：" +
                " / ".join(line.text for line in current if any(
                    failure in line.text for failure in failures
                ))
            )

    def _return_to_file_transfer_assistant(self) -> None:
        for _attempt in range(5):
            self.adb.back()
            time.sleep(0.8)
            _image, lines = self.recognize()
            title = self._find(lines, "文件传输助手")
            if title is not None and title.center[1] < 400:
                return
        raise MobileAutomationError("返回后没有回到文件传输助手")


def ocr_image(path: str | Path) -> list[str]:
    image = cv2.imread(str(path))
    if image is None:
        raise MobileAutomationError(f"无法读取图片：{path}")
    raw, _elapsed = RapidOCR()(image)
    return [str(item[1]).strip() for item in raw or []]
