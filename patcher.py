import tkinter as tk
from tkinter import ttk, filedialog
import os, shutil, threading, random, re, winreg, urllib.request, urllib.parse, zipfile, io, json, time
try:
    from PIL import Image, ImageTk
    _PIL = True
except ImportError:
    _PIL = False

APP_TITLE  = "gbe_fork Patcher"
GBE_URL    = "https://github.com/Detanup01/gbe_fork/releases/latest/download/emu-win-release.zip"
STEAM_API    = "https://store.steampowered.com/api/appdetails?appids={appid}&l=english"

CONFIG_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patcher_config.json")
DEFAULT_STEAM_KEY = "YourSteamAPIKEYHERE"  # fallback key, overridden by config

DARK = {
    "bg":       "#0d0d0f",
    "surface":  "#111115",
    "border":   "#1e1e28",
    "text":     "#c8c8d0",
    "text2":    "#555568",
    "accent":   "#5a80c0",
    "accent2":  "#3a5a90",
    "green":    "#5ab87a",
    "green_bg": "#0e1a13",
    "yellow":   "#c4982a",
    "red":      "#c05050",
    "entry_bg": "#13131a",
}

CONFIGS_MAIN = """\
[main::connectivity]
disable_networking=0
disable_overlay=0
disable_lan_only=0

[main::general]
unlock_all_dlc=1
enable_experimental_overlay=1
"""

CONFIGS_OVERLAY = """\
[overlay::general]
enable_experimental_overlay=1
Notification_Position=top_right
"""

CONFIGS_APP = """\
[app::general]
; AppID written automatically via steam_appid.txt
"""

CONFIGS_USER_TPL = """\
[user::general]
account_name={name}
account_steamid={steamid}
language=english
"""

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def random_steamid():
    return str(76561190000000000 + random.randint(10_000_000, 99_999_999))

def parse_acf(path):
    """Parse a Steam .acf file, return dict of key→value from the top-level AppState block."""
    result = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r'^\s*"(\w+)"\s+"([^"]*)"', line)
                if m:
                    result[m.group(1).lower()] = m.group(2)
    except Exception:
        pass
    return result

def build_acf_map(common_path):
    """
    Scan steamapps/ (parent of common/) for appmanifest_*.acf files.
    Returns dict: installdir_lower → {"appid": str, "name": str}
    """
    steamapps = os.path.dirname(common_path)
    acf_map = {}
    try:
        for fname in os.listdir(steamapps):
            if not fname.startswith("appmanifest_") or not fname.endswith(".acf"):
                continue
            data = parse_acf(os.path.join(steamapps, fname))
            installdir = data.get("installdir", "").strip()
            appid      = data.get("appid", "").strip()
            name       = data.get("name", "").strip()
            if installdir and appid:
                acf_map[installdir.lower()] = {"appid": appid, "name": name}
    except Exception:
        pass
    return acf_map

def find_steam_paths():
    paths = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\WOW6432Node\Valve\Steam")
        steam = winreg.QueryValueEx(key, "InstallPath")[0]
        c = os.path.join(steam, "steamapps", "common")
        if os.path.isdir(c):
            paths.append(c)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            with open(vdf, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"path"' in line.lower():
                        parts = line.split('"')
                        if len(parts) >= 4:
                            p = os.path.join(parts[3], "steamapps", "common")
                            if os.path.isdir(p) and p not in paths:
                                paths.append(p)
    except Exception:
        pass
    return paths

def find_dll_in_tree(game_folder):
    SKIP = {"__pycache__","redist","redistributables","directx","vcredist","dotnet","physx","_commonredist"}
    for root, dirs, files in os.walk(game_folder):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP]
        low = {f.lower() for f in files}
        has64    = "steam_api64.dll"     in low
        has32    = "steam_api.dll"       in low
        bak64    = "steam_api64.dll.bak" in low
        bak32    = "steam_api.dll.bak"   in low
        if has64 or has32 or bak64 or bak32:
            return {"dll_dir": root, "has64": has64, "has32": has32,
                    "has_bak64": bak64, "has_bak32": bak32}
    return None

def fetch_steam_info(appid):
    """
    Query Steam Store API for a game's name and DLC list.
    Returns {"game_name": str, "dlcs": [(dlc_appid, dlc_name), ...]} or None on failure.
    """
    url = STEAM_API.format(appid=appid)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
    data = json.loads(raw)
    entry = data.get(str(appid), {})
    if not entry.get("success"):
        return None
    info = entry["data"]
    game_name = info.get("name", "")
    dlc_appids = info.get("dlc", [])
    dlcs = []
    for dlc_id in dlc_appids:
        dlcs.append((str(dlc_id), f"DLC_{dlc_id}"))
    return {"game_name": game_name, "dlcs": dlcs}

def fetch_dlc_names(dlc_ids):
    """
    Fetch DLC names from Steam Store API in batches.
    Returns dict: dlc_id_str → name_str
    """
    names = {}
    for dlc_id in dlc_ids:
        try:
            url = STEAM_API.format(appid=dlc_id)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
            data = json.loads(raw)
            entry = data.get(str(dlc_id), {})
            if entry.get("success") and entry.get("data"):
                names[str(dlc_id)] = entry["data"].get("name", f"DLC_{dlc_id}")
            else:
                names[str(dlc_id)] = f"DLC_{dlc_id}"
            time.sleep(0.3)   # polite rate limit
        except Exception:
            names[str(dlc_id)] = f"DLC_{dlc_id}"
    return names


# Scene groups / repack tags to strip from folder names
_STRIP_TAGS = re.compile(
    r'[\(\[]\s*('
    r'steamrip|fitgirl|repack|codex|skidrow|cpy|plaza|ali213|tenoke|'
    r'goldberg|elamigos|gog|darksiders|razor1911|darkzer0|dodi|'
    r'v\.?\d[\d\.]*[a-z0-9\.\-]*|'   # version numbers like v1.2, v2.0.1b
    r'build\.?\d+|'
    r'update\.?\d+|'
    r'b\d{4,}|'                             # build IDs like b12345
    r'\d{4,}|'                              # bare long numbers
    r'early.access|'
    r'online.fix|'
    r'multi\d*|'
    r'dlc[^)\]]*'
    r')[^)\]]*[\)\]]',
    re.IGNORECASE
)
_STRIP_EXTRAS = re.compile(
    r'\s*[-_]\s*(v|ver|build|update)\s*[\d\.]+.*$',
    re.IGNORECASE
)

def clean_game_name(folder_name):
    """
    Strip repack/scene tags, version numbers, and noise from a folder name
    to produce a clean title suitable for a Steam search.
    Examples:
      "Until Dawn (v1.0.3) [SteamRip]"  →  "Until Dawn"
      "Cyberpunk 2077 - v2.1 Goldberg"   →  "Cyberpunk 2077"
      "HollowKnight_v1.5.68.11182"       →  "HollowKnight"
    """
    name = folder_name
    # Remove bracketed/parenthesised tags
    name = _STRIP_TAGS.sub("", name)
    # Remove trailing version strings after dash/underscore
    name = _STRIP_EXTRAS.sub("", name)
    # Replace underscores/dots used as spaces
    name = re.sub(r"[_]", " ", name)
    # Collapse multiple spaces
    name = re.sub(r"  +", " ", name).strip(" -.")
    return name

def search_steam_appid(query):
    """
    Use the Steam storefront search API to find the best matching AppID for a
    game name. Returns (appid_str, official_name) or (None, None) on failure.
    """
    safe = urllib.parse.quote(query)
    url  = f"https://store.steampowered.com/api/storesearch/?term={safe}&l=english&cc=US"
    req  = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read())
    items = data.get("items", [])
    if not items:
        return None, None
    # Pick the item whose name most closely matches our query
    query_low = query.lower()
    best = None
    best_score = -1
    for item in items[:5]:           # only consider top 5 results
        item_name = item.get("name","").lower()
        # simple overlap score: how many query words appear in the item name
        words = [w for w in re.split(r"\W+", query_low) if len(w) > 2]
        score = sum(1 for w in words if w in item_name)
        if score > best_score:
            best_score = score
            best = item
    if best and best_score > 0:
        return str(best["id"]), best.get("name","")
    # fallback: just take the first result
    first = items[0]
    return str(first["id"]), first.get("name","")

def scan_games(library_path):
    acf_map = build_acf_map(library_path)
    games = []
    try:
        for folder_name in os.listdir(library_path):
            game_folder = os.path.join(library_path, folder_name)
            if not os.path.isdir(game_folder):
                continue
            result = find_dll_in_tree(game_folder)
            if result is None:
                continue
            dll_dir = result["dll_dir"]

            # --- AppID: ACF manifest first (most reliable), then steam_appid.txt fallback ---
            acf_entry = acf_map.get(folder_name.lower(), {})
            appid = acf_entry.get("appid", "")
            acf_name = acf_entry.get("name", "")
            if not appid:
                for check in [game_folder, dll_dir]:
                    fp = os.path.join(check, "steam_appid.txt")
                    if os.path.isfile(fp):
                        with open(fp, errors="ignore") as f:
                            appid = f.read().strip()
                        break

            # Clean folder name for search (strip repack tags etc.)
            clean_name = clean_game_name(folder_name)

            patched = result["has_bak64"] or result["has_bak32"]
            games.append({
                "name":         folder_name,
                "clean_name":   clean_name,     # sanitised for searching
                "acf_name":     acf_name,       # official name from manifest
                "path":         game_folder,
                "dll_dir":      dll_dir,
                "has64":        result["has64"],
                "has32":        result["has32"],
                "has_bak64":    result["has_bak64"],
                "has_bak32":    result["has_bak32"],
                "appid":        appid,
                "patched":      patched,
                "dlcs":         [],
                "steam_fetched": False,
            })
    except Exception:
        pass
    return games



def gse_saves_path():
    """Return the base GSE Saves directory on Windows."""
    return os.path.join(os.environ.get("APPDATA", ""), "GSE Saves")

def ach_save_file(appid):
    """Path to the per-game unlocked achievements JSON written by gbe_fork."""
    return os.path.join(gse_saves_path(), str(appid), "stats", "achievements.json")

_RUNTIME_KEY = {"value": DEFAULT_STEAM_KEY}  # updated at runtime from config/UI

def steam_api_key():
    """Return the active Steam API key (config overrides hardcoded default)."""
    return _RUNTIME_KEY["value"] or DEFAULT_STEAM_KEY

def _ach_http_get(url, timeout=15):
    """Simple HTTP GET helper, returns bytes or raises."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def _ach_make_entry(name, display="", desc="", hidden="0", icon="", icongray=""):
    """Build a normalised gbe_fork achievement dict."""
    return {
        "name":        name,
        "displayName": display or name,
        "description": desc,
        "hidden":      str(hidden),
        "icon":        icon,
        "icongray":    icongray or icon,
    }

def _ach_source_schema_api(appid):
    """
    Source A — GetSchemaForGame?key=0&appid=X&l=english
    -------------------------------------------------------
    Fully public endpoint, no key required for most games.
    Response path: game.availableGameStats.achievements[]
    Fields we extract (all are top-level on each achievement object):
      name        → API identifier      e.g. "ACH_WIN_DUEL"
      displayName → Human title         e.g. "Duelist"
      description → Unlock description  e.g. "Win 10 duels"
      hidden      → 0 or 1 int
      icon        → absolute HTTPS URL  (earned/color icon)
      icongray    → absolute HTTPS URL  (unearned/gray icon)
    Icons come back as full URLs already — no CDN prefix needed.
    """
    url = (f"https://api.steampowered.com/ISteamUserStats"
           f"/GetSchemaForGame/v2/?key={steam_api_key()}&appid={appid}&l=english&format=json")
    try:
        raw = _ach_http_get(url, timeout=15)
        data = json.loads(raw)
        achs = (data.get("game", {})
                    .get("availableGameStats", {})
                    .get("achievements", []))
        if not achs:
            return []
        return [_ach_make_entry(
            name     = a.get("name",        ""),
            display  = a.get("displayName", ""),
            desc     = a.get("description", ""),
            hidden   = str(a.get("hidden",  0)),
            icon     = a.get("icon",        ""),
            icongray = a.get("icongray",    ""),
        ) for a in achs if a.get("name")]
    except Exception:
        return []

def _ach_source_hover_content(appid):
    """
    Source B — store.steampowered.com/apphovercontent/<appid>/achievements
    -----------------------------------------------------------------------
    Returns a small HTML fragment used by the store hover popup.
    Structure per achievement:
      <div class="achievement_list_achievement">
        <img src="ICON_URL" />
        <div class="achievement_list_achievement_info">
          <div class="ellipsis achievement_name">DISPLAY NAME</div>
          <div class="achievement_description">DESCRIPTION</div>
        </div>
      </div>
    No API names in this source — use displayName as name fallback.
    Good coverage and always returns English text.
    """
    url = f"https://store.steampowered.com/apphovercontent/{appid}/achievements"
    try:
        html = _ach_http_get(url, timeout=15).decode("utf-8", errors="replace")
    except Exception:
        return []

    result = []
    # Match each achievement block
    for block in re.finditer(
            r'<div[^>]+class="[^"]*achievement_list_achievement[^"]*"[^>]*>(.*?)</div\s*>\s*</div\s*>',
            html, re.DOTALL | re.IGNORECASE):
        chunk = block.group(1)
        icon_m  = re.search(r'<img[^>]+src="([^"]+)"', chunk, re.IGNORECASE)
        name_m  = re.search(r'class="[^"]*achievement_name[^"]*"[^>]*>\s*(.*?)\s*<', chunk, re.DOTALL | re.IGNORECASE)
        desc_m  = re.search(r'class="[^"]*achievement_description[^"]*"[^>]*>\s*(.*?)\s*<', chunk, re.DOTALL | re.IGNORECASE)
        disp = re.sub(r'<[^>]+>', '', name_m.group(1)).strip() if name_m else ""
        desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""
        icon = icon_m.group(1).strip() if icon_m else ""
        if disp:
            result.append(_ach_make_entry(name=disp, display=disp, desc=desc, icon=icon))
    return result

def _ach_source_global_names(appid):
    """
    Source C — GetGlobalAchievementPercentagesForApp (last resort)
    --------------------------------------------------------------
    Returns API names only — no displayName or description.
    Used only when both A and B fail, so the list is at least populated.
    """
    url = (f"https://api.steampowered.com/ISteamUserStats"
           f"/GetGlobalAchievementPercentagesForApp/v2/?key={steam_api_key()}&gameid={appid}&format=json")
    try:
        data = json.loads(_ach_http_get(url, timeout=12))
        entries = data.get("achievementpercentages", {}).get("achievements", [])
        return [e["name"] for e in entries if e.get("name")]
    except Exception:
        return []

def fetch_achievements_schema(appid):
    """
    Fetch full achievement definitions (displayName + description + icons).
    No API key required.

    Pipeline (fastest-wins parallel fetch):
      A. GetSchemaForGame?key=0   — primary, full data when available
      B. apphovercontent HTML     — fallback with display names + descriptions
      C. GlobalAchievementPercentages — last resort, API names only

    A and B run in parallel. Results are merged so each field is filled
    from whichever source has it. C runs only if both A and B return nothing.
    """
    out = {}
    def run_a(o): o["a"] = _ach_source_schema_api(appid)
    def run_b(o): o["b"] = _ach_source_hover_content(appid)
    ta = threading.Thread(target=run_a, args=(out,), daemon=True)
    tb = threading.Thread(target=run_b, args=(out,), daemon=True)
    ta.start(); tb.start()
    ta.join(timeout=20); tb.join(timeout=20)

    schema_a = out.get("a", [])   # full data: name + displayName + desc + icons
    schema_b = out.get("b", [])   # displayName + desc + icon (no API name)

    # If A worked it's the best source — use it as base, then fill any missing
    # displayName/description/icon gaps from B (matched by position, since
    # hover content has no API names).
    if schema_a:
        if schema_b:
            # B is ordered the same as A — zip to fill gaps
            for i, entry_a in enumerate(schema_a):
                if i >= len(schema_b):
                    break
                entry_b = schema_b[i]
                if not entry_a.get("displayName") or entry_a["displayName"] == entry_a["name"]:
                    entry_a["displayName"] = entry_b.get("displayName") or entry_a["displayName"]
                if not entry_a.get("description"):
                    entry_a["description"] = entry_b.get("description", "")
                if not entry_a.get("icon"):
                    entry_a["icon"] = entry_b.get("icon", "")
                if not entry_a.get("icongray"):
                    entry_a["icongray"] = entry_b.get("icongray", "")
        return schema_a

    # A failed — use B (displayName as name, since hover has no API names)
    if schema_b:
        return schema_b

    # Both failed — last resort: API names only from global percentages
    names = _ach_source_global_names(appid)
    if names:
        return [_ach_make_entry(name=n) for n in names]

    return []

def read_unlocked_achievements(appid):
    """
    Read %APPDATA%/GSE Saves/<AppID>/stats/achievements.json.
    Returns dict: api_name -> {"earned": bool, "earned_time": int}
    """
    path = ach_save_file(appid)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_unlocked_achievements(appid, state):
    """
    Write the unlocked achievements dict back to disk.
    state: dict api_name -> {"earned": bool, "earned_time": int}
    """
    path = ach_save_file(appid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── ICON HELPERS ──────────────────────────────────────────────────────────────

_ICON_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ach_icons")
_ICON_MEM_CACHE = {}

def _icon_url(appid, raw_icon):
    if not raw_icon:
        return None
    if raw_icon.startswith("http"):
        return raw_icon.replace("http://", "https://")
    return f"https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{raw_icon}"

def _icon_cache_path(url):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", url.split("/")[-1])
    return os.path.join(_ICON_CACHE_DIR, safe)

def load_icon_sync(appid, raw_icon, size=36, local_base=None):
    """
    Load an achievement icon as a PhotoImage.
    raw_icon may be:
      - a local relative path like "images/ACH_WIN.jpg"  (preferred)
      - a full https:// URL  (downloaded + cached to disk)
    local_base: absolute path to the steam_settings/ folder so relative
                paths can be resolved. If None, falls back to URL mode.
    """
    if not _PIL or not raw_icon:
        return None

    # ── Local file (relative path stored in achievements.json) ────────────────
    if local_base and not raw_icon.startswith("http"):
        abs_path = os.path.join(local_base, raw_icon)
        if os.path.isfile(abs_path):
            cache_key = abs_path
            if cache_key in _ICON_MEM_CACHE:
                return _ICON_MEM_CACHE[cache_key]
            try:
                img   = Image.open(abs_path).convert("RGBA").resize((size, size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                _ICON_MEM_CACHE[cache_key] = photo
                return photo
            except Exception:
                return None

    # ── Remote URL ─────────────────────────────────────────────────────────────
    url = _icon_url(appid, raw_icon)
    if not url:
        return None
    if url in _ICON_MEM_CACHE:
        return _ICON_MEM_CACHE[url]
    os.makedirs(_ICON_CACHE_DIR, exist_ok=True)
    disk = _icon_cache_path(url)
    try:
        if not os.path.isfile(disk):
            data = _ach_http_get(url, timeout=10)
            with open(disk, "wb") as f:
                f.write(data)
        img   = Image.open(disk).convert("RGBA").resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        _ICON_MEM_CACHE[url] = photo
        return photo
    except Exception:
        return None

def make_placeholder_icon(size=36, unlocked=True):
    if not _PIL:
        return None
    color = (90, 184, 122, 255) if unlocked else (85, 85, 104, 255)
    img = Image.new("RGBA", (size, size), color)
    return ImageTk.PhotoImage(img)


def download_achievement_images(appid, schema, images_dir, log_cb=None):
    """
    Download icon + icongray for every achievement in schema.
    Saves to: images_dir/{api_name}.jpg  and  images_dir/{api_name}_gray.jpg
    Replaces the "icon" and "icongray" fields in each entry with the local
    relative path  (e.g.  "images/ACH_WIN.jpg").
    Returns the mutated schema list.
    """
    os.makedirs(images_dir, exist_ok=True)
    total   = len(schema)
    done    = 0
    errors  = 0

    for entry in schema:
        api_name = entry.get("name","").strip()
        if not api_name:
            continue

        for field, suffix in [("icon", ""), ("icongray", "_gray")]:
            url = entry.get(field, "")
            if not url or not url.startswith("http"):
                continue
            # Determine file extension from URL (jpg or png)
            ext = ".png" if url.lower().endswith(".png") else ".jpg"
            filename  = f"{api_name}{suffix}{ext}"
            local_abs = os.path.join(images_dir, filename)
            # Relative path stored in JSON (relative to steam_settings/)
            local_rel = os.path.join("images", filename)

            # Skip if already downloaded
            if not os.path.isfile(local_abs):
                try:
                    data = _ach_http_get(url, timeout=12)
                    with open(local_abs, "wb") as f:
                        f.write(data)
                except Exception as ex:
                    errors += 1
                    if log_cb:
                        log_cb(f"  [img] failed {filename}: {ex}")
                    continue

            # Replace URL with local relative path
            entry[field] = local_rel

        done += 1
        if log_cb and done % 10 == 0:
            log_cb(f"  [img] {done}/{total} icons downloaded...")

    if log_cb:
        log_cb(f"  [img] done — {done} processed, {errors} errors")
    return schema


def load_config():
    """Load saved settings from JSON, return dict (empty if missing/corrupt)."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(data):
    """Persist settings dict to JSON next to the script."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ─── APP ──────────────────────────────────────────────────────────────────────

class PatcherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x780")
        self.minsize(900, 620)
        self.configure(bg=DARK["bg"])
        self.resizable(True, True)

        self.games      = []
        self._cfg       = load_config()
        self.lib_path   = tk.StringVar(value=self._cfg.get("library_path", ""))
        self.username   = tk.StringVar(value=self._cfg.get("username", "Player"))
        self.steamid    = tk.StringVar(value=self._cfg.get("steamid", random_steamid()))
        _k = self._cfg.get("steam_api_key", DEFAULT_STEAM_KEY)
        self.api_key    = tk.StringVar(value=_k)
        _RUNTIME_KEY["value"] = _k  # apply immediately
        self.gbe64      = None
        self.gbe32      = None
        self.dl_status  = tk.StringVar(value="")
        self._saved_dll = self._cfg.get("dll_path", "")   # path to cached DLL on disk

        self._ach_game_idx  = None   # currently selected game in ach tab
        self._icon_refs     = []     # keep PhotoImage refs alive (prevent GC)
        self._ach_schema    = []     # definitions list
        self._ach_state     = {}     # unlocked state dict
        self._ach_filter    = tk.StringVar(value="all")  # all/unlocked/locked

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._auto_detect()
        self._load_saved_dll()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._mk_style()
        hdr = tk.Frame(self, bg=DARK["bg"])
        hdr.pack(fill="x", padx=20, pady=(16,0))
        tk.Label(hdr, text="gbe_fork", font=("Consolas",18,"bold"),
                 fg=DARK["accent"], bg=DARK["bg"]).pack(side="left")
        tk.Label(hdr, text=" // auto patcher", font=("Consolas",13),
                 fg=DARK["text2"], bg=DARK["bg"]).pack(side="left", pady=3)
        tk.Frame(self, bg=DARK["border"], height=1).pack(fill="x", padx=20, pady=(10,0))

        body = tk.Frame(self, bg=DARK["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=10)

        left = tk.Frame(body, bg=DARK["bg"], width=265)
        left.pack(side="left", fill="y", padx=(0,14))
        left.pack_propagate(False)
        self._build_left(left)

        right = tk.Frame(body, bg=DARK["bg"])
        right.pack(side="left", fill="both", expand=True)
        self._build_right(right)

        lf = tk.Frame(self, bg=DARK["bg"])
        lf.pack(fill="x", padx=20, pady=(0,10))
        tk.Label(lf, text="LOG", font=("Consolas",8), fg=DARK["text2"],
                 bg=DARK["bg"]).pack(anchor="w")
        self.log_box = tk.Text(lf, height=5, bg=DARK["entry_bg"], fg=DARK["text2"],
                               font=("Consolas",9), relief="flat", bd=0,
                               state="disabled", highlightthickness=1,
                               highlightbackground=DARK["border"],
                               insertbackground=DARK["text"])
        self.log_box.pack(fill="x")
        for tag, col in [("ok", DARK["green"]), ("err", DARK["red"]),
                         ("warn", DARK["yellow"]), ("act", DARK["accent"])]:
            self.log_box.tag_config(tag, foreground=col)

    def _build_left(self, p):
        self._lbl(p, "PROFILE")
        self._field(p, "Username", self.username)
        self._field(p, "Steam ID 64", self.steamid)
        tk.Button(p, text="⟳ random ID",
                  command=lambda: self.steamid.set(random_steamid()),
                  **self._bs("ghost")).pack(fill="x", pady=(2,10))

        tk.Frame(p, bg=DARK["border"], height=1).pack(fill="x", pady=6)
        self._lbl(p, "GBE_FORK DLL")
        self.dll_lbl = tk.Label(p, text="⬤ not loaded", font=("Consolas",9),
                                fg=DARK["red"], bg=DARK["bg"], anchor="w")
        self.dll_lbl.pack(fill="x", pady=(0,4))
        tk.Button(p, text="⬇ download latest",
                  command=self._download_gbe, **self._bs("primary")).pack(fill="x", pady=2)
        tk.Button(p, text="📂 load from file",
                  command=self._load_dll_file, **self._bs("ghost")).pack(fill="x", pady=2)
        tk.Label(p, textvariable=self.dl_status, font=("Consolas",8),
                 fg=DARK["yellow"], bg=DARK["bg"], anchor="w",
                 wraplength=230, justify="left").pack(fill="x", pady=(2,0))

        tk.Frame(p, bg=DARK["border"], height=1).pack(fill="x", pady=6)
        self._lbl(p, "STEAM API KEY")
        key_entry = tk.Entry(p, textvariable=self.api_key,
                 bg=DARK["entry_bg"], fg=DARK["accent"], font=("Consolas",8),
                 relief="flat", highlightthickness=1,
                 highlightbackground=DARK["border"],
                 insertbackground=DARK["text"], show="")
        key_entry.pack(fill="x", pady=(0,4))
        self.api_key.trace_add("write", lambda *_: self._on_api_key_change())
        tk.Label(p, text="used for achievement schema fetching",
                 font=("Consolas",7), fg=DARK["text2"],
                 bg=DARK["bg"], anchor="w").pack(fill="x", pady=(0,6))

        tk.Frame(p, bg=DARK["border"], height=1).pack(fill="x", pady=6)
        self._lbl(p, "STEAM LIBRARY")
        tk.Entry(p, textvariable=self.lib_path, bg=DARK["entry_bg"],
                 fg=DARK["text"], font=("Consolas",8), relief="flat",
                 highlightthickness=1, highlightbackground=DARK["border"],
                 insertbackground=DARK["text"]).pack(fill="x", pady=(0,4))
        tk.Button(p, text="📂 browse",
                  command=self._browse_lib, **self._bs("ghost")).pack(fill="x", pady=2)
        tk.Button(p, text="↺ scan games",
                  command=self._scan, **self._bs("ghost")).pack(fill="x", pady=2)

        tk.Frame(p, bg=DARK["border"], height=1).pack(fill="x", pady=6)
        self._lbl(p, "STEAM DB AUTO-FETCH")
        tk.Label(p, text="Reads AppID from .acf manifests,\nthen fetches name + DLC list\nfrom store.steampowered.com",
                 font=("Consolas",8), fg=DARK["text2"], bg=DARK["bg"],
                 justify="left").pack(anchor="w", pady=(0,4))
        tk.Button(p, text="⬇ fetch ALL from Steam",
                  command=self._fetch_all_steam, **self._bs("primary")).pack(fill="x", pady=2)
        tk.Button(p, text="🔍 search AppIDs by name",
                  command=self._search_all_appids, **self._bs("ghost")).pack(fill="x", pady=2)

    def _build_right(self, p):
        # ── Tab bar ──
        style = ttk.Style()
        style.configure("Dark.TNotebook", background=DARK["bg"], borderwidth=0)
        style.configure("Dark.TNotebook.Tab",
                         background=DARK["surface"], foreground=DARK["text2"],
                         font=("Consolas",9), padding=(12,5))
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", DARK["accent2"])],
                  foreground=[("selected", DARK["text"])])

        self._nb = ttk.Notebook(p, style="Dark.TNotebook")
        self._nb.pack(fill="both", expand=True)

        # ── Tab 1: Games ──────────────────────────────────────────────────────
        games_tab = tk.Frame(self._nb, bg=DARK["bg"])
        self._nb.add(games_tab, text=" 🎮  games ")

        hdr = tk.Frame(games_tab, bg=DARK["bg"])
        hdr.pack(fill="x", pady=(8,6))
        self.count_lbl = tk.Label(hdr, text="0 games", font=("Consolas",10),
                                  fg=DARK["text2"], bg=DARK["bg"])
        self.count_lbl.pack(side="left")
        tk.Button(hdr, text="↩ restore ALL",
                  command=self._restore_all, **self._bs("ghost")).pack(side="right")
        tk.Button(hdr, text="⚡ patch ALL",
                  command=self._patch_all, **self._bs("primary")).pack(side="right", padx=(4,4))

        wrap = tk.Frame(games_tab, bg=DARK["surface"],
                        highlightthickness=1, highlightbackground=DARK["border"])
        wrap.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(wrap, bg=DARK["surface"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self._canvas.yview)
        self.game_frame = tk.Frame(self._canvas, bg=DARK["surface"])
        self.game_frame.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0,0), window=self.game_frame, anchor="nw")
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._canvas.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1*(e.delta//120), "units"))

        # ── Tab 2: Achievements ───────────────────────────────────────────────
        ach_tab = tk.Frame(self._nb, bg=DARK["bg"])
        self._nb.add(ach_tab, text=" 🏆  achievements ")
        self._build_ach_tab(ach_tab)

    # ── ACHIEVEMENTS TAB ─────────────────────────────────────────────────────

    def _build_ach_tab(self, p):
        # Top bar: game selector + filter + actions
        top = tk.Frame(p, bg=DARK["bg"])
        top.pack(fill="x", pady=(8,4))

        tk.Label(top, text="Game:", font=("Consolas",9),
                 fg=DARK["text2"], bg=DARK["bg"]).pack(side="left", padx=(0,4))
        self._ach_game_var = tk.StringVar(value="— select a game —")
        self._ach_game_combo = ttk.Combobox(top, textvariable=self._ach_game_var,
                                             state="readonly", font=("Consolas",9), width=34)
        self._ach_game_combo.pack(side="left", padx=(0,8))
        self._ach_game_combo.bind("<<ComboboxSelected>>", lambda e: self._ach_load_game())

        tk.Button(top, text="⬇ fetch schema",
                  command=self._ach_fetch_schema, **self._bs("primary")).pack(side="left", padx=2)
        tk.Button(top, text="↺ reload saves",
                  command=self._ach_reload_saves, **self._bs("ghost")).pack(side="left", padx=2)

        # Filter radio buttons
        filter_frame = tk.Frame(top, bg=DARK["bg"])
        filter_frame.pack(side="right")
        for val, label in [("all","all"), ("unlocked","✓ unlocked"), ("locked","○ locked")]:
            tk.Radiobutton(filter_frame, text=label, variable=self._ach_filter, value=val,
                           font=("Consolas",8), fg=DARK["text2"], bg=DARK["bg"],
                           selectcolor=DARK["accent2"], activebackground=DARK["bg"],
                           activeforeground=DARK["text"], command=self._ach_render,
                           relief="flat", bd=0).pack(side="left", padx=4)

        # Search bar
        search_row = tk.Frame(p, bg=DARK["bg"])
        search_row.pack(fill="x", pady=(0,4))
        tk.Label(search_row, text="search:", font=("Consolas",8),
                 fg=DARK["text2"], bg=DARK["bg"]).pack(side="left")
        self._ach_search = tk.StringVar()
        self._ach_search.trace_add("write", lambda *_: self._ach_render())
        tk.Entry(search_row, textvariable=self._ach_search, bg=DARK["entry_bg"],
                 fg=DARK["text"], font=("Consolas",9), relief="flat",
                 highlightthickness=1, highlightbackground=DARK["border"],
                 insertbackground=DARK["text"], width=30).pack(side="left", padx=(4,16))

        # Bulk action buttons
        tk.Button(search_row, text="✓ unlock ALL",
                  command=self._ach_unlock_all, **self._bs("success")).pack(side="right", padx=2)
        tk.Button(search_row, text="○ lock ALL",
                  command=self._ach_lock_all, **self._bs("ghost")).pack(side="right", padx=2)

        # Stats bar
        self._ach_stats_lbl = tk.Label(p, text="", font=("Consolas",8),
                                        fg=DARK["text2"], bg=DARK["bg"], anchor="w")
        self._ach_stats_lbl.pack(fill="x", padx=2, pady=(0,4))

        # Achievement list (scrollable)
        wrap = tk.Frame(p, bg=DARK["surface"],
                        highlightthickness=1, highlightbackground=DARK["border"])
        wrap.pack(fill="both", expand=True)
        self._ach_canvas = tk.Canvas(wrap, bg=DARK["surface"], highlightthickness=0)
        ach_sb = ttk.Scrollbar(wrap, orient="vertical", command=self._ach_canvas.yview)
        self._ach_frame = tk.Frame(self._ach_canvas, bg=DARK["surface"])
        self._ach_frame.bind("<Configure>",
            lambda e: self._ach_canvas.configure(
                scrollregion=self._ach_canvas.bbox("all")))
        self._ach_canvas.create_window((0,0), window=self._ach_frame, anchor="nw")
        self._ach_canvas.configure(yscrollcommand=ach_sb.set)
        self._ach_canvas.pack(side="left", fill="both", expand=True)
        ach_sb.pack(side="right", fill="y")
        self._ach_canvas.bind("<MouseWheel>",
            lambda e: self._ach_canvas.yview_scroll(-1*(e.delta//120), "units"))

    def _ach_update_combo(self):
        """Refresh the game selector combobox from self.games."""
        names = [f"{g.get('acf_name') or g['name']}  [{g['appid'] or '?'}]"
                 for g in self.games]
        self._ach_game_combo["values"] = names
        if names and self._ach_game_idx is None:
            self._ach_game_combo.current(0)
            self._ach_game_idx = 0

    def _ach_load_game(self):
        idx = self._ach_game_combo.current()
        if idx < 0 or idx >= len(self.games):
            return
        self._ach_game_idx = idx
        g = self.games[idx]
        appid = g.get("appid","")
        # Load schema from steam_settings/achievements.json if present
        sd = os.path.join(g.get("dll_dir", g["path"]), "steam_settings", "achievements.json")
        if os.path.isfile(sd):
            try:
                with open(sd, encoding="utf-8") as f:
                    self._ach_schema = json.load(f)
                self.log(f"[ach] loaded schema from steam_settings ({len(self._ach_schema)} achievements)", "ok")
            except Exception:
                self._ach_schema = []
        else:
            self._ach_schema = []
        # Load save state
        if appid:
            self._ach_state = read_unlocked_achievements(appid)
        else:
            self._ach_state = {}
        self._icon_refs.clear()
        self._ach_render()
        if self._ach_schema and appid:
            sd_base = os.path.join(g.get("dll_dir", g["path"]), "steam_settings")
            self._ach_preload_icons(appid, self._ach_schema, local_base=sd_base)

    def _ach_fetch_schema(self):
        idx = self._ach_game_idx
        if idx is None or idx >= len(self.games):
            self.log("[ach] select a game first", "warn"); return
        g = self.games[idx]
        appid = g.get("appid","").strip()
        if not appid:
            self.log("[ach] game has no AppID — fetch info or enter it manually", "warn"); return
        def run():
            self.log(f"[ach] fetching schema for AppID {appid}...", "act")
            schema = fetch_achievements_schema(appid)
            if not schema:
                self.log("[ach] no achievements found (game may have no achievements or API unavailable)", "warn")
                return
            # Download icons → steam_settings/images/ and patch paths in schema
            sd_dir     = os.path.join(g.get("dll_dir", g["path"]), "steam_settings")
            images_dir = os.path.join(sd_dir, "images")
            self.log(f"[ach] downloading {len(schema)} achievement icons...", "act")
            schema = download_achievement_images(
                appid, schema, images_dir, log_cb=lambda m: self.log(m, "act"))
            self._ach_schema = schema
            # Write achievements.json with local image paths
            os.makedirs(sd_dir, exist_ok=True)
            sd_path = os.path.join(sd_dir, "achievements.json")
            with open(sd_path, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2, ensure_ascii=False)
            self.log(f"[ach] wrote {len(schema)} achievements → {sd_path}", "ok")
            # Reload save state + re-render
            self._ach_state = read_unlocked_achievements(appid)
            self._icon_refs.clear()
            self.after(0, self._ach_render)
            self._ach_preload_icons(appid, schema, local_base=sd_dir)
        threading.Thread(target=run, daemon=True).start()

    def _ach_reload_saves(self):
        idx = self._ach_game_idx
        if idx is None or idx >= len(self.games): return
        g = self.games[idx]
        appid = g.get("appid","").strip()
        if appid:
            self._ach_state = read_unlocked_achievements(appid)
        self._ach_render()

    def _ach_render(self):
        """Redraw the achievement list based on current schema, state, filter, search."""
        for w in self._ach_frame.winfo_children():
            w.destroy()

        schema = self._ach_schema
        state  = self._ach_state
        filt   = self._ach_filter.get()
        query  = self._ach_search.get().lower().strip()

        if not schema:
            tk.Label(self._ach_frame,
                     text='Select a game and click "⬇ fetch schema" to load achievements.',
                     font=("Consolas",10), fg=DARK["text2"],
                     bg=DARK["surface"]).pack(pady=40, padx=20)
            self._ach_stats_lbl.config(text="")
            return

        total    = len(schema)
        unlocked = sum(1 for a in schema if state.get(a["name"],{}).get("earned", False))

        # Filter + search
        visible = []
        for a in schema:
            earned = state.get(a["name"], {}).get("earned", False)
            if filt == "unlocked" and not earned: continue
            if filt == "locked"   and earned:     continue
            if query and query not in a.get("displayName","").lower()                      and query not in a.get("description","").lower()                      and query not in a["name"].lower():
                continue
            visible.append(a)

        self._ach_stats_lbl.config(
            text=f"  {unlocked}/{total} unlocked  |  showing {len(visible)}")

        for a in visible:
            name    = a["name"]
            earned  = state.get(name, {}).get("earned", False)
            e_time  = state.get(name, {}).get("earned_time", 0)
            hidden  = str(a.get("hidden","0")) == "1"

            bg      = DARK["green_bg"] if earned else DARK["surface"]
            border  = "#1a3a28"        if earned else DARK["border"]

            row = tk.Frame(self._ach_frame, bg=bg,
                           highlightthickness=1, highlightbackground=border)
            row.pack(fill="x", padx=6, pady=2)
            inner = tk.Frame(row, bg=bg, padx=10, pady=6)
            inner.pack(fill="x")

            # Icon
            idx2       = self._ach_game_idx
            g2         = self.games[idx2] if idx2 is not None else {}
            appid2     = g2.get("appid","")
            sd_base    = os.path.join(g2.get("dll_dir", g2.get("path","")), "steam_settings")
            icon_f     = a.get("icon","") if earned else a.get("icongray", a.get("icon",""))
            photo      = load_icon_sync(appid2, icon_f, 36, local_base=sd_base) if appid2 else None
            if photo is None:
                photo = make_placeholder_icon(36, earned)
            dot_color = DARK["green"] if earned else DARK["text2"]
            dot_char  = "✓" if earned else "○"
            icon_col = tk.Frame(inner, bg=bg)
            icon_col.pack(side="left", padx=(0,10))
            if photo:
                self._icon_refs.append(photo)
                tk.Label(icon_col, image=photo, bg=bg, bd=0).pack()
            tk.Label(icon_col, text=dot_char, font=("Consolas",9,"bold"),
                     fg=dot_color, bg=bg).pack()

            info = tk.Frame(inner, bg=bg)
            info.pack(side="left", fill="x", expand=True)

            disp_name = a.get("displayName") or name
            tk.Label(info, text=disp_name, font=("Consolas",10,"bold"),
                     fg=DARK["green"] if earned else DARK["text"],
                     bg=bg, anchor="w").pack(fill="x")

            desc = a.get("description","")
            if hidden and not desc:
                desc = "(hidden achievement)"
            if desc:
                tk.Label(info, text=desc, font=("Consolas",8),
                         fg=DARK["text2"], bg=bg, anchor="w",
                         wraplength=480, justify="left").pack(fill="x")

            if earned and e_time:
                import datetime
                dt = datetime.datetime.fromtimestamp(e_time).strftime("%Y-%m-%d %H:%M")
                tk.Label(info, text=f"unlocked: {dt}", font=("Consolas",8),
                         fg=DARK["accent"], bg=bg, anchor="w").pack(fill="x")

            # api name (small)
            tk.Label(info, text=name, font=("Consolas",7),
                     fg=DARK["text2"], bg=bg, anchor="w").pack(fill="x")

            # Unlock / Lock button
            btn_frame = tk.Frame(inner, bg=bg)
            btn_frame.pack(side="right")
            if earned:
                tk.Button(btn_frame, text="○ lock",
                          command=lambda n=name: self._ach_set(n, False),
                          **self._bs("ghost")).pack()
            else:
                tk.Button(btn_frame, text="✓ unlock",
                          command=lambda n=name: self._ach_set(n, True),
                          **self._bs("success")).pack()

    def _ach_preload_icons(self, appid, schema, local_base=None):
        def worker():
            for a in schema:
                load_icon_sync(appid, a.get("icon",""),    36, local_base=local_base)
                load_icon_sync(appid, a.get("icongray",""),36, local_base=local_base)
            self.after(0, self._ach_render)
        threading.Thread(target=worker, daemon=True).start()

    def _ach_set(self, ach_name, unlock):
        """Unlock or lock a single achievement and persist to disk."""
        idx = self._ach_game_idx
        if idx is None or idx >= len(self.games): return
        g = self.games[idx]
        appid = g.get("appid","").strip()
        if not appid:
            self.log("[ach] no AppID for this game", "warn"); return
        if unlock:
            self._ach_state[ach_name] = {
                "earned": True,
                "earned_time": int(time.time()),
            }
            self.log(f"[ach] unlocked: {ach_name}", "ok")
        else:
            self._ach_state[ach_name] = {"earned": False, "earned_time": 0}
            self.log(f"[ach] locked: {ach_name}", "warn")
        try:
            write_unlocked_achievements(appid, self._ach_state)
        except Exception as ex:
            self.log(f"[ach] could not write save: {ex}", "err")
        self._ach_render()

    def _ach_unlock_all(self):
        idx = self._ach_game_idx
        if idx is None or idx >= len(self.games): return
        g = self.games[idx]
        appid = g.get("appid","").strip()
        if not appid or not self._ach_schema:
            self.log("[ach] no schema or AppID", "warn"); return
        now = int(time.time())
        for a in self._ach_schema:
            self._ach_state[a["name"]] = {"earned": True, "earned_time": now}
        write_unlocked_achievements(appid, self._ach_state)
        self.log(f"[ach] unlocked all {len(self._ach_schema)} achievements", "ok")
        self._ach_render()

    def _ach_lock_all(self):
        idx = self._ach_game_idx
        if idx is None or idx >= len(self.games): return
        g = self.games[idx]
        appid = g.get("appid","").strip()
        if not appid or not self._ach_schema:
            self.log("[ach] no schema or AppID", "warn"); return
        for a in self._ach_schema:
            self._ach_state[a["name"]] = {"earned": False, "earned_time": 0}
        write_unlocked_achievements(appid, self._ach_state)
        self.log(f"[ach] locked all {len(self._ach_schema)} achievements", "warn")
        self._ach_render()

        # ── STYLE HELPERS ─────────────────────────────────────────────────────────

    def _mk_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Vertical.TScrollbar", background=DARK["border"],
                    troughcolor=DARK["surface"], bordercolor=DARK["border"],
                    arrowcolor=DARK["text2"])

    def _lbl(self, p, t):
        tk.Label(p, text=t, font=("Consolas",8), fg=DARK["text2"],
                 bg=DARK["bg"]).pack(anchor="w", pady=(8,2))

    def _field(self, p, label, var):
        tk.Label(p, text=label, font=("Consolas",8), fg=DARK["text2"],
                 bg=DARK["bg"]).pack(anchor="w")
        tk.Entry(p, textvariable=var, bg=DARK["entry_bg"], fg=DARK["text"],
                 font=("Consolas",10), relief="flat", highlightthickness=1,
                 highlightbackground=DARK["border"],
                 insertbackground=DARK["text"]).pack(fill="x", pady=(0,5))

    def _bs(self, kind="primary"):
        base = dict(font=("Consolas",9), relief="flat", cursor="hand2",
                    padx=8, pady=5, bd=0)
        if kind == "primary":
            return {**base, "bg": DARK["accent2"], "fg": DARK["accent"],
                    "activebackground": DARK["accent2"], "activeforeground": DARK["text"]}
        if kind == "success":
            return {**base, "bg": DARK["green_bg"], "fg": DARK["green"],
                    "activebackground": DARK["green_bg"], "activeforeground": DARK["green"]}
        return {**base, "bg": DARK["surface"], "fg": DARK["text2"],
                "activebackground": DARK["surface"], "activeforeground": DARK["text"],
                "highlightthickness": 1, "highlightbackground": DARK["border"]}

    def log(self, msg, tag=""):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ── GAME LIST RENDER ──────────────────────────────────────────────────────

    def _render_games(self):
        for w in self.game_frame.winfo_children():
            w.destroy()
        self.count_lbl.config(
            text=f"{len(self.games)} game{'s' if len(self.games)!=1 else ''} found")
        if not self.games:
            tk.Label(self.game_frame,
                     text="No games found. Select a Steam library and scan.",
                     font=("Consolas",10), fg=DARK["text2"],
                     bg=DARK["surface"]).pack(pady=40)
            return
        for i, g in enumerate(self.games):
            self._game_card(i, g)
        # Keep achievement tab game selector in sync
        if hasattr(self, "_ach_game_combo"):
            self._ach_update_combo()

    def _game_card(self, i, g):
        patched = g["patched"]
        bg = DARK["green_bg"] if patched else DARK["surface"]
        border = "#1a3a28" if patched else DARK["border"]

        card = tk.Frame(self.game_frame, bg=bg,
                        highlightthickness=1, highlightbackground=border)
        card.pack(fill="x", padx=6, pady=3)

        inner = tk.Frame(card, bg=bg, padx=10, pady=8)
        inner.pack(fill="x")

        # Icon letter
        tk.Label(inner, text=g["name"][0].upper(), font=("Consolas",13,"bold"),
                 fg=DARK["accent"], bg=DARK["border"],
                 width=2, height=1).pack(side="left", padx=(0,10))

        info = tk.Frame(inner, bg=bg)
        info.pack(side="left", fill="x", expand=True)

        # Display name: prefer ACF name, fall back to folder name
        display_name = g.get("acf_name") or g["name"]
        tk.Label(info, text=display_name, font=("Consolas",10,"bold"),
                 fg=DARK["green"] if patched else DARK["text"],
                 bg=bg, anchor="w").pack(fill="x")

        # Folder / dll location line
        dll_dir  = g.get("dll_dir", g["path"])
        rel      = os.path.relpath(dll_dir, g["path"])
        dll_info = f"dll in: {rel}" if rel != "." else "dll in: root"
        bits = []
        if g["has64"]: bits.append("64-bit")
        if g["has32"]: bits.append("32-bit")
        if g["has_bak64"] or g["has_bak32"]: bits.append(".bak")
        tk.Label(info, text=f"{dll_info}  |  {' '.join(bits)}",
                 font=("Consolas",8), fg=DARK["text2"], bg=bg, anchor="w").pack(fill="x")

        # Show cleaned search name if it differs from folder name
        clean = g.get("clean_name","")
        if clean and clean.lower() != g["name"].lower():
            tk.Label(info, text=f"search: {clean}",
                     font=("Consolas",8), fg=DARK["yellow"],
                     bg=bg, anchor="w").pack(fill="x")

        # AppID + DLC info row
        appid_var = tk.StringVar(value=g["appid"])
        g["_appid_var"] = appid_var
        apid_row = tk.Frame(info, bg=bg)
        apid_row.pack(fill="x", pady=(2,0))
        tk.Label(apid_row, text="AppID:", font=("Consolas",8),
                 fg=DARK["text2"], bg=bg).pack(side="left")
        tk.Entry(apid_row, textvariable=appid_var, width=9,
                 bg=DARK["entry_bg"], fg=DARK["accent"], font=("Consolas",9),
                 relief="flat", highlightthickness=1,
                 highlightbackground=DARK["border"],
                 insertbackground=DARK["text"]).pack(side="left", padx=(3,8))

        # DLC count badge
        dlc_count = len(g.get("dlcs", []))
        dlc_color = DARK["green"] if dlc_count > 0 else DARK["text2"]
        dlc_text  = f"{dlc_count} DLCs" if g.get("steam_fetched") else "not fetched"
        g["_dlc_lbl"] = tk.Label(apid_row, text=dlc_text, font=("Consolas",8),
                                  fg=dlc_color, bg=bg)
        g["_dlc_lbl"].pack(side="left")

        # Right side buttons
        right_btns = tk.Frame(inner, bg=bg)
        right_btns.pack(side="right")

        # Status badge
        if patched:
            s_txt, s_fg, s_bg = "✓ patched", DARK["green"], "#1a3a28"
        else:
            s_txt, s_fg, s_bg = "● stock",   DARK["accent"], DARK["entry_bg"]
        tk.Label(right_btns, text=s_txt, font=("Consolas",8),
                 fg=s_fg, bg=s_bg, padx=6, pady=2).pack(side="right", padx=(6,0))

        # Fetch from Steam button
        tk.Button(right_btns, text="☁ fetch",
                  command=lambda idx=i: self._fetch_one_steam(idx),
                  **self._bs("ghost")).pack(side="right", padx=2)

        # Search AppID by name (only if no appid yet)
        if not g.get("appid"):
            tk.Button(right_btns, text="🔍 find ID",
                      command=lambda idx=i: self._search_one_appid(idx),
                      **self._bs("ghost")).pack(side="right", padx=2)

        # Patch / Restore
        if not patched:
            tk.Button(right_btns, text="⚡ patch",
                      command=lambda idx=i: self._patch_one(idx),
                      **self._bs("success")).pack(side="right", padx=2)
        else:
            tk.Button(right_btns, text="↩ restore",
                      command=lambda idx=i: self._restore_one(idx),
                      **self._bs("ghost")).pack(side="right", padx=2)

    # ── AUTO DETECT ───────────────────────────────────────────────────────────

    def _on_api_key_change(self):
        """Sync the API key StringVar → runtime dict immediately."""
        _RUNTIME_KEY["value"] = self.api_key.get().strip() or DEFAULT_STEAM_KEY

    def _on_close(self):
        """Save config then quit."""
        self._save_config()
        self.destroy()

    def _save_config(self):
        data = {
            "library_path":  self.lib_path.get(),
            "username":      self.username.get(),
            "steamid":       self.steamid.get(),
            "dll_path":      self._saved_dll,
            "steam_api_key": self.api_key.get().strip(),
        }
        save_config(data)
        self.log("Config saved.", "ok")

    def _load_saved_dll(self):
        """On startup, re-load the DLL from the path saved in config."""
        path = self._saved_dll
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    data = f.read()
                if "64" in os.path.basename(path).lower():
                    self.gbe64 = data
                else:
                    self.gbe32 = data
                self.dll_lbl.config(text=f"⬤ ready ({os.path.basename(path)})", fg=DARK["green"])
                self.log(f"DLL loaded from saved path: {path}", "ok")
            except Exception as ex:
                self.log(f"Could not reload saved DLL: {ex}", "warn")

    def _auto_detect(self):
        paths = find_steam_paths()
        if paths:
            self.lib_path.set(paths[0])
            self.log(f"Steam library: {paths[0]}", "act")
            threading.Thread(target=self._scan_thread, daemon=True).start()
        else:
            self.log("Steam not found — browse manually.", "warn")

    # ── SCAN ──────────────────────────────────────────────────────────────────

    def _browse_lib(self):
        p = filedialog.askdirectory(title="Select Steam steamapps/common folder")
        if p:
            self.lib_path.set(p)
            self._save_config()
            self._scan()

    def _scan(self):
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        path = self.lib_path.get()
        if not path or not os.path.isdir(path):
            self.log("Invalid library path.", "err"); return
        self.log(f"Scanning {path} ...", "act")
        games = scan_games(path)
        self.games = games
        found_with_id = sum(1 for g in games if g["appid"])
        self.log(f"Found {len(games)} game(s), {found_with_id} with AppID from manifest.", "ok")
        self.after(0, self._render_games)

    # ── APPID SEARCH BY NAME ─────────────────────────────────────────────────

    def _search_one_appid(self, i):
        """Search Steam for AppID using the cleaned folder name."""
        def run():
            g = self.games[i]
            clean = g.get("clean_name") or clean_game_name(g["name"])
            self.log(f'  [{g["name"]}] searching: "{clean}" ...', "act")
            try:
                appid, steam_name = search_steam_appid(clean)
                if appid:
                    self.games[i]["appid"]    = appid
                    self.games[i]["acf_name"] = steam_name
                    avar = g.get("_appid_var")
                    if avar:
                        self.after(0, lambda v=appid: avar.set(v))
                    self.log(f"  [{g['name']}] → {steam_name} (AppID {appid})", "ok")
                else:
                    self.log(f'  [{g["name"]}] no match for "{clean}"', "warn")
            except Exception as ex:
                self.log(f"  [{g['name']}] search failed: {ex}", "err")
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

    def _search_all_appids(self):
        """Search Steam for AppIDs for every game that doesn't have one yet."""
        def run():
            missing = [(i, g) for i, g in enumerate(self.games) if not g.get("appid")]
            self.log(f"Searching AppIDs for {len(missing)} game(s) without one...", "act")
            for i, g in missing:
                clean = g.get("clean_name") or clean_game_name(g["name"])
                try:
                    appid, steam_name = search_steam_appid(clean)
                    if appid:
                        self.games[i]["appid"]    = appid
                        self.games[i]["acf_name"] = steam_name
                        avar = g.get("_appid_var")
                        if avar:
                            self.after(0, lambda v=appid: avar.set(v))
                        self.log(f"  [{g['name']}] → {steam_name} ({appid})", "ok")
                    else:
                        self.log(f'  [{g["name"]}] no match for "{clean}"', "warn")
                    time.sleep(0.4)
                except Exception as ex:
                    self.log(f"  [{g['name']}] {ex}", "err")
            self.log("AppID search done.", "ok")
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

        # ── STEAM API FETCH ───────────────────────────────────────────────────────

    def _fetch_one_steam(self, i):
        def run():
            g = self.games[i]
            appid_var = g.get("_appid_var")
            appid = (appid_var.get() if appid_var else g["appid"]).strip()
            if not appid:
                self.log(f"  [{g['name']}] no AppID — enter one manually.", "warn")
                return
            self.log(f"  [{g['name']}] fetching Steam info for AppID {appid}...", "act")
            try:
                info = fetch_steam_info(appid)
                if not info:
                    self.log(f"  [{g['name']}] Steam API returned no data.", "warn")
                    return
                self.games[i]["acf_name"] = info["game_name"] or g["acf_name"]
                # Fetch DLC names individually
                dlc_ids = [d[0] for d in info["dlcs"]]
                if dlc_ids:
                    self.log(f"  [{g['name']}] found {len(dlc_ids)} DLC(s), fetching names...", "act")
                    dlc_name_map = fetch_dlc_names(dlc_ids)
                    self.games[i]["dlcs"] = [(did, dlc_name_map.get(did, f"DLC_{did}"))
                                             for did in dlc_ids]
                else:
                    self.games[i]["dlcs"] = []
                self.games[i]["steam_fetched"] = True
                self.games[i]["appid"] = appid
                self.log(f"  [{g['name']}] OK — {info['game_name']} | {len(dlc_ids)} DLC(s)", "ok")
            except Exception as ex:
                self.log(f"  [{g['name']}] fetch failed: {ex}", "err")
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

    def _fetch_all_steam(self):
        def run():
            self.log(f"Fetching Steam info for {len(self.games)} game(s)...", "act")
            for i, g in enumerate(self.games):
                appid_var = g.get("_appid_var")
                appid = (appid_var.get() if appid_var else g["appid"]).strip()
                if not appid:
                    self.log(f"  [{g['name']}] skipped — no AppID", "warn")
                    continue
                try:
                    info = fetch_steam_info(appid)
                    if not info:
                        self.log(f"  [{g['name']}] no data from Steam", "warn")
                        continue
                    self.games[i]["acf_name"] = info["game_name"] or g["acf_name"]
                    dlc_ids = [d[0] for d in info["dlcs"]]
                    if dlc_ids:
                        dlc_name_map = fetch_dlc_names(dlc_ids)
                        self.games[i]["dlcs"] = [(did, dlc_name_map.get(did, f"DLC_{did}"))
                                                 for did in dlc_ids]
                    else:
                        self.games[i]["dlcs"] = []
                    self.games[i]["steam_fetched"] = True
                    self.games[i]["appid"] = appid
                    self.log(f"  [{g['name']}] {info['game_name']} | {len(dlc_ids)} DLC(s)", "ok")
                    time.sleep(0.5)   # polite rate limit
                except Exception as ex:
                    self.log(f"  [{g['name']}] {ex}", "err")
            self.log("Steam fetch complete.", "ok")
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

    # ── DOWNLOAD GBE DLL ──────────────────────────────────────────────────────

    def _download_gbe(self):
        self.dl_status.set("Downloading...")
        self.dll_lbl.config(text="⬤ downloading...", fg=DARK["yellow"])
        threading.Thread(target=self._dl_thread, daemon=True).start()

    def _dl_thread(self):
        try:
            self.after(0, lambda: self.dl_status.set("Connecting to GitHub..."))
            req = urllib.request.Request(GBE_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            self.after(0, lambda: self.dl_status.set("Extracting..."))
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
            dll64 = next((n for n in names
                          if n.lower().endswith("steam_api64.dll")
                          and "experimental" not in n.lower()), None)
            dll32 = next((n for n in names
                          if n.lower().endswith("steam_api.dll")
                          and "64" not in n.lower()
                          and "experimental" not in n.lower()), None)
            if dll64: self.gbe64 = zf.read(dll64); self.log(f"DLL64: {dll64}", "ok")
            if dll32: self.gbe32 = zf.read(dll32); self.log(f"DLL32: {dll32}", "ok")
            if self.gbe64 or self.gbe32:
                # Cache DLL to disk next to script so it survives restarts
                cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gbe_steam_api64.dll")
                try:
                    if self.gbe64:
                        with open(cache_path, "wb") as _f: _f.write(self.gbe64)
                        self._saved_dll = cache_path
                        self._save_config()
                except Exception: pass
                self.after(0, lambda: self.dll_lbl.config(text="⬤ ready", fg=DARK["green"]))
                self.after(0, lambda: self.dl_status.set("✓ Downloaded!"))
                self.log("gbe_fork DLL ready.", "ok")
            else:
                raise Exception("DLL not found in zip.")
        except Exception as ex:
            self.log(f"Download failed: {ex}", "err")
            self.after(0, lambda: self.dl_status.set(f"Error: {ex}"))
            self.after(0, lambda: self.dll_lbl.config(text="⬤ failed", fg=DARK["red"]))

    def _load_dll_file(self):
        path = filedialog.askopenfilename(
            title="Select gbe_fork steam_api64.dll",
            filetypes=[("DLL","*.dll"),("All","*.*")])
        if path:
            with open(path, "rb") as f:
                self.gbe64 = f.read()
            self._saved_dll = path
            self._save_config()
            self.dll_lbl.config(text="⬤ ready (manual)", fg=DARK["green"])
            self.dl_status.set(f"Loaded: {os.path.basename(path)}")
            self.log(f"DLL loaded: {path}", "ok")

    # ── PATCH / RESTORE ───────────────────────────────────────────────────────

    def _build_configs(self, game):
        name  = self.username.get() or "Player"
        sid   = self.steamid.get()  or random_steamid()
        avar  = game.get("_appid_var")
        appid = (avar.get() if avar else game["appid"]).strip()
        user  = CONFIGS_USER_TPL.format(name=name, steamid=sid)

        # Build DLC.txt content (gbe_fork format: appid=Name, one per line)
        dlcs = game.get("dlcs", [])
        dlc_txt = "\n".join(f"{did}={dname}" for did, dname in dlcs) if dlcs else ""

        return {
            "configs.main.ini":    CONFIGS_MAIN,
            "configs.user.ini":    user,
            "configs.app.ini":     CONFIGS_APP,
            "configs.overlay.ini": CONFIGS_OVERLAY,
            "DLC.txt":             dlc_txt,
            "steam_appid.txt":     appid,
        }

    def _do_patch(self, game):
        if not self.gbe64 and not self.gbe32:
            self.log("No DLL loaded — download or load gbe_fork first.", "err")
            return False
        dll_dir   = game.get("dll_dir") or game["path"]
        game_root = game["path"]
        name      = game["name"]
        try:
            if game["has64"] and self.gbe64:
                orig = os.path.join(dll_dir, "steam_api64.dll")
                bak  = os.path.join(dll_dir, "steam_api64.dll.bak")
                if not os.path.isfile(bak):
                    shutil.copy2(orig, bak)
                    self.log(f"  [{name}] backed up steam_api64.dll", "ok")
                with open(orig, "wb") as f: f.write(self.gbe64)
                self.log(f"  [{name}] patched steam_api64.dll", "ok")

            if game["has32"] and self.gbe32:
                orig32 = os.path.join(dll_dir, "steam_api.dll")
                bak32  = os.path.join(dll_dir, "steam_api.dll.bak")
                if not os.path.isfile(bak32):
                    shutil.copy2(orig32, bak32)
                    self.log(f"  [{name}] backed up steam_api.dll", "ok")
                with open(orig32, "wb") as f: f.write(self.gbe32)
                self.log(f"  [{name}] patched steam_api.dll", "ok")

            # Write steam_settings/ next to the DLL
            sd = os.path.join(dll_dir, "steam_settings")
            os.makedirs(sd, exist_ok=True)
            configs = self._build_configs(game)
            for fname, content in configs.items():
                if fname == "steam_appid.txt":
                    for dest in set([dll_dir, game_root]):
                        if content:
                            with open(os.path.join(dest, fname), "w") as f:
                                f.write(content)
                elif fname == "DLC.txt":
                    if content:
                        with open(os.path.join(sd, fname), "w", encoding="utf-8") as f:
                            f.write(content)
                        dlc_count = content.count("\n") + 1
                        self.log(f"  [{name}] wrote DLC.txt ({dlc_count} DLC entries)", "ok")
                    # even if empty, don't write — gbe_fork is fine without it
                else:
                    with open(os.path.join(sd, fname), "w", encoding="utf-8") as f:
                        f.write(content)

            self.log(f"  [{name}] steam_settings → {sd}", "ok")
            # Auto-fetch achievement schema if we have an appid and it's not already there
            ach_path = os.path.join(sd, "achievements.json")
            appid_val = (game.get("_appid_var") and game["_appid_var"].get()) or game.get("appid","")
            if appid_val and not os.path.isfile(ach_path):
                threading.Thread(
                    target=lambda g=game, ap=appid_val, sp=sd: self._auto_fetch_ach_schema(g, ap, sp),
                    daemon=True).start()
            return True
        except Exception as ex:
            self.log(f"  [{name}] FAILED: {ex}", "err")
            return False

    def _auto_fetch_ach_schema(self, game, appid, settings_dir):
        """Background: fetch + write achievements.json after patching."""
        try:
            schema = fetch_achievements_schema(appid)
            if schema:
                # Download icons → steam_settings/images/
                images_dir = os.path.join(settings_dir, "images")
                schema = download_achievement_images(
                    appid, schema, images_dir,
                    log_cb=lambda m: self.log(m, "act"))
                path = os.path.join(settings_dir, "achievements.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(schema, f, indent=2, ensure_ascii=False)
                self.log(f"  [{game['name']}] achievements.json written ({len(schema)} entries)", "ok")
                # If this game is open in ach tab, refresh schema
                idx = self._ach_game_idx
                if idx is not None and idx < len(self.games) and self.games[idx]["name"] == game["name"]:
                    self._ach_schema = schema
                    self.after(0, self._ach_render)
        except Exception as ex:
            self.log(f"  [{game['name']}] ach schema fetch failed: {ex}", "warn")

    def _do_restore(self, game):
        dll_dir  = game.get("dll_dir") or game["path"]
        name     = game["name"]
        restored = False
        for dll, bak in [("steam_api64.dll","steam_api64.dll.bak"),
                         ("steam_api.dll",  "steam_api.dll.bak")]:
            bp = os.path.join(dll_dir, bak)
            dp = os.path.join(dll_dir, dll)
            if os.path.isfile(bp):
                shutil.copy2(bp, dp)
                os.remove(bp)
                self.log(f"  [{name}] restored {dll}", "ok")
                restored = True
        if not restored:
            self.log(f"  [{name}] no backup found", "warn")
        return restored

    def _patch_one(self, i):
        def run():
            ok = self._do_patch(self.games[i])
            if ok:
                self.games[i]["patched"]   = True
                self.games[i]["has_bak64"] = True
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

    def _restore_one(self, i):
        def run():
            self._do_restore(self.games[i])
            self.games[i]["patched"]   = False
            self.games[i]["has_bak64"] = False
            self.games[i]["has_bak32"] = False
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

    def _patch_all(self):
        def run():
            targets = [g for g in self.games if not g["patched"]]
            self.log(f"Patching {len(targets)} game(s)...", "act")
            for g in targets:
                if self._do_patch(g):
                    g["patched"] = True; g["has_bak64"] = True
            self.log("Patch all done.", "ok")
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()

    def _restore_all(self):
        def run():
            targets = [g for g in self.games if g["patched"]]
            self.log(f"Restoring {len(targets)} game(s)...", "act")
            for g in targets:
                self._do_restore(g)
                g["patched"] = False; g["has_bak64"] = False; g["has_bak32"] = False
            self.log("Restore all done.", "ok")
            self.after(0, self._render_games)
        threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    PatcherApp().mainloop()
