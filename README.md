# MX Master 3S Hotkeys

A lightweight Windows notification-area utility that remaps standard mouse
buttons and reverses horizontal thumb-wheel scrolling without installing
Logi Options+.

## Button mappings

| Mouse button | Action |
| --- | --- |
| Wheel / middle click | `Ctrl+W` |
| Back button | `Escape` |
| Forward button | `Right Arrow` |
| Thumb wheel | Reverse horizontal scrolling |

## Install

Python 3 is required. Download `mx_master_3s_hotkeys.py`, then double-click it
or run:

```powershell
py .\mx_master_3s_hotkeys.py
```

The first run automatically:

- installs a private copy under `%LOCALAPPDATA%\MXMaster3SHotkeys`;
- starts the utility through `pythonw.exe`, without a console or taskbar item;
- enables automatic startup for the current Windows user;
- displays an `Mx` icon in the Windows notification area.

Click or right-click the `Mx` icon to access:

- **Always Run** — verifies that login startup remains enabled.
- **Uninstall...** — asks for confirmation, removes startup and the installed
  copy, and stops the utility.

## Notes

- Uses only the Python standard library and native Win32 APIs.
- No Logitech software, account, network access, or administrator permission is
  required.
- The low-level Windows mouse hook applies these mappings to the corresponding
  buttons on every connected mouse.
- Horizontal scrolling is reversed globally for every connected mouse that
  reports `WM_MOUSEHWHEEL`; vertical wheel direction is unchanged.
- To control an application running as Administrator, this utility must run at
  the same privilege level.

## Manual maintenance

The command-line options remain available for recovery or scripted use:

```powershell
py .\mx_master_3s_hotkeys.py --install
py .\mx_master_3s_hotkeys.py --uninstall
```
