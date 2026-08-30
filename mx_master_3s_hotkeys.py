"""MX Master 3S hotkeys for Windows, without Logi Options+.

Mappings:
    Middle / wheel click -> Ctrl+W
    Back button          -> Escape
    Forward button       -> Right Arrow
    Thumb wheel          -> Reversed horizontal scrolling

First run:
    Double-click the file, or run: py mx_master_3s_hotkeys.py

The first run installs it for the current user, starts it silently, and enables
Always Run after Windows sign-in. The notification-area menu contains
"Check for Updates", "Always Run", and "Uninstall...". Updates are downloaded
from the public release repository, verified, installed, and restarted
automatically. Uninstall requires explicit confirmation.

The program uses only Python's standard library. Windows' low-level mouse hook
does not identify the physical mouse, so the mappings apply to the same buttons
on every connected mouse.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path


if sys.platform != "win32":
    raise SystemExit("This program supports Windows only.")

import winreg  # noqa: E402  (available only on Windows)


# Mouse hook messages and constants
WH_MOUSE_LL = 14
WM_NULL = 0x0000
WM_QUIT = 0x0012
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_CONTEXTMENU = 0x007B
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
XBUTTON1 = 0x0001  # Back
XBUTTON2 = 0x0002  # Forward

# Input constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_HWHEEL = 0x1000
SIDE_SCROLL_EXTRA_INFO = 0x4D585352  # "MXSR"
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_RIGHT = 0x27
VK_W = 0x57

# Notification area constants
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IDI_APPLICATION = 32512
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
ID_ALWAYS_RUN = 1001
ID_UNINSTALL = 1002
ID_CHECK_FOR_UPDATES = 1003
MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040
MB_ICONERROR = 0x00000010
MB_ICONQUESTION = 0x00000020
MB_YESNO = 0x00000004
MB_DEFBUTTON2 = 0x00000100
IDYES = 6
FW_BOLD = 700
TRANSPARENT = 1
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020

# Startup and single-instance identity
APP_NAME = "MX Master 3S Hotkeys"
APP_VERSION = "1.1.1"
UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/AdolfTWN/"
    "windows-utilities/main/latest.json"
)
UPDATE_DOWNLOAD_PREFIX = (
    "https://raw.githubusercontent.com/AdolfTWN/"
    "windows-utilities/"
)
UPDATE_TIMEOUT_SECONDS = 15
MAX_MANIFEST_BYTES = 16 * 1024
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
RUN_VALUE_NAME = "MXMaster3SHotkeys"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOW_CLASS_NAME = "MXMaster3SHotkeysTrayWindow"
WINDOW_TITLE = "MX Master 3S Hotkeys"
MUTEX_NAME = r"Local\MXMaster3SHotkeys.SingleInstance"
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
MUTEX_MODIFY_STATE = 0x00000001
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080


ULONG_PTR = wintypes.WPARAM
LRESULT = wintypes.LPARAM


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", INPUT_UNION))


WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = (
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    )


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    )


class ICONINFO(ctypes.Structure):
    _fields_ = (
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    )


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def _declare_win32_functions() -> None:
    user32.SetWindowsHookExW.argtypes = (
        ctypes.c_int,
        HOOKPROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    )
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = (
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    )
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.DispatchMessageW.restype = LRESULT
    user32.PostThreadMessageW.argtypes = (
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.PostMessageW.restype = wintypes.BOOL
    user32.DefWindowProcW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    )
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.LoadIconW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR)
    user32.LoadIconW.restype = wintypes.HICON
    user32.CreateIconIndirect.argtypes = (ctypes.POINTER(ICONINFO),)
    user32.CreateIconIndirect.restype = wintypes.HICON
    user32.DestroyIcon.argtypes = (wintypes.HICON,)
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.FillRect.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.HBRUSH,
    )
    user32.FillRect.restype = ctypes.c_int
    user32.DrawTextW.argtypes = (
        wintypes.HDC,
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(wintypes.RECT),
        wintypes.UINT,
    )
    user32.DrawTextW.restype = ctypes.c_int
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = (
        wintypes.HMENU,
        wintypes.UINT,
        ULONG_PTR,
        wintypes.LPCWSTR,
    )
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = (
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.LPVOID,
    )
    user32.TrackPopupMenu.restype = wintypes.UINT
    user32.DestroyMenu.argtypes = (wintypes.HMENU,)
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = (ctypes.c_int,)
    user32.MessageBoxW.argtypes = (
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    )
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    shell32.Shell_NotifyIconW.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(NOTIFYICONDATAW),
    )
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateCompatibleBitmap.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
    )
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
    )
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.CreateSolidBrush.argtypes = (wintypes.COLORREF,)
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    gdi32.SetBkMode.argtypes = (wintypes.HDC, ctypes.c_int)
    gdi32.SetBkMode.restype = ctypes.c_int
    gdi32.SetTextColor.argtypes = (wintypes.HDC, wintypes.COLORREF)
    gdi32.SetTextColor.restype = wintypes.COLORREF
    gdi32.CreateFontW.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPCWSTR,
    )
    gdi32.CreateFontW.restype = wintypes.HFONT


_declare_win32_functions()


def _rgb(red: int, green: int, blue: int) -> int:
    """Build a Win32 COLORREF (0x00BBGGRR)."""
    return red | (green << 8) | (blue << 16)


def create_mx_icon(size: int = 64) -> wintypes.HICON | None:
    """Create the teal-and-navy Mx notification icon entirely in memory."""
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    color_bitmap = gdi32.CreateCompatibleBitmap(screen_dc, size, size)
    user32.ReleaseDC(None, screen_dc)
    if not memory_dc or not color_bitmap:
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if color_bitmap:
            gdi32.DeleteObject(color_bitmap)
        return None

    old_bitmap = gdi32.SelectObject(memory_dc, color_bitmap)
    navy_brush = gdi32.CreateSolidBrush(_rgb(17, 35, 54))
    teal_brush = gdi32.CreateSolidBrush(_rgb(18, 151, 163))
    accent_brush = gdi32.CreateSolidBrush(_rgb(74, 237, 222))
    font = gdi32.CreateFontW(
        -34,
        0,
        0,
        0,
        FW_BOLD,
        0,
        0,
        0,
        1,
        0,
        0,
        5,
        0,
        "Segoe UI",
    )
    old_font = None
    icon = None
    mask_bitmap = None
    try:
        full_rect = wintypes.RECT(0, 0, size, size)
        inner_rect = wintypes.RECT(3, 3, size - 3, size - 3)
        accent_rect = wintypes.RECT(17, 53, size - 17, 56)
        user32.FillRect(memory_dc, ctypes.byref(full_rect), navy_brush)
        user32.FillRect(memory_dc, ctypes.byref(inner_rect), teal_brush)
        user32.FillRect(memory_dc, ctypes.byref(accent_rect), accent_brush)

        if font:
            old_font = gdi32.SelectObject(memory_dc, font)
        gdi32.SetBkMode(memory_dc, TRANSPARENT)
        gdi32.SetTextColor(memory_dc, _rgb(255, 255, 255))
        text_rect = wintypes.RECT(0, -2, size, size - 5)
        user32.DrawTextW(
            memory_dc,
            "Mx",
            -1,
            ctypes.byref(text_rect),
            DT_CENTER | DT_VCENTER | DT_SINGLELINE,
        )

        # A zero monochrome mask makes every pixel of the color bitmap opaque.
        row_bytes = ((size + 15) // 16) * 2
        mask_bits = (ctypes.c_ubyte * (row_bytes * size))()
        mask_bitmap = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)
        if mask_bitmap:
            icon_info = ICONINFO(True, 0, 0, mask_bitmap, color_bitmap)
            icon = user32.CreateIconIndirect(ctypes.byref(icon_info))
    finally:
        if old_font:
            gdi32.SelectObject(memory_dc, old_font)
        gdi32.SelectObject(memory_dc, old_bitmap)
        if font:
            gdi32.DeleteObject(font)
        if navy_brush:
            gdi32.DeleteObject(navy_brush)
        if teal_brush:
            gdi32.DeleteObject(teal_brush)
        if accent_brush:
            gdi32.DeleteObject(accent_brush)
        if mask_bitmap:
            gdi32.DeleteObject(mask_bitmap)
        gdi32.DeleteObject(color_bitmap)
        gdi32.DeleteDC(memory_dc)
    return icon


def _key(vk: int, key_up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if key_up else 0
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, flags, 0, 0))


def send_keys(*virtual_keys: int) -> None:
    """Press keys in order and release them in reverse order."""
    events = [_key(vk) for vk in virtual_keys]
    events.extend(_key(vk, key_up=True) for vk in reversed(virtual_keys))
    event_array = (INPUT * len(events))(*events)
    user32.SendInput(len(events), event_array, ctypes.sizeof(INPUT))


def send_horizontal_wheel(delta: int) -> None:
    """Send one horizontal wheel event with the supplied signed delta."""
    event = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(
            0,
            0,
            delta & 0xFFFFFFFF,
            MOUSEEVENTF_HWHEEL,
            0,
            SIDE_SCROLL_EXTRA_INFO,
        ),
    )
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


class MouseRemapper:
    def __init__(self) -> None:
        self._hook: wintypes.HHOOK | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: OSError | None = None
        self._callback = HOOKPROC(self._mouse_hook)

    @staticmethod
    def _xbutton(mouse_data: int) -> int:
        return (mouse_data >> 16) & 0xFFFF

    @staticmethod
    def _wheel_delta(mouse_data: int) -> int:
        return ctypes.c_short((mouse_data >> 16) & 0xFFFF).value

    def _mouse_hook(self, code: int, message: int, data_address: int) -> int:
        if code >= 0:
            if message == WM_MOUSEHWHEEL:
                mouse_event = ctypes.cast(
                    data_address, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents
                if mouse_event.dwExtraInfo != SIDE_SCROLL_EXTRA_INFO:
                    delta = self._wheel_delta(mouse_event.mouseData)
                    if delta:
                        send_horizontal_wheel(-delta)
                    return 1

            if message == WM_MBUTTONDOWN:
                send_keys(VK_CONTROL, VK_W)
                return 1
            if message == WM_MBUTTONUP:
                return 1

            if message in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                mouse_data = ctypes.cast(
                    data_address, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents.mouseData
                button = self._xbutton(mouse_data)

                if button == XBUTTON1:
                    if message == WM_XBUTTONDOWN:
                        send_keys(VK_ESCAPE)
                    return 1

                if button == XBUTTON2:
                    if message == WM_XBUTTONDOWN:
                        send_keys(VK_RIGHT)
                    return 1

        return user32.CallNextHookEx(self._hook, code, message, data_address)

    def _message_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._callback, None, 0)
        if not self._hook:
            self._startup_error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            self._stopped.set()
            return

        self._ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            self._stopped.set()

    def start(self) -> None:
        thread = threading.Thread(
            target=self._message_loop,
            name="MXMasterMouseHook",
            daemon=True,
        )
        thread.start()
        self._ready.wait()
        if self._startup_error:
            raise self._startup_error

    def stop(self) -> None:
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._stopped.wait(timeout=2)


class TrayApplication:
    def __init__(self, remapper: MouseRemapper) -> None:
        self.remapper = remapper
        self.hwnd: wintypes.HWND | None = None
        self._notify_data: NOTIFYICONDATAW | None = None
        self._icon: wintypes.HICON | None = None
        self._owns_icon = False
        self._uninstalling = False
        self._update_in_progress = False
        self._wndproc = WNDPROC(self._window_proc)

    def _show_message(self, text: str, title: str = APP_NAME, error: bool = False) -> None:
        user32.MessageBoxW(
            self.hwnd,
            text,
            title,
            MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION),
        )

    def _check_for_updates(self) -> None:
        if self._update_in_progress:
            return
        self._update_in_progress = True

        def worker() -> None:
            try:
                update = _download_update()
                if update is None:
                    self._show_message(
                        f"You already have the latest version ({APP_VERSION})."
                    )
                    return

                staged_path, new_version = update
                destination = _installed_script_path()
                subprocess.Popen(
                    [
                        str(_pythonw_path()),
                        str(destination),
                        "--apply-update",
                        str(staged_path),
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    close_fds=True,
                )
                self._show_message(
                    f"Version {new_version} was downloaded successfully.\n\n"
                    "The app will now restart to finish the update."
                )
                self.request_exit()
            except (OSError, RuntimeError, ValueError) as error:
                self._show_message(
                    f"Unable to check for updates:\n\n{error}",
                    title="Update Error",
                    error=True,
                )
            finally:
                self._update_in_progress = False

        threading.Thread(
            target=worker,
            name="MXMasterUpdateCheck",
            daemon=True,
        ).start()

    def _confirm_uninstall(self) -> None:
        if not self.hwnd:
            return
        choice = user32.MessageBoxW(
            self.hwnd,
            "Remove MX Master 3S Hotkeys?\n\n"
            "The hotkeys will stop now and will not run after your next "
            "Windows sign-in.",
            "Confirm Uninstall",
            MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2,
        )
        if choice != IDYES:
            return
        try:
            _remove_startup_registry()
        except OSError as error:
            user32.MessageBoxW(
                self.hwnd,
                f"Unable to remove Always Run:\n\n{error}",
                APP_NAME,
                0x00000010,
            )
            return
        self._uninstalling = True
        user32.DestroyWindow(self.hwnd)

    def _confirm_always_run(self) -> None:
        if not self.hwnd:
            return
        try:
            _write_startup_registry()
            user32.MessageBoxW(
                self.hwnd,
                "Always Run is enabled.\n\n"
                "MX Master 3S Hotkeys will start automatically after every "
                "Windows sign-in.",
                APP_NAME,
                MB_OK | MB_ICONINFORMATION,
            )
        except OSError as error:
            user32.MessageBoxW(
                self.hwnd,
                f"Unable to enable Always Run:\n\n{error}",
                APP_NAME,
                0x00000010,
            )

    def _show_menu(self) -> None:
        if not self.hwnd:
            return
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(
                menu,
                MF_STRING,
                ID_CHECK_FOR_UPDATES,
                "Check for Updates",
            )
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(
                menu,
                MF_STRING | MF_CHECKED,
                ID_ALWAYS_RUN,
                "Always Run",
            )
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, ID_UNINSTALL, "Uninstall...")
            cursor = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            user32.SetForegroundWindow(self.hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RETURNCMD | TPM_NONOTIFY,
                cursor.x,
                cursor.y,
                0,
                self.hwnd,
                None,
            )
            if command == ID_CHECK_FOR_UPDATES:
                self._check_for_updates()
            elif command == ID_ALWAYS_RUN:
                self._confirm_always_run()
            elif command == ID_UNINSTALL:
                self._confirm_uninstall()
            if self.hwnd:
                user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)

    def _window_proc(
        self,
        hwnd: wintypes.HWND,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == WM_TRAYICON:
            if lparam in (WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0

        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0

        if message == WM_DESTROY:
            if self._notify_data is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data))
            if self._owns_icon and self._icon:
                user32.DestroyIcon(self._icon)
            self.remapper.stop()
            if self._uninstalling:
                _remove_installed_files()
            self.hwnd = None
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def request_exit(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def run(self) -> int:
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW(
            0,
            self._wndproc,
            0,
            0,
            instance,
            None,
            None,
            None,
            None,
            WINDOW_CLASS_NAME,
        )
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())

        self.hwnd = user32.CreateWindowExW(
            0,
            WINDOW_CLASS_NAME,
            WINDOW_TITLE,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        icon = create_mx_icon()
        if icon:
            self._owns_icon = True
        else:
            icon_resource = ctypes.cast(
                ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR
            )
            icon = user32.LoadIconW(None, icon_resource)
        self._icon = icon
        notify_data = NOTIFYICONDATAW()
        notify_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        notify_data.hWnd = self.hwnd
        notify_data.uID = 1
        notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        notify_data.uCallbackMessage = WM_TRAYICON
        notify_data.hIcon = icon
        notify_data.szTip = f"{APP_NAME} {APP_VERSION} — Always Run"
        self._notify_data = notify_data

        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data)):
            raise ctypes.WinError(ctypes.get_last_error())

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        return 0


def _pythonw_path() -> Path:
    executable = Path(sys.executable).resolve()
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.exists():
        raise FileNotFoundError(f"pythonw.exe was not found beside {executable}")
    return pythonw


def _installed_script_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not available.")
    return Path(local_app_data) / "MXMaster3SHotkeys" / "mx_master_3s_hotkeys.pyw"


def _read_url(url: str, maximum_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Cache-Control": "no-cache",
            "User-Agent": f"MXMaster3SHotkeys/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=UPDATE_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise RuntimeError(f"GitHub returned HTTP {response.status}.")
            data = response.read(maximum_bytes + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub returned HTTP {error.code}.") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error
    if len(data) > maximum_bytes:
        raise RuntimeError("The update response was unexpectedly large.")
    return data


def _version_tuple(version: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid update version: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not url.startswith(UPDATE_DOWNLOAD_PREFIX):
        raise RuntimeError("The update manifest contains an untrusted download URL.")


def _download_update() -> tuple[Path, str] | None:
    manifest_bytes = _read_url(UPDATE_MANIFEST_URL, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("The update manifest is invalid.") from error
    if not isinstance(manifest, dict):
        raise RuntimeError("The update manifest is invalid.")

    version = manifest.get("version")
    download_url = manifest.get("url")
    expected_sha256 = manifest.get("sha256")
    if not all(isinstance(value, str) for value in (version, download_url, expected_sha256)):
        raise RuntimeError("The update manifest is missing required fields.")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise RuntimeError("The update manifest contains an invalid checksum.")
    _validate_download_url(download_url)
    if _version_tuple(version) <= _version_tuple(APP_VERSION):
        return None

    script_bytes = _read_url(download_url, MAX_SCRIPT_BYTES)
    actual_sha256 = hashlib.sha256(script_bytes).hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        raise RuntimeError("The downloaded update failed checksum verification.")

    try:
        source = script_bytes.decode("utf-8")
        compile(source, "mx_master_3s_hotkeys.pyw", "exec")
    except (UnicodeDecodeError, SyntaxError) as error:
        raise RuntimeError("The downloaded update is not a valid Python program.") from error
    version_match = re.search(
        r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']\s*$',
        source,
        re.MULTILINE,
    )
    if not version_match or version_match.group(1) != version:
        raise RuntimeError("The downloaded update version does not match its manifest.")

    destination = _installed_script_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix="mx_master_update_",
        suffix=".pyw",
        dir=destination.parent,
    )
    staged_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as temporary_file:
            temporary_file.write(script_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise
    return staged_path, version


def apply_update(staged_path: Path) -> int:
    destination = _installed_script_path()
    try:
        staged_path = staged_path.resolve(strict=True)
        if staged_path.parent != destination.parent.resolve():
            raise RuntimeError("The staged update is in an unexpected location.")
        if not staged_path.name.startswith("mx_master_update_"):
            raise RuntimeError("The staged update has an unexpected name.")

        _wait_until_stopped(timeout_seconds=10.0)
        _wait_for_instance_release(timeout_ms=10000)
        os.replace(staged_path, destination)
        _write_startup_registry()
        subprocess.Popen(
            [str(_pythonw_path()), str(destination)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        return 0
    except (OSError, RuntimeError) as error:
        user32.MessageBoxW(
            None,
            f"Unable to install the downloaded update:\n\n{error}",
            "Update Error",
            MB_OK | MB_ICONERROR,
        )
        return 1
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_startup_registry() -> None:
    destination = _installed_script_path()
    command = subprocess.list2cmdline([str(_pythonw_path()), str(destination)])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)


def _remove_startup_registry() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
    except FileNotFoundError:
        pass


def _remove_installed_files() -> None:
    destination = _installed_script_path()
    try:
        destination.unlink()
    except FileNotFoundError:
        pass
    try:
        destination.parent.rmdir()
    except OSError:
        pass


def _request_running_app_exit() -> bool:
    hwnd = user32.FindWindowW(WINDOW_CLASS_NAME, WINDOW_TITLE)
    if not hwnd:
        return False
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return True


def _wait_until_stopped(timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not user32.FindWindowW(WINDOW_CLASS_NAME, WINDOW_TITLE):
            return
        time.sleep(0.1)


def _wait_for_instance_release(timeout_ms: int = 4000) -> bool:
    mutex = kernel32.OpenMutexW(
        SYNCHRONIZE | MUTEX_MODIFY_STATE,
        False,
        MUTEX_NAME,
    )
    if not mutex:
        return True
    try:
        result = kernel32.WaitForSingleObject(mutex, timeout_ms)
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            kernel32.ReleaseMutex(mutex)
            return True
        return False
    finally:
        kernel32.CloseHandle(mutex)


def _start_installed_app(timeout_seconds: float = 8.0) -> None:
    destination = _installed_script_path()
    process = subprocess.Popen(
        [str(_pythonw_path()), str(destination)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if user32.FindWindowW(WINDOW_CLASS_NAME, WINDOW_TITLE):
            return
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"The background app exited before it became ready (code {exit_code})."
            )
        time.sleep(0.1)
    raise RuntimeError("The background app did not become ready in time.")


def install_startup() -> int:
    source = Path(__file__).resolve()
    destination = _installed_script_path()

    _request_running_app_exit()
    _wait_until_stopped()
    if not _wait_for_instance_release():
        raise RuntimeError(
            "The previous background instance did not stop. Sign out of Windows "
            "once, then run this file again."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination:
        shutil.copy2(source, destination)

    _write_startup_registry()

    _start_installed_app()
    user32.MessageBoxW(
        None,
        "MX Master 3S Hotkeys is installed and active.\n\n"
        "The notification icon will remain available near the Windows clock.",
        APP_NAME,
        MB_OK | MB_ICONINFORMATION,
    )
    print(f"{APP_NAME} installed and started.")
    print("It will start automatically after you sign in to Windows.")
    return 0


def uninstall_startup() -> int:
    _request_running_app_exit()
    _wait_until_stopped()
    _remove_startup_registry()
    _remove_installed_files()

    print(f"{APP_NAME} stopped and removed from startup.")
    return 0


def run_tray_app() -> int:
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(mutex)
        return 0

    remapper = MouseRemapper()
    tray = TrayApplication(remapper)

    def request_exit(_signum: int, _frame: object) -> None:
        tray.request_exit()

    signal.signal(signal.SIGINT, request_exit)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_exit)

    try:
        remapper.start()
        return tray.run()
    except OSError as error:
        remapper.stop()
        user32.MessageBoxW(
            None,
            f"Unable to start {APP_NAME}:\n\n{error}",
            APP_NAME,
            0x00000010,
        )
        return 1
    finally:
        kernel32.CloseHandle(mutex)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--install",
        action="store_true",
        help="install for this user, start silently, and enable login startup",
    )
    action.add_argument(
        "--uninstall",
        action="store_true",
        help="stop the app and remove it from login startup",
    )
    action.add_argument(
        "--apply-update",
        type=Path,
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.install:
        try:
            return install_startup()
        except (OSError, RuntimeError) as error:
            user32.MessageBoxW(
                None,
                f"Unable to install or start {APP_NAME}:\n\n{error}",
                APP_NAME,
                MB_OK | MB_ICONERROR,
            )
            return 1
    if args.uninstall:
        return uninstall_startup()
    if args.apply_update:
        return apply_update(args.apply_update)
    if Path(__file__).resolve() != _installed_script_path().resolve():
        try:
            return install_startup()
        except (OSError, RuntimeError) as error:
            user32.MessageBoxW(
                None,
                f"Unable to install or start {APP_NAME}:\n\n{error}",
                APP_NAME,
                MB_OK | MB_ICONERROR,
            )
            return 1
    _write_startup_registry()
    return run_tray_app()


if __name__ == "__main__":
    raise SystemExit(main())
