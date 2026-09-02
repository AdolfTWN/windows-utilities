"""MX Master 3S hotkeys for Windows, without Logi Options+.

Mappings:
    Middle / wheel click -> Ctrl+W
    Back button          -> Escape
    Forward button       -> Right Arrow
    Thumb wheel          -> Native HID++ inverted horizontal scrolling

First run:
    Double-click the file, or run: py mx_master_3s_hotkeys.py

The first run installs it for the current user and starts it silently.
Auto Start is disabled by default and is enabled only by explicitly checking
"Auto Start" in the notification-area menu. Unchecking it removes login startup.
The menu also contains "Check for Updates" and "Uninstall...". Updates are downloaded
from the public release repository, verified, installed, and restarted
automatically. Uninstall requires explicit confirmation.

The program uses only Python's standard library. Windows' low-level mouse hook
does not identify the physical mouse, so the mappings apply to the same buttons
on every connected mouse. Thumb-wheel inversion is configured directly on
the MX Master 3S through HID++; wheel input is never suppressed or re-injected.
Quit Options+ completely before starting this app. Use --portable to run
without installing or enabling login startup. Legacy automatic startup entries
are removed unless Auto Start was explicitly enabled in version 1.1.6 or later.
Native wheel settings remain
on the device after exit; --restore-thumb-wheel BACKUP.json restores a backup.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
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
WM_THUMB_STATUS = WM_APP + 2
WM_POWERBROADCAST = 0x0218
WM_DEVICECHANGE = 0x0219
XBUTTON1 = 0x0001  # Back
XBUTTON2 = 0x0002  # Forward

# Input constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_HWHEEL = 0x1000
LLMHF_INJECTED = 0x00000001
SIDE_SCROLL_EXTRA_INFO = 0x4D585352  # "MXSR"
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_RIGHT = 0x27
VK_W = 0x57

# Notification area constants
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
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
APP_VERSION = "1.1.6"
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


class MouseRemapper:
    """Remap buttons only. Wheel events always pass through unchanged."""

    def __init__(self) -> None:
        self._hook: wintypes.HHOOK | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: OSError | None = None
        self._callback = HOOKPROC(self._mouse_hook)
        self._keys: queue.Queue[tuple[int, ...]] = queue.Queue(maxsize=64)
        self._key_thread: threading.Thread | None = None
        self._suppressed: set[int] = set()

    def _send_key_events(self) -> None:
        while not self._stopped.is_set():
            try:
                keys = self._keys.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self._stopped.is_set():
                send_keys(*keys)

    def _mouse_hook(self, code: int, message: int, data_address: int) -> int:
        # Do not inspect, swallow, reverse, or synthesize any wheel event.
        if code >= 0 and message in (
            WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP
        ):
            event = ctypes.cast(data_address, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not event.flags & LLMHF_INJECTED:
                button = 0 if message in (WM_MBUTTONDOWN, WM_MBUTTONUP) else (event.mouseData >> 16) & 0xFFFF
                keys = {0: (VK_CONTROL, VK_W), XBUTTON1: (VK_ESCAPE,), XBUTTON2: (VK_RIGHT,)}.get(button)
                if keys is not None:
                    if message in (WM_MBUTTONDOWN, WM_XBUTTONDOWN):
                        try:
                            self._keys.put_nowait(keys)
                        except queue.Full:
                            pass  # Preserve the physical click if the sender is busy.
                        else:
                            self._suppressed.add(button)
                            return 1
                    elif button in self._suppressed:
                        self._suppressed.discard(button)
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
        message = wintypes.MSG()
        try:
            self._key_thread = threading.Thread(target=self._send_key_events, name="MXMasterKeySender", daemon=True)
            self._key_thread.start()
            self._ready.set()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            self._stopped.set()

    def start(self) -> None:
        thread = threading.Thread(target=self._message_loop, name="MXMasterMouseHook", daemon=True)
        thread.start()
        if not self._ready.wait(timeout=3):
            raise RuntimeError("Button hook startup timed out")
        if self._startup_error:
            raise self._startup_error

    def stop(self) -> None:
        self._stopped.set()
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._key_thread is not None:
            self._key_thread.join(timeout=0.5)


class NativeThumbUnavailable(RuntimeError):
    """The receiver or mouse is temporarily unavailable; retry later."""


class NativeThumbStopped(RuntimeError):
    """Configuration was cancelled because the app is shutting down."""


class NativeThumbRecovery:
    """Serialize bounded HID++ attempts outside the UI and input callback."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._suspended = threading.Event()
        self._hwnd: wintypes.HWND | None = None
        self._thread: threading.Thread | None = None
        self.status = "Waiting for receiver"
        self._last_detail = ""

    def _publish(self, status: str, detail: str = "") -> None:
        changed = (status, detail) != (self.status, self._last_detail)
        self.status, self._last_detail = status, detail
        if changed:
            try:
                path = _installed_script_path().parent / "native-thumb-status.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({
                    "version": APP_VERSION, "status": status, "detail": detail,
                    "updated": time.time(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                pass  # A diagnostic write must not terminate recovery.
        if self._hwnd and not self._stop.is_set():
            user32.PostMessageW(self._hwnd, WM_THUMB_STATUS, 0, 0)

    def start(self, hwnd: wintypes.HWND) -> None:
        self._hwnd = hwnd
        self._thread = threading.Thread(target=self._run, name="MXNativeThumbRecovery", daemon=True)
        self._thread.start()

    def request(self) -> None:
        self._wake.set()

    def suspend(self) -> None:
        self._suspended.set()
        self._wake.set()

    def resume(self) -> None:
        self._suspended.clear()
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        delays = (2, 5, 10, 30, 60)
        failures = 0
        delay = 0
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            if self._suspended.is_set():
                delay = None
                continue
            try:
                _configure_native_thumb_wheel(stop_event=self._stop)
            except NativeThumbStopped:
                return
            except NativeThumbUnavailable as error:
                delay = delays[min(failures, len(delays) - 1)]
                failures += 1
                self._publish("Waiting for receiver", str(error))
            except (OSError, RuntimeError, ValueError) as error:
                # Unsupported devices, Options+ conflicts and local file errors
                # require user action, not an endless settings-write loop.
                self._publish("Configuration unavailable", str(error))
                delay = None
            else:
                self._publish("Native inversion active")
                failures = 0
                delay = None  # No repeated writes after success.


class TrayApplication:
    def __init__(self, remapper: MouseRemapper, native: NativeThumbRecovery) -> None:
        self.remapper = remapper
        self.native = native
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
            _save_auto_start_consent(False)
        except OSError as error:
            user32.MessageBoxW(
                self.hwnd,
                f"Unable to remove Auto Start:\n\n{error}",
                APP_NAME,
                0x00000010,
            )
            return
        self._uninstalling = True
        user32.DestroyWindow(self.hwnd)

    def _toggle_auto_start(self) -> None:
        if not self.hwnd:
            return
        try:
            if _auto_start_enabled():
                _remove_startup_registry()
                _save_auto_start_consent(False)
            else:
                # This menu click is the only path that registers startup.
                _write_startup_registry()
                try:
                    _save_auto_start_consent(True)
                except OSError:
                    _remove_startup_registry()
                    raise
        except OSError as error:
            user32.MessageBoxW(
                self.hwnd,
                f"Unable to change Auto Start:\n\n{error}",
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
                MF_STRING | (MF_CHECKED if _auto_start_enabled() else 0),
                ID_ALWAYS_RUN,
                "Auto Start",
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
                self._toggle_auto_start()
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
        if message == WM_POWERBROADCAST:
            if wparam == 0x04:  # PBT_APMSUSPEND
                self.native.suspend()
            elif wparam in (0x06, 0x07, 0x12):  # Resume critical, user, automatic.
                self.native.resume()
            return 1

        if message == WM_DEVICECHANGE and wparam in (0x0007, 0x8000, 0x8004):
            self.native.request()
            return 1

        if message == WM_THUMB_STATUS:
            if self._notify_data is not None:
                self._notify_data.szTip = f"{APP_NAME} {APP_VERSION} — {self.native.status}"[:127]
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._notify_data))
            return 0

        if message == WM_TRAYICON:
            if lparam in (WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0

        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0

        if message == WM_DESTROY:
            self.native.stop()
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
        notify_data.szTip = f"{APP_NAME} {APP_VERSION} — {self.native.status}"[:127]
        self._notify_data = notify_data

        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data)):
            raise ctypes.WinError(ctypes.get_last_error())

        self.native.start(self.hwnd)

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        return 0



def _ensure_options_stopped() -> None:
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        present = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        if not present:
            raise ctypes.WinError(ctypes.get_last_error())
        while present:
            if entry.exe.lower() in ("logioptionsplus_agent.exe", "logioptionsplus_app.exe"):
                raise RuntimeError(
                    "Options+ is running. Fully exit Options+ and its agent before "
                    "starting MX Master 3S Hotkeys; both programs must not control the mouse together."
                )
            present = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def _run_thumb_worker(*arguments: str, stop_event: threading.Event | None = None) -> str:
    worker = subprocess.Popen(
        [str(_pythonw_path()), "-c", _NATIVE_THUMB_WORKER, *arguments],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 12
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise NativeThumbStopped("Application is exiting")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeThumbUnavailable("Native thumb-wheel configuration timed out; waiting to retry.")
            try:
                output, error = worker.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if worker.poll() is None:
            worker.kill()
            try:
                worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    if worker.returncode == 75:
        raise NativeThumbUnavailable(error[-1800:])
    if worker.returncode:
        raise RuntimeError("Unable to configure the native thumb wheel:\n" + error[-1800:])
    return output


def _configure_native_thumb_wheel(*, stop_event: threading.Event | None = None) -> None:
    _ensure_options_stopped()
    directory = _installed_script_path().parent / "thumbwheel-backups"
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / (str(time.time_ns()) + ".json")
    _run_thumb_worker("--native-inverted", "--backup", str(backup), stop_event=stop_event)


_NATIVE_THUMB_WORKER = r'''
"""Configure a Logitech thumb wheel through HID++, without input injection.

Windows only; standard library only. Default: read the current configuration.
Use --native-inverted to enable native HID scrolling with device inversion.
Use --restore BACKUP.json to restore the two saved thumb-wheel settings.
"""
from __future__ import annotations

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import sys
import time

u = c.WinDLL('user32', use_last_error=True)
k = c.WinDLL('kernel32', use_last_error=True)


class Device(c.Structure):
    _fields_ = [('handle', w.HANDLE), ('type', w.DWORD)]


class HidInfo(c.Structure):
    _fields_ = [('vendor', w.DWORD), ('product', w.DWORD), ('version', w.DWORD),
                ('page', w.USHORT), ('usage', w.USHORT)]


class InfoUnion(c.Union):
    _fields_ = [('hid', HidInfo), ('padding', c.c_byte * 24)]


class DeviceInfo(c.Structure):
    _fields_ = [('size', w.DWORD), ('type', w.DWORD), ('data', InfoUnion)]


class Overlapped(c.Structure):
    _fields_ = [('internal', c.c_size_t), ('internal_high', c.c_size_t),
                ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]


u.GetRawInputDeviceList.argtypes = [c.POINTER(Device), c.POINTER(w.UINT), w.UINT]
u.GetRawInputDeviceList.restype = w.UINT
u.GetRawInputDeviceInfoW.argtypes = [w.HANDLE, w.UINT, w.LPVOID, c.POINTER(w.UINT)]
u.GetRawInputDeviceInfoW.restype = w.UINT
k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
k.CreateFileW.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
k.CreateEventW.restype = w.HANDLE
for name in ('ReadFile', 'WriteFile'):
    getattr(k, name).argtypes = [w.HANDLE, w.LPVOID, w.DWORD, c.POINTER(w.DWORD), c.POINTER(Overlapped)]
    getattr(k, name).restype = w.BOOL
k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k.WaitForSingleObject.restype = w.DWORD
k.GetOverlappedResult.argtypes = [w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL]
k.GetOverlappedResult.restype = w.BOOL
k.CancelIoEx.argtypes = [w.HANDLE, c.POINTER(Overlapped)]


def interfaces():
    count = w.UINT()
    if u.GetRawInputDeviceList(None, c.byref(count), c.sizeof(Device)) == 0xffffffff:
        raise c.WinError(c.get_last_error())
    devices = (Device * count.value)()
    result = u.GetRawInputDeviceList(devices, c.byref(count), c.sizeof(Device))
    if result == 0xffffffff:
        raise c.WinError(c.get_last_error())
    for device in devices[:result]:
        if device.type != 2:
            continue
        info = DeviceInfo(); info.size = c.sizeof(info)
        size = w.UINT(c.sizeof(info))
        if u.GetRawInputDeviceInfoW(device.handle, 0x2000000b, c.byref(info), c.byref(size)) == 0xffffffff:
            continue
        hid = info.data.hid
        # Long HID++ reports: Logitech vendor page, usage 2 (20 bytes).
        if (hid.vendor, hid.page, hid.usage) != (0x046d, 0xff00, 2):
            continue
        length = w.UINT()
        u.GetRawInputDeviceInfoW(device.handle, 0x20000007, None, c.byref(length))
        name = c.create_unicode_buffer(length.value + 1)
        if u.GetRawInputDeviceInfoW(device.handle, 0x20000007, name, c.byref(length)) == 0xffffffff:
            continue
        yield {'path': name.value, 'product': hid.product}


class Hidpp:
    def __init__(self, path):
        self.pending = []
        self.handle = k.CreateFileW(path, 0xc0000000, 3, None, 3, 0x40000000, None)
        if self.handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())

    def close(self):
        if self.handle:
            k.CloseHandle(self.handle)
            self.handle = None

    def io(self, data=None, timeout_ms=1000):
        buf = c.create_string_buffer(data, len(data)) if data is not None else c.create_string_buffer(20)
        done = w.DWORD()
        ov = Overlapped(); ov.event = k.CreateEventW(None, True, False, None)
        if not ov.event:
            raise c.WinError(c.get_last_error())
        release_event = True
        try:
            fn = k.WriteFile if data is not None else k.ReadFile
            ok = fn(self.handle, buf, len(buf), c.byref(done), c.byref(ov))
            if not ok:
                error = c.get_last_error()
                if error != 997:
                    raise c.WinError(error)
                if k.WaitForSingleObject(ov.event, timeout_ms) != 0:
                    k.CancelIoEx(self.handle, c.byref(ov))
                    if k.WaitForSingleObject(ov.event, 250) != 0:
                        # Cancellation is asynchronous. Keep its memory alive until
                        # process exit; never free a pending OVERLAPPED or buffer.
                        self.pending.append((ov, buf, done))
                        release_event = False
                    raise TimeoutError('HID++ request timed out')
                if not k.GetOverlappedResult(self.handle, c.byref(ov), c.byref(done), False):
                    raise c.WinError(c.get_last_error())
            if data is not None and done.value != len(data):
                raise OSError('Incomplete HID++ write')
            return bytes(buf[:done.value])
        finally:
            if release_event:
                k.CloseHandle(ov.event)

    def request(self, index, feature, function, params=b'', timeout=.8):
        header = bytes((0x11, index, feature, function | 0x0d))
        self.io((header + params).ljust(20, b'\0'))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = self.io(timeout_ms=max(1, int((deadline-time.monotonic())*1000)))
            if reply[:4] == header:
                return reply[4:]
            if len(reply) >= 7 and reply[1] == index and reply[2] == 0x8f and reply[3:5] == header[2:4]:
                raise RuntimeError(f'HID++ error 0x{reply[5]:02x}')
        raise TimeoutError('Matching HID++ response not received')

    def feature(self, index, feature_id):
        return self.request(index, 0, 0, feature_id.to_bytes(2, 'big'))[0]

    def name(self, index):
        feature = self.feature(index, 0x0005)
        if not feature:
            return 'Unknown Logitech device'
        length = self.request(index, feature, 0)[0]
        result = b''
        while len(result) < length:
            result += self.request(index, feature, 0x10, bytes((len(result),)))
        return result[:length].decode('utf-8', errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--native-inverted', action='store_true')
    actions.add_argument('--restore', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--index', type=int, default=5, help='Receiver slot observed during diagnostics')
    args = parser.parse_args()
    devices = list(interfaces())
    if not devices:
        print('Logitech receiver interface is not available yet.', file=sys.stderr)
        raise SystemExit(75)
    if len(devices) != 1:
        raise RuntimeError(f'Expected one Logitech long-report interface, found {len(devices)}')
    endpoint = devices[0]
    device = Hidpp(endpoint['path'])
    try:
        feature = device.feature(args.index, 0x2150)
        if not feature:
            raise RuntimeError('Selected device does not expose THUMB_WHEEL (0x2150)')
        name = device.name(args.index)
        if 'MX Master 3S' not in name:
            raise RuntimeError(f'Refusing to change unexpected device: {name!r}')
        before = device.request(args.index, feature, 0x10)
        state = dict(name=name, product=endpoint['product'], index=args.index,
                     feature_id='0x2150', feature_index=feature,
                     mode=before[0], inverted=bool(before[1] & 1),
                     raw_status=before.hex(), info=device.request(args.index, feature, 0).hex())
        print(json.dumps({'before': state}, indent=2), flush=True)
        already_native_inverted = args.native_inverted and (before[0], before[1] & 1) == (0, 1)
        if args.backup and not already_native_inverted:
            with args.backup.open('x', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        target = None
        if args.native_inverted:
            if not args.backup:
                raise RuntimeError('--backup is required before changing device settings')
            target = (0, 1)
        elif args.restore:
            saved = json.loads(args.restore.read_text(encoding='utf-8'))
            if (saved['name'],saved['product'],saved['index']) != (name,endpoint['product'],args.index):
                raise RuntimeError('Backup does not match the selected device')
            target = (saved['mode'], int(saved['inverted']))
            if target[0] not in (0, 1):
                raise RuntimeError('Invalid reporting mode in backup')
        if target and (before[0], before[1] & 1) != target:
            device.request(args.index, feature, 0x20, bytes((*target, 0)))
            after = device.request(args.index, feature, 0x10)
            actual = (after[0], after[1] & 1)
            if actual != target:
                raise RuntimeError(f'Readback mismatch: expected {target}, received {actual}')
            print(json.dumps({'after': {'mode':actual[0], 'inverted':bool(actual[1]), 'raw_status':after.hex()}}, indent=2),flush=True)
    finally:
        device.close()


try:
    main()
except TimeoutError as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(75)
except OSError as error:
    if getattr(error, 'winerror', None) in (2, 6, 21, 31, 121, 995, 1167):
        print(str(error), file=sys.stderr)
        raise SystemExit(75)
    raise
'''

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
    # A portable launch must register the file that actually exists and is running.
    destination = Path(__file__).resolve()
    command = subprocess.list2cmdline([str(_pythonw_path()), str(destination)])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)


def _auto_start_consent_path() -> Path:
    return _installed_script_path().parent / "auto-start-consent.json"


def _auto_start_opted_in() -> bool:
    try:
        value = json.loads(_auto_start_consent_path().read_text(encoding="utf-8"))
        return isinstance(value, dict) and value.get("enabled") is True
    except (FileNotFoundError, ValueError, UnicodeError):
        return False


def _save_auto_start_consent(enabled: bool) -> None:
    path = _auto_start_consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    temporary.replace(path)


def _auto_start_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            command, kind = winreg.QueryValueEx(key, RUN_VALUE_NAME)
        # Reflect the real entry, including one pointing to an older/portable
        # copy, so the user can always uncheck an existing registration.
        return kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and bool(command)
    except OSError:
        return False


def _apply_startup_default() -> None:
    # Older versions registered Run automatically, so an existing entry is not
    # evidence of consent. Remove that legacy entry until the user opts in here.
    # Never recreate an entry deleted externally, even when consent is saved.
    if not _auto_start_opted_in():
        _remove_startup_registry()


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


def _start_installed_app(timeout_seconds: float = 20.0) -> None:
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
    _ensure_options_stopped()
    source = Path(__file__).resolve()
    destination = _installed_script_path()

    _request_running_app_exit()
    _wait_until_stopped()
    if not _wait_for_instance_release():
        raise RuntimeError(
            "The previous background instance did not stop. Sign out of Windows "
            "once, then run this file again."
        )

    _apply_startup_default()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination:
        shutil.copy2(source, destination)

    _start_installed_app()
    user32.MessageBoxW(
        None,
        "MX Master 3S Hotkeys is installed and active.\n\n"
        "The notification icon will remain available near the Windows clock.\n"
        "Login startup is not enabled by installation. Use Auto Start to opt in.",
        APP_NAME,
        MB_OK | MB_ICONINFORMATION,
    )
    print(f"{APP_NAME} installed and started.")
    print("Installation does not enable Auto Start. Use the tray checkbox to opt in.")
    return 0


def uninstall_startup() -> int:
    _request_running_app_exit()
    _wait_until_stopped()
    _remove_startup_registry()
    _save_auto_start_consent(False)
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
    native = NativeThumbRecovery()
    tray = TrayApplication(remapper, native)

    def request_exit(_signum: int, _frame: object) -> None:
        tray.request_exit()

    signal.signal(signal.SIGINT, request_exit)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_exit)

    try:
        _apply_startup_default()
        _ensure_options_stopped()
        remapper.start()
        return tray.run()
    except (OSError, RuntimeError) as error:
        remapper.stop()
        user32.MessageBoxW(
            None,
            f"Unable to start {APP_NAME}:\n\n{error}",
            APP_NAME,
            0x00000010,
        )
        return 1
    finally:
        native.stop()
        native.join()
        remapper.stop()
        kernel32.CloseHandle(mutex)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--install",
        action="store_true",
        help="install for this user and start silently; Auto Start is off by default",
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
    action.add_argument("--portable", action="store_true", help="run without installing or enabling login startup")
    action.add_argument("--restore-thumb-wheel", type=Path, metavar="BACKUP.json", help="restore saved device settings")
    args = parser.parse_args()

    if args.portable:
        return run_tray_app()
    if args.restore_thumb_wheel:
        try:
            _run_thumb_worker("--restore", str(args.restore_thumb_wheel.resolve()))
            return 0
        except (OSError, RuntimeError) as error:
            user32.MessageBoxW(None, str(error), APP_NAME, MB_OK | MB_ICONERROR)
            return 1

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
    return run_tray_app()


if __name__ == "__main__":
    raise SystemExit(main())
