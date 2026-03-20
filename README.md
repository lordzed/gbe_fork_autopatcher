![Screenshot](screenshot.png)

# gbe_fork Auto Patcher

A Windows GUI tool that automatically patches Steam games with the [gbe_fork](https://github.com/Detanup01/gbe_fork) Steam emulator. It scans your Steam library, detects games, fetches info from the Steam Store API, generates all required configs, and replaces `steam_api64.dll` / `steam_api.dll` in one click.

---

## Features

- **Auto-detects Steam library** from the Windows registry, including extra library folders from `libraryfolders.vdf`
- **Recursive DLL scan** — finds `steam_api64.dll` / `steam_api.dll` no matter how deep in the game's folder tree
- **AppID from `.acf` manifests** — reads `appmanifest_*.acf` files from `steamapps/` for reliable AppID detection without any API calls
- **Smart name cleaning** — strips repack/scene tags from folder names before searching (e.g. `Until Dawn (SteamRip)` → `Until Dawn`)
- **Steam Store API integration** — auto-fetches official game names and full DLC lists per game
- **AppID search by name** — searches `store.steampowered.com` using the cleaned folder name to find the AppID automatically for games without a manifest
- **Auto-generates all gbe_fork configs** inside `steam_settings/` next to the DLL
- **DLC.txt generation** — writes all DLC entries in gbe_fork format on patch
- **Backup & restore** — renames the original DLL to `.bak` before patching; restores it with one click
- **Persistent config** — saves your profile, library path, and DLL path to `patcher_config.json` so you never need to reconfigure on restart
- **Download gbe_fork DLL automatically** from the latest GitHub release, or load one manually

---

## Requirements

- Windows 10 / 11
- Python 3.8 or newer
- No extra packages — uses only Python standard library (`tkinter`, `winreg`, `urllib`, etc.)

---

## Running from source

```bash
python patcher.py
```

That's it. No `pip install` needed.

---

## Building a standalone `.exe`

Run `build.bat` on Windows. It installs PyInstaller and produces `dist\gbe_fork_Patcher.exe` — no Python required to run the `.exe`.

```
build.bat
```

---

## First-time setup

1. Launch the app. It auto-detects your Steam library and scans for games.
2. Click **⬇ download latest** to fetch the gbe_fork DLL from GitHub automatically.  
   Or click **📂 load from file** to point it at a DLL you already have.
3. Set your **Username** and **Steam ID 64** in the left panel (a random ID is pre-filled).
4. Click **🔍 search AppIDs by name** to auto-fill any missing AppIDs, then **⬇ fetch ALL from Steam** to pull game names and DLC lists.
5. Click **⚡ patch ALL** to patch every game at once, or **⚡ patch** on individual games.

Your settings are saved automatically to `patcher_config.json` and restored on next launch.

---

## How patching works

For each game the patcher:

1. Locates the exact folder containing `steam_api64.dll` or `steam_api.dll` (walks the full directory tree)
2. Renames the original DLL to `steam_api64.dll.bak` (backup)
3. Writes the gbe_fork DLL in its place
4. Creates a `steam_settings/` folder next to the DLL containing:

| File | Purpose |
|---|---|
| `configs.main.ini` | Networking, overlay, DLC unlock |
| `configs.user.ini` | Username and Steam ID |
| `configs.app.ini` | App-specific settings |
| `configs.overlay.ini` | Overlay position and options |
| `DLC.txt` | All DLC AppIDs with names (gbe_fork format) |

5. Writes `steam_appid.txt` next to the DLL and at the game root

---

## Generated config contents

**`configs.main.ini`**
```ini
[main::connectivity]
disable_networking=0
disable_overlay=0
disable_lan_only=0

[main::general]
unlock_all_dlc=1
enable_experimental_overlay=1
```

**`configs.user.ini`**
```ini
[user::general]
account_name=YourName
account_steamid=76561198012345678
language=english
```

**`DLC.txt`** (example)
```
1234561=Season Pass
1234562=Expansion Pack 1
1234563=Soundtrack
```

---

## Folder name cleaning

Games downloaded from scene/repack sites often have noise in the folder name. The patcher strips this before searching Steam:

| Folder name | Searched as |
|---|---|
| `Until Dawn (v1.0.3) [SteamRip]` | `Until Dawn` |
| `Cyberpunk.2077_v2.1_Goldberg` | `Cyberpunk 2077` |
| `HollowKnight_v1.5.68.11182` | `HollowKnight` |
| `TheForest (FitGirl Repack)` | `TheForest` |
| `ELDEN.RING.build12345` | `ELDEN RING` |

Stripped tags include: `SteamRip`, `FitGirl`, `CODEX`, `SKIDROW`, `CPY`, `PLAZA`, `Goldberg`, `GOG`, `DODI`, version numbers like `v1.2.3`, build IDs, `Early Access`, `Online Fix`, and more.

---

## Persistent config

Settings are saved to `patcher_config.json` next to the script/exe:

```json
{
  "library_path": "C:\\Program Files (x86)\\Steam\\steamapps\\common",
  "username": "Player",
  "steamid": "76561198012345678",
  "dll_path": "C:\\path\\to\\gbe_steam_api64.dll"
}
```

You can edit this file manually. It is saved automatically when you close the app, browse for a library folder, or load/download a DLL.

---

## Restoring original DLLs

Click **↩ restore** on any patched game to swap the `.bak` file back and remove the gbe_fork DLL. Click **↩ restore ALL** to restore every patched game at once.

---

## File structure

```
gbe_patcher/
├── patcher.py              # Main application
├── build.bat               # Build script (produces .exe via PyInstaller)
├── patcher_config.json     # Auto-generated on first save
├── gbe_steam_api64.dll     # Cached gbe_fork DLL (auto-downloaded)
└── README.md
```

After patching a game the structure inside its folder looks like:

```
GameFolder/
├── steam_api64.dll         # gbe_fork DLL (replaced)
├── steam_api64.dll.bak     # original Steam DLL (backup)
├── steam_appid.txt         # AppID written by patcher
└── steam_settings/
    ├── configs.main.ini
    ├── configs.user.ini
    ├── configs.app.ini
    ├── configs.overlay.ini
    └── DLC.txt
```

---

## Credits

- [gbe_fork](https://github.com/Detanup01/gbe_fork) by **Detanup01** — the Steam emulator this tool patches with
- [Goldberg Steam Emulator](https://gitlab.com/Mr_Goldberg/goldberg_emulator) by **Mr. Goldberg** — the original project gbe_fork is based on

---

## Disclaimer

This tool is intended for **LAN play, offline use, and development/testing** purposes only. Do not use it to bypass copy protection on games you do not own. The authors take no responsibility for misuse.
