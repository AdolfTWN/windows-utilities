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
- leaves **Auto Start disabled by default**;
- displays an `Mx` icon in the Windows notification area.

Click or right-click the `Mx` icon to access:

- **Auto Start** — check to register login startup; uncheck to remove it.
- **Check for Updates** — downloads and verifies a newer published version.
- **Uninstall...** — asks for confirmation, removes startup and the installed
  copy, and stops the utility.

Version **1.1.6** removes legacy automatic startup registration unless you have
explicitly enabled Auto Start in this version or later. Installation, normal
launch, and updates do not register startup. An entry removed externally is
not silently recreated.

## Notes

- Uses only the Python standard library and native Win32 APIs.
- No Logitech software, account, network access, or administrator permission is
  required.
- The low-level Windows mouse hook applies these mappings to the corresponding
  buttons on every connected mouse.
- Thumb-wheel inversion uses the MX Master 3S native HID++ setting, without
  suppressing or re-injecting horizontal wheel events. Vertical scrolling is unchanged.
- The current device configuration targets the identified Bolt receiver slot 5.
  Other pairing slots and Bluetooth connections are not yet supported by this build.
- Fully exit Options+ and its agent before starting this utility.
- Receiver unavailability is retried in the background; resume and device changes
  request a fresh configuration check. Native settings remain after the utility exits.
- Releases 1.1.4–1.1.6 have not undergone post-change runtime testing; the earlier
  short device tests do not establish long-duration application compatibility.
- To control an application running as Administrator, this utility must run at
  the same privilege level.

## Manual maintenance

The command-line options remain available for recovery or scripted use:

```powershell
py .\mx_master_3s_hotkeys.py --install
py .\mx_master_3s_hotkeys.py --uninstall
py .\mx_master_3s_hotkeys.py --portable
py .\mx_master_3s_hotkeys.py --restore-thumb-wheel "path\to\backup.json"
```
