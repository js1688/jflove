#!/usr/bin/env python3
"""X11 输入注入（XTest）：点击 / 输入 / 清空 / 聚焦。

为什么需要它：Xvnc 里没有 xdotool/wmctrl 时，验证 GUI 仍要能真实点击、真实键盘输入。
只用 libX11 + libXtst（Fedora 默认自带），不引入 Python 依赖。

用法：
    xinput.py click 718 593 [--display :99]
    xinput.py text 'http://127.0.0.1:8989'
    xinput.py clear
    xinput.py key Escape
    xinput.py focus JFLove

注意：
- 中文无法用 XTest 直接输入（keysym 映射不稳定）；中文用例改用剪贴板或 CDP。
- 大写/符号需要 Shift；本脚本内置映射，避免把 `Test@2026` 打成 `test22026`。
"""
from __future__ import annotations

import argparse
import ctypes
import time

# 需要 Shift 的符号 → 对应无 Shift 的键名
SHIFT_SYMBOLS = {
    ":": "semicolon",
    "@": "2",
    "/": "slash",
    ".": "period",
    "-": "minus",
    "_": "minus",
    "+": "equal",
    "=": "equal",
    "?": "slash",
    "!": "1",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    '"': "apostrophe",
    "'": "apostrophe",
    ",": "comma",
    ";": "semicolon",
    "\\": "backslash",
    "|": "backslash",
    "[": "bracketleft",
    "]": "bracketright",
    "{": "bracketleft",
    "}": "bracketright",
    "<": "comma",
    ">": "period",
    "~": "grave",
    "`": "grave",
    " ": "space",
}


class X:
    def __init__(self, display: str) -> None:
        self.x = ctypes.CDLL("libX11.so.6")
        self.xt = ctypes.CDLL("libXtst.so.6")
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XStringToKeysym.restype = ctypes.c_ulong
        self.x.XKeysymToKeycode.restype = ctypes.c_uint
        self.d = ctypes.c_void_p(self.x.XOpenDisplay(display.encode()))
        if not self.d:
            raise SystemExit(f"无法连接 DISPLAY={display}")
        self.shift = self.keycode("Shift_L")

    def keycode(self, name: str) -> int:
        return self.x.XKeysymToKeycode(self.d, self.x.XStringToKeysym(name.encode()))

    def _event(self, code: int, down: bool) -> None:
        self.xt.XTestFakeKeyEvent(self.d, code, 1 if down else 0, 0)
        self.x.XFlush(self.d)
        time.sleep(0.02)

    def press(self, name: str, shift: bool = False) -> None:
        code = self.keycode(name)
        if shift:
            self._event(self.shift, True)
        self._event(code, True)
        self._event(code, False)
        if shift:
            self._event(self.shift, False)

    def text(self, value: str) -> None:
        for ch in value:
            if ch in SHIFT_SYMBOLS:
                self.press(SHIFT_SYMBOLS[ch], shift=True)
            elif ch.isupper():
                self.press(ch.lower(), shift=True)
            else:
                self.press(ch)

    def move(self, x: int, y: int) -> None:
        self.xt.XTestFakeMotionEvent(self.d, 0, x, y, 0)
        self.x.XFlush(self.d)
        time.sleep(0.15)

    def click(self, x: int, y: int, button: int = 1) -> None:
        self.move(x, y)
        self.xt.XTestFakeButtonEvent(self.d, button, 1, 0)
        self.xt.XTestFakeButtonEvent(self.d, button, 0, 0)
        self.x.XFlush(self.d)
        time.sleep(0.25)

    def clear(self) -> None:
        # Ctrl+A → BackSpace
        self._event(self.keycode("Control_L"), True)
        self._event(self.keycode("a"), True)
        self._event(self.keycode("a"), False)
        self._event(self.keycode("Control_L"), False)
        self.press("BackSpace")

    def focus(self, name: str) -> bool:
        """按窗口标题聚焦。递归搜索：KWin 会把客户端窗口 reparent 到 frame 下。"""
        root = self.x.XDefaultRootWindow(self.d)

        def walk(wid: int, depth: int) -> int | None:
            title = ctypes.c_char_p()
            if self.x.XFetchName(self.d, wid, ctypes.byref(title)) and title.value:
                if name in title.value.decode(errors="replace"):
                    return wid
            if depth <= 0:
                return None
            r = ctypes.c_ulong()
            p = ctypes.c_ulong()
            children = ctypes.POINTER(ctypes.c_ulong)()
            n = ctypes.c_uint()
            if not self.x.XQueryTree(
                self.d, wid, ctypes.byref(r), ctypes.byref(p),
                ctypes.byref(children), ctypes.byref(n),
            ):
                return None
            for i in range(n.value):
                hit = walk(children[i], depth - 1)
                if hit:
                    return hit
            return None

        target = walk(root, 4)
        if target is None:
            return False
        self.x.XSetInputFocus(self.d, target, 2, 0)  # RevertToParent
        self.x.XFlush(self.d)
        time.sleep(0.3)
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="X11 输入注入（XTest）")
    parser.add_argument("--display", default=":99")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_click = sub.add_parser("click")
    p_click.add_argument("x", type=int)
    p_click.add_argument("y", type=int)
    p_click.add_argument("--button", type=int, default=1)

    p_text = sub.add_parser("text")
    p_text.add_argument("value")

    p_key = sub.add_parser("key")
    p_key.add_argument("name")

    sub.add_parser("clear")

    p_focus = sub.add_parser("focus")
    p_focus.add_argument("name")

    args = parser.parse_args()
    x = X(args.display)

    if args.cmd == "click":
        x.click(args.x, args.y, args.button)
    elif args.cmd == "text":
        x.text(args.value)
    elif args.cmd == "key":
        x.press(args.name)
    elif args.cmd == "clear":
        x.clear()
    elif args.cmd == "focus":
        if not x.focus(args.name):
            raise SystemExit(f"找不到窗口：{args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
