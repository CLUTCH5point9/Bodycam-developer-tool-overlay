"""High-level API the overlay UI calls. Wraps bridge_client (live game RPC) and
gvas2 (Loadout.sav file editing) behind clean functions.

Design rule: individual items (skins, operators, maps found on disk) are always
pulled live/fresh; only the family->category mapping in families.json is
hand-maintained (see 1-DOCUMENTATION.md section 5.3 for why). Rationale for
the trickier live-game hacks below (bot fill, explosive bullets, cap/travel
ordering, cycle_match's map-name matching) is centralized in that same file,
section 5.5, rather than repeated per function.
"""
import json
import os
import re
import shutil
import sys

import bridge_client as bc
import gvas2

# PyInstaller onefile builds extract bundled data (see build.bat's --add-data)
# to a temp dir exposed as sys._MEIPASS -- recreated fresh (and wiped) on every
# launch. Plain `python overlay_app.py` runs use this file's own directory.
_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
SAVE_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Bodycam", "Saved", "SaveGames", "Loadout.sav")

_GAME_ROOT_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Bodycam",
    r"C:\Program Files\Steam\steamapps\common\Bodycam",
]

# families.json / maps.json / gamemodes.json are meant to be hand-editable
# (see README) -- that only works if edits survive a restart, which _HERE
# alone can't guarantee in a packaged exe. Seed a persistent copy in AppData
# on first run, then always read/write THAT copy; the bundled files under
# _HERE are just the initial defaults, never touched again after seeding.
_CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", _HERE), "BodycamOverlay")
os.makedirs(_CONFIG_DIR, exist_ok=True)
for _cfg_name in ("families.json", "maps.json", "gamemodes.json"):
    _dest = os.path.join(_CONFIG_DIR, _cfg_name)
    if not os.path.exists(_dest):
        shutil.copy2(os.path.join(_HERE, _cfg_name), _dest)


def _load_json(name):
    """Loads a config file, dropping any "_..." documentation keys (see
    families.json's own "_comment")."""
    with open(os.path.join(_CONFIG_DIR, name), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


FAMILIES, MAPS, GAMEMODES = {}, {}, {}
_CONFIG_FILES = {"families.json": FAMILIES, "maps.json": MAPS, "gamemodes.json": GAMEMODES}


def reload_configs():
    """Re-reads families.json / maps.json / gamemodes.json from disk in place
    (so a running overlay picks up hand edits without restarting)."""
    for _fname, _target in _CONFIG_FILES.items():
        _target.clear()
        _target.update(_load_json(_fname))


reload_configs()


# --------------------------------------------------------------------------- connectivity
def is_connected(timeout=3.0):
    ok, _ = bc.ping(timeout=timeout)
    return ok


def run_raw_lua(code, timeout=20.0):
    """For the Console tab: run arbitrary Lua on the game thread, full output
    (print() lines + the '-- return:' marker, if any) returned untouched."""
    return bc.run_lua_raw(code, timeout=timeout)


# --------------------------------------------------------------------------- saved console snippets
# NOT under _HERE: in a packaged exe, _HERE is PyInstaller's onefile temp
# extraction dir, recreated fresh (and wiped) on every launch -- anything
# written there is lost the moment the app closes. Saved snippets need an
# actual persistent, writable location regardless of frozen/dev-run state.
# Same _CONFIG_DIR the families/maps/gamemodes JSON files were seeded into above.
_SNIPPETS_PATH = os.path.join(_CONFIG_DIR, "snippets.json")
_PLUGINS_DIR = os.path.join(_CONFIG_DIR, "plugins")
os.makedirs(_PLUGINS_DIR, exist_ok=True)


def _normalize_widgets(data):
    """Accepts either the current {'widgets': [...]} shape or the original
    flat {name: code} shape saved buttons used before they had a mode -- so
    files/snippets.json saved before this existed still load correctly."""
    if isinstance(data, dict) and "widgets" in data:
        return list(data["widgets"])
    if isinstance(data, dict):
        return [{"widget": "button", "label": name, "code": code, "mode": "run_once"}
                for name, code in data.items()]
    return []


def list_snippets():
    """Saved Command Buttons, as a flat list of button widget dicts
    ({"widget": "button", "label", "code", "mode"})."""
    if not os.path.exists(_SNIPPETS_PATH):
        return []
    with open(_SNIPPETS_PATH, encoding="utf-8") as f:
        return _normalize_widgets(json.load(f))


def _write_snippets(widgets):
    with open(_SNIPPETS_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "widgets": widgets}, f, indent=2)


def save_snippet(name, code, mode="run_once"):
    widgets = [w for w in list_snippets() if w.get("label") != name]
    widgets.append({"widget": "button", "label": name, "code": code, "mode": mode})
    _write_snippets(widgets)


def delete_snippet(name):
    _write_snippets([w for w in list_snippets() if w.get("label") != name])


def export_snippets(path, labels=None):
    """Exports all saved buttons, or only the given ones if `labels` is given
    (used by the Saved Command Buttons tab's Export Selected)."""
    widgets = list_snippets()
    if labels is not None:
        widgets = [w for w in widgets if w.get("label") in labels]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "widgets": widgets}, f, indent=2)


def import_snippets(path):
    """Returns the button widgets found in `path` (labels/separators, which
    only make sense for a Plugin's layout, are dropped here since Saved
    Command Buttons is just a flat list). Caller decides how to merge/handle
    name conflicts against the existing saved buttons."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [w for w in _normalize_widgets(data) if w.get("widget", "button") == "button"]


# --------------------------------------------------------------------------- plugins
# A plugin is a JSON file shaped like {"plugin_name": ..., "widgets": [...]}
# -- the same widget shape Saved Command Buttons uses, plus a name and
# optional "label"/"separator" widgets for layout. Deployed copies live under
# _PLUGINS_DIR (same persistent-AppData pattern as families.json/snippets.json)
# so they survive restarts and PyInstaller's MEIPASS wipe.
def list_plugins():
    out = []
    for fname in sorted(os.listdir(_PLUGINS_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(_PLUGINS_DIR, fname), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        out.append({"id": fname, "plugin_name": data.get("plugin_name", fname[:-5]),
                    "widgets": data.get("widgets", [])})
    return out


def add_plugin(source_path):
    with open(source_path, encoding="utf-8") as f:
        data = json.load(f)
    if "widgets" not in data:
        raise ValueError("Not a valid plugin file: missing 'widgets'.")
    plugin_name = data.get("plugin_name") or os.path.splitext(os.path.basename(source_path))[0]
    safe = "".join(c for c in plugin_name if c.isalnum() or c in " _-").strip() or "plugin"
    dest_name, counter = safe + ".json", 2
    while os.path.exists(os.path.join(_PLUGINS_DIR, dest_name)):
        dest_name = f"{safe} ({counter}).json"
        counter += 1
    data["plugin_name"] = plugin_name
    with open(os.path.join(_PLUGINS_DIR, dest_name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"id": dest_name, "plugin_name": plugin_name, "widgets": data.get("widgets", [])}


def remove_plugin(plugin_id):
    path = os.path.join(_PLUGINS_DIR, plugin_id)
    if os.path.exists(path):
        os.remove(path)


# --------------------------------------------------------------------------- live catalog (cached per process run)
_cache = {"operators": None, "shop_items": None}


def get_operators(force=False):
    """All operator skin row names, live from DT_OperatorSkins."""
    if _cache["operators"] is None or force:
        body = bc.run_lua(
            "local dt=StaticFindObject('/Game/BodycamCore/ItemsDefinition/Skins/DT_OperatorSkins.DT_OperatorSkins')\n"
            "local rows=dt:GetRowNames()\n"
            "local out={}\n"
            "for _,rn in ipairs(rows) do out[#out+1]=tostring(rn) end\n"
            "return table.concat(out, '\\n')",
            timeout=20,
        )
        _cache["operators"] = sorted(l.strip() for l in body.splitlines() if l.strip())
    return list(_cache["operators"])


def _get_shop_items(force=False):
    """Every row name in DT_NewShopItem, live. Cached -- this table is large (700+ rows)."""
    if _cache["shop_items"] is None or force:
        body = bc.run_lua(
            "local dt=StaticFindObject('/Game/BodycamCore/ItemsDefinition/DT_NewShopItem.DT_NewShopItem')\n"
            "local rows=dt:GetRowNames()\n"
            "local out={}\n"
            "for _,rn in ipairs(rows) do out[#out+1]=tostring(rn) end\n"
            "return table.concat(out, '\\n')",
            timeout=30,
        )
        _cache["shop_items"] = [l.strip() for l in body.splitlines() if l.strip()]
    return _cache["shop_items"]


_ATTACHMENT_HINTS = (
    "trigger", "barrel", "grip", "stock", "magazine", "muzzle", "optic", "wheel",
    "shell", "antenna", "device", "spoiler", "reticle", "cylinder", "hammer",
    "slide", "forearm", "upperbarrel", "underbarrel", "ammo", "mount", "laser",
    "flashlight", "siderail", "sticker",
)


def bundles_by_category(category):
    return [name for name, info in FAMILIES.items() if info.get("category") == category]


def find_weapon_variants(bundle_name):
    """Skin variants for a bundle's base item(s), live-filtered from the shop catalog.
    Returns a sorted list of item row names, e.g. ['AR15 Base Anime', 'M4A1 Base Receiver', ...]."""
    info = FAMILIES.get(bundle_name)
    if not info:
        return []
    prefixes = info["prefixes"]
    category = info["category"]
    items = _get_shop_items()
    matches = set()

    if category == "perk":
        for p in prefixes:
            for it in items:
                if it.startswith(p) and (it.endswith("Chassis") or it.endswith("Drone")):
                    matches.add(it)
        if not matches:
            # fall back to a loose substring search, excluding obvious attachment parts
            for p in prefixes:
                for it in items:
                    low = it.lower()
                    if p.lower() in low and not any(h in low for h in _ATTACHMENT_HINTS):
                        matches.add(it)
    elif category == "lethal":
        for p in prefixes:
            for it in items:
                if it == p or it.startswith(p + " "):
                    low = it.lower()
                    if not any(h in low for h in _ATTACHMENT_HINTS):
                        matches.add(it)
    else:  # primary / secondary / melee / other
        for p in prefixes:
            pat = re.compile(r"^" + re.escape(p) + r" Base ", re.IGNORECASE)
            for it in items:
                if pat.match(it) or it.lower() == (p + " base receiver").lower():
                    matches.add(it)
        if not matches:
            # Some weapons (e.g. the Crossbow) don't use the "<prefix> Base <skin>"
            # convention at all -- fall back to a plain prefix match, excluding
            # rows that look like attachment parts rather than the weapon itself.
            for p in prefixes:
                for it in items:
                    low = it.lower()
                    if low.startswith(p.lower()) and not any(h in low for h in _ATTACHMENT_HINTS):
                        matches.add(it)

    return sorted(matches)


def find_bundle_for_weapon(item_name):
    """Reverse lookup: given a chosen weapon/vehicle item, find its bundle name."""
    for bundle_name, info in FAMILIES.items():
        for p in info["prefixes"]:
            if item_name.startswith(p):
                return bundle_name
    return None


def get_default_attachments_for(bundle_name):
    """A minimal, known-working attachment set for special-case weapons that need
    specific parts to function (mirrors what we verified by hand tonight)."""
    special = {
        "TenpointStealthCrossbow Basic Bundle": ["Crossbow Trigger", "Arrow"],
    }
    return special.get(bundle_name, [])


# --------------------------------------------------------------------------- maps (offline, from curated list)
def list_maps():
    return dict(MAPS)


def list_gamemodes():
    return dict(GAMEMODES)


# --------------------------------------------------------------------------- live match state
def get_live_state(timeout=15):
    """Returns dict: connected, map_gamemode_class, mode_name, phase, count, max, team_size."""
    lua = r"""
local function vld(o) if o==nil then return false end local ok,v=pcall(function() return o:IsValid() end) return ok and v==true end
local function isreal(v) return v ~= nil and not tostring(v):find("^TrivialObject") end
local gm = (FindAllOf('GameModeBase') or {})[1]
local gs = (FindAllOf('GameStateBase') or {})[1]
if not vld(gm) or not vld(gs) then return 'NOMATCH' end
local cls = '?'; pcall(function() cls = gm:GetClass():GetFName():ToString() end)
local ph = '?'; pcall(function() ph = gs.CurrentPhase.TagName:ToString() end)
local n = -1; pcall(function() n = gs:GetNumPlayersAndBot() end)
local mx = -1; pcall(function() mx = gs:GetMaxPlayers() end)
local tms = -1
pcall(function()
    local cfg = gm.ConfigDataAsset
    if isreal(cfg) then
        local tc = cfg.TeamConfig
        if isreal(tc) and isreal(tc.TeamMaxSize) then tms = tc.TeamMaxSize end
    end
end)
return cls .. '|' .. ph .. '|' .. tostring(n) .. '|' .. tostring(mx) .. '|' .. tostring(tms)
"""
    body = bc.run_lua(lua, timeout=timeout).strip()
    if body == "NOMATCH":
        return {"connected": True, "in_match": False}
    cls, ph, n, mx, tms = body.split("|")
    mode_name = None
    for name, info in GAMEMODES.items():
        if info["class"].endswith(cls + "_C") or cls == info["class"].split(".")[-1]:
            mode_name = name
            break

    if mode_name is None:
        # Not one of the 7 known playable modes -- this is the Lobby (GM_Bodycam_C)
        # or a transitional state during map rotation (e.g. GM_Host_C, seen live
        # tonight). Neither is a "real match" as far as cap/cycle logic cares.
        return {"connected": True, "in_match": False, "gamemode_class_short": cls}

    def _num(s, default=-1):
        try:
            return int(s)
        except ValueError:
            return default

    return {
        "connected": True,
        "in_match": True,
        "gamemode_class_short": cls,
        "mode_name": mode_name,
        "phase": ph,
        "count": _num(n),
        "max": _num(mx),
        "team_size": _num(tms),
    }


def force_round_end(timeout=15):
    lua = r"""
local gs = (FindAllOf('GameStateBase') or {})[1]
if not gs then return 'no gamestate' end
local ok = pcall(function() gs:OverrideScoreLimit(2) end)
return tostring(ok)
"""
    return bc.run_lua(lua, timeout=timeout)


def write_cap(cap, team_size=None, bots=None, timeout=15):
    """Writes MaxPlayers (and optionally TeamMaxSize, HMS_bBotsMethod) only if
    not in StartRound (see 1-DOCUMENTATION.md §5.5). Returns a status string;
    caller should check for 'ABORT' and retry."""
    ts_line = f"pcall(function() gm.ConfigDataAsset.TeamConfig.TeamMaxSize={team_size} end)" if team_size is not None else ""
    bots_line = f"pcall(function() gm.HMS_bBotsMethod={str(bool(bots)).lower()} end)" if bots is not None else ""
    lua = f"""
local gm = (FindAllOf('GameModeBase') or {{}})[1]
local gs = (FindAllOf('GameStateBase') or {{}})[1]
if not gm or not gs then return 'no match loaded' end
local ph = '?'; pcall(function() ph = gs.CurrentPhase.TagName:ToString() end)
if ph:find('StartRound') then return 'ABORT: ' .. ph end
pcall(function() gm.ConfigDataAsset.TeamConfig.MaxPlayers = {cap} end)
{ts_line}
{bots_line}
local gi = UEHelpers.GetGameInstance()
pcall(function() gi['Session Max Players'] = {cap} end)
local sess = (FindAllOf('GameSession') or {{}})[1]
if sess then pcall(function() sess.MaxPlayers = {cap} end) end
local sb = '?'; pcall(function() sb = tostring(gm:ShouldSpawnBots()) end)
return 'WROTE phase=' .. ph .. ' ShouldSpawnBots=' .. sb
"""
    return bc.run_lua(lua, timeout=timeout)


def write_cap_retrying(cap, team_size=None, bots=None, attempts=8, delay=2.0, timeout=15):
    import time
    for i in range(attempts):
        result = write_cap(cap, team_size=team_size, bots=bots, timeout=timeout)
        if "WROTE" in result:
            return result
        time.sleep(delay)
    return result


def spawn_bots_to_target(target_count, timeout=15):
    """Manually fills to target_count via GameMode:SpawnBot(), one per 4s,
    bypassing ShouldSpawnBots()/HMS_bBotsMethod entirely -- see
    1-DOCUMENTATION.md §5.5 for why. Generation-guarded: calling this again
    supersedes any fill already in progress rather than stacking a second
    timer."""
    lua = f"""
local function vld(o) if o==nil then return false end local ok,v=pcall(function() return o:IsValid() end) return ok and v==true end
local gm = (FindAllOf('GameModeBase') or {{}})[1]
local gs = (FindAllOf('GameStateBase') or {{}})[1]
if not vld(gm) or not vld(gs) then return 'no match loaded' end
_G.BOTFILL = _G.BOTFILL or {{}}
_G.BOTFILL.gen = (_G.BOTFILL.gen or 0) + 1
local MYGEN = _G.BOTFILL.gen
local TARGET = {target_count}
local function step()
    if _G.BOTFILL.gen ~= MYGEN then return end
    local gm2 = (FindAllOf('GameModeBase') or {{}})[1]
    local gs2 = (FindAllOf('GameStateBase') or {{}})[1]
    if not vld(gm2) or not vld(gs2) then return end
    local n = 0; pcall(function() n = gs2:GetNumPlayersAndBot() end)
    if n >= TARGET then return end
    local b; pcall(function() b = gm2:SpawnBot() end)
    if vld(b) then
        ExecuteWithDelay(2500, function() ExecuteInGameThread(function()
            pcall(function() if not vld(b.Pawn) then gm2:RestartPlayer(b) end end)
        end) end)
    end
    ExecuteWithDelay(4000, function() ExecuteInGameThread(step) end)
end
ExecuteWithDelay(1000, function() ExecuteInGameThread(step) end)
return 'bot fill gen ' .. MYGEN .. ' armed, target=' .. TARGET
"""
    return bc.run_lua(lua, timeout=timeout)


def set_explosive_bullets(enabled, damage=50.0, radius=300.0, timeout=15):
    """Registers (once per process) a post-hook on WEP_C:SpawnImpactEffects --
    fires on EVERY bullet impact -- and, while enabled, calls the engine's own
    ApplyRadialDamage at the hit location. A hot-path hook, so its guards
    (enabled-check first, one-time registration, pcall-wrapped) are
    load-bearing, not decorative -- see 1-DOCUMENTATION.md §5.5. Not
    independently verified against live rapid-fire yet -- test with a few
    individual shots before trusting it in a real firefight."""
    lua = f"""
_G.EXPLOSIVE_BULLETS = _G.EXPLOSIVE_BULLETS or {{enabled = false, damage = 50.0, radius = 300.0}}
_G.EXPLOSIVE_BULLETS.enabled = {str(bool(enabled)).lower()}
_G.EXPLOSIVE_BULLETS.damage = {damage}
_G.EXPLOSIVE_BULLETS.radius = {radius}

if not _G.EXPLOSIVE_BULLETS_HOOKED then
    _G.EXPLOSIVE_BULLETS_HOOKED = true
    RegisterHook("/Game/BodycamWeapons/Core/Blueprint/WEP.WEP_C:SpawnImpactEffects",
        function() end,
        function(Context)
            if not (_G.EXPLOSIVE_BULLETS and _G.EXPLOSIVE_BULLETS.enabled) then return end
            pcall(function()
                local self = Context:get()
                local hitLoc = Context.HitLocation
                if not hitLoc then return end
                local GS = StaticFindObject('/Script/Engine.Default__GameplayStatics')
                local w = UEHelpers.GetWorld()
                GS:ApplyRadialDamage(w, _G.EXPLOSIVE_BULLETS.damage, hitLoc,
                    _G.EXPLOSIVE_BULLETS.radius, nil, {{}}, self, nil, true, 0)
            end)
        end)
end
return 'explosive bullets: enabled=' .. tostring(_G.EXPLOSIVE_BULLETS.enabled) ..
       ' damage=' .. tostring(_G.EXPLOSIVE_BULLETS.damage) ..
       ' radius=' .. tostring(_G.EXPLOSIVE_BULLETS.radius) ..
       ' (hook registered=' .. tostring(_G.EXPLOSIVE_BULLETS_HOOKED) .. ')'
"""
    return bc.run_lua(lua, timeout=timeout)


def enable_all_nametags(timeout=15):
    """Forces every W_PlayerIndicator_C (the same widget that shows teammate
    nametags in Versus/TDM) visible, regardless of team -- a periodic keeper
    loop since these widgets are pooled/reused and new ones appear as players
    come in range. Whether a name actually populates depends on the game's own
    proximity/line-of-sight logic, which can't be verified by reflection alone
    -- try it live and see what shows up."""
    lua = r"""
_G.NAMETAGS = _G.NAMETAGS or {}
_G.NAMETAGS.gen = (_G.NAMETAGS.gen or 0) + 1
local MYGEN = _G.NAMETAGS.gen
local function tick()
    if _G.NAMETAGS.gen ~= MYGEN then return end
    local ws = FindAllOf('W_PlayerIndicator_C') or {}
    local forced = 0
    for _, w in ipairs(ws) do
        local ok = pcall(function()
            w.bShouldBeHidden = false
            w:SetVisibility(0)
        end)
        if ok then forced = forced + 1 end
    end
    _G.NAMETAGS.lastCount = forced
    ExecuteWithDelay(1500, function() ExecuteInGameThread(tick) end)
end
ExecuteWithDelay(200, function() ExecuteInGameThread(tick) end)
return 'nametag-reveal loop gen ' .. MYGEN .. ' armed'
"""
    return bc.run_lua(lua, timeout=timeout)


def disable_all_nametags(timeout=10):
    """Cancels the enable_all_nametags loop without forcing anything back hidden
    (the game's own team-check logic resumes controlling visibility from here)."""
    return bc.run_lua("_G.NAMETAGS = _G.NAMETAGS or {}; _G.NAMETAGS.gen = (_G.NAMETAGS.gen or 0) + 1; "
                       "return 'stopped'", timeout=timeout)


def stop_bot_fill(timeout=10):
    """Cancels any in-progress spawn_bots_to_target loop without starting a new one."""
    return bc.run_lua("_G.BOTFILL = _G.BOTFILL or {}; _G.BOTFILL.gen = (_G.BOTFILL.gen or 0) + 1; "
                       "return 'stopped'", timeout=timeout)


def host_and_travel(map_path, gamemode_class, cap, team_size, private, bots, session_name="Custom Match", timeout=20):
    """Full flow: end current round if in one (wait for it to settle), travel to
    map+mode, then write cap/team AFTER the new mode has loaded -- each gamemode
    has its own persistent cap asset, so writing it only makes sense once the
    target mode's GameMode instance actually exists (see 1-DOCUMENTATION.md
    §5.5). private=True sets both UpdateLobbyAccessMethod(true) and a session
    password as a second layer; bots=True bypasses HMS_bBotsMethod entirely and
    spawns manually via spawn_bots_to_target() (same section explains why).
    """
    import time
    state = get_live_state(timeout=timeout)
    if state.get("in_match"):
        force_round_end(timeout=timeout)
        time.sleep(15)  # let the phase actually settle before ending/traveling

    password = "ClaudeOverlay" if private else ""
    lua = f"""
local gi = UEHelpers.GetGameInstance()
pcall(function() gi['HostAlone?'] = false end)
pcall(function() gi['Session Use LAN'] = false end)
pcall(function() gi['Session Password'] = {password!r} end)
pcall(function() gi['Session Name'] = {session_name!r} end)
pcall(function() gi['Session Max Players'] = {cap} end)
pcall(function() gi['HMS_ExpectedPlayerCount'] = {cap} end)
pcall(function() gi:UpdateLobbyAccessMethod({str(bool(private)).lower()}) end)
local KSL = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local w = UEHelpers.GetWorld()
local pc = UEHelpers.GetPlayerController()
local ok = pcall(function() KSL:ExecuteConsoleCommand(w, 'servertravel {map_path}?game={gamemode_class}', pc) end)
if {str(not private).lower()} then pcall(function() gi.HMS_AdvertiseSession(gi) end) end
return 'travel issued ok=' .. tostring(ok)
"""
    travel_result = bc.run_lua(lua, timeout=timeout)

    time.sleep(12)  # let the new map/mode actually load before touching its cap/bots
    cap_result = write_cap_retrying(cap, team_size=team_size, bots=bots, attempts=10, delay=2.0, timeout=timeout)

    bot_result = ""
    if bots:
        bot_result = "; " + spawn_bots_to_target(cap, timeout=timeout)
    else:
        stop_bot_fill(timeout=timeout)  # cancel any earlier fill so it doesn't keep adding bots

    return f"{travel_result}; cap: {cap_result}{bot_result}"


def get_current_level_name(timeout=15):
    """Best-effort current level name via UGameplayStatics::GetCurrentLevelName.
    That function returns an FString object, not a plain Lua string -- it needs
    an explicit :ToString() (same as FName), tostring() alone just shows the
    wrapper's type+address."""
    lua = r"""
local GS = StaticFindObject('/Script/Engine.Default__GameplayStatics')
local w = UEHelpers.GetWorld()
local ok, name = pcall(function() return GS:GetCurrentLevelName(w, true) end)
if not ok or name == nil then return 'UNKNOWN' end
local ok2, s = pcall(function() return name:ToString() end)
if ok2 and s and s ~= '' then return s end
return 'UNKNOWN'
"""
    body = bc.run_lua(lua, timeout=timeout).strip()
    return None if body == "UNKNOWN" else body


def cycle_match(fallback_map_path=None, private=False, bots=True, timeout=20):
    """Capture current map/mode/cap/team size, end the match, and reload the exact same thing.
    fallback_map_path is used if the live level name can't be matched to a known map
    (pass the map_path the UI last hosted, if any). private/bots aren't reliably
    readable back from the live game, so the caller should pass through whatever
    it last set (the UI remembers this from its own last host/create-match action)."""
    state = get_live_state(timeout=timeout)
    if not state.get("in_match"):
        raise RuntimeError("not currently in a match -- nothing to cycle")
    mode_name = state.get("mode_name")
    if not mode_name:
        raise RuntimeError(f"couldn't map current gamemode class {state.get('gamemode_class_short')} to a known mode")
    gamemode_class = GAMEMODES[mode_name]["class"]

    level_name = get_current_level_name(timeout=timeout)
    map_path = None
    if level_name:
        # Mode rotation uses per-mode-prefixed level names, not maps.json's bare
        # package name -- strip a known prefix before comparing (1-DOCUMENTATION.md §5.5).
        bare = level_name
        for prefix in ("DM_", "TDM_", "GG_", "HP_", "BB_", "VS_", "WM_"):
            if bare.startswith(prefix):
                bare = bare[len(prefix):]
                break
        for name, info in MAPS.items():
            last_segment = info["path"].rsplit("/", 1)[-1]
            if (last_segment.lower() == bare.lower()
                    or last_segment.lower() == level_name.lower()
                    or name.lower() == bare.lower()):
                map_path = info["path"]
                break
    if map_path is None:
        map_path = fallback_map_path
    if map_path is None:
        raise RuntimeError(
            f"couldn't resolve current level '{level_name}' to a known map path, "
            "and no fallback_map_path was given"
        )

    result = host_and_travel(
        map_path, gamemode_class, state["max"], state["team_size"],
        private=private, bots=bots, session_name="Cycled Match", timeout=timeout,
    )
    return {"map_path": map_path, "mode_name": mode_name, "cap": state["max"],
            "team_size": state["team_size"], "result": result}


# --------------------------------------------------------------------------- loadout file editing
def loadout_count():
    d, regs, rows, L = gvas2.slots(SAVE_PATH)
    return len(L)


def dump_loadout(idx):
    d, regs, rows, L = gvas2.slots(SAVE_PATH)
    lo = L[idx]

    def names(r):
        return [k["value"] for k in gvas2.row_in(rows, r)] if r else []

    return {
        "operator": names(lo["op"])[0] if names(lo["op"]) else None,
        "slots": [
            {"weapon": (names(s["weapon"]) or [None])[0], "bundle": (names(s["bundle"]) or [None])[0]}
            for s in lo["slots"]
        ],
    }


def dump_all_loadouts():
    return [dump_loadout(i) for i in range(loadout_count())]


def backup_save():
    import shutil, time
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = SAVE_PATH + f".backup-{ts}"
    shutil.copy2(SAVE_PATH, dst)
    return dst


def set_operator(loadout_idx, operator_name):
    backup_save()
    return gvas2.set_row_in_region(SAVE_PATH, lambda L, i=loadout_idx: L[i]["op"], operator_name)


def set_slot_weapon(loadout_idx, slot_idx, weapon_item_name, bundle_name=None, attachments=None):
    """Set a loadout slot's weapon (+ bundle, + optionally a couple of attachment rows).
    bundle_name defaults to the reverse-lookup from families.json."""
    backup_save()
    if bundle_name is None:
        bundle_name = find_bundle_for_weapon(weapon_item_name)
        if bundle_name is None:
            raise ValueError(f"don't know the bundle for '{weapon_item_name}' -- pass bundle_name explicitly")

    gvas2.set_row_in_region(SAVE_PATH, lambda L, i=loadout_idx, s=slot_idx: L[i]["slots"][s]["bundle"], bundle_name)
    gvas2.set_row_in_region(SAVE_PATH, lambda L, i=loadout_idx, s=slot_idx: L[i]["slots"][s]["weapon"], weapon_item_name)

    if attachments is None:
        attachments = get_default_attachments_for(bundle_name)
    for att in attachments:
        d, regs, rows, L = gvas2.slots(SAVE_PATH)
        att_region = L[loadout_idx]["slots"][slot_idx]["attachments"]
        ks = gvas2.row_in(rows, att_region)
        idx = attachments.index(att)
        if idx < len(ks):
            gvas2.set_rowname(SAVE_PATH, ks[idx]["str_off"], ks[idx]["value"], att)

    return True


def select_active_loadout(loadout_idx, timeout=15):
    """Calls SelectNewCurrentLoadout on the live PlayerController's loadout manager."""
    lua = f"""
local pc = UEHelpers.GetPlayerController()
local mgr
pcall(function() mgr = pc.BP_LoadoutSaveManagerComponent end)
if not mgr then for _,c in ipairs(FindAllOf('BP_LoadoutSaveManagerComponent_C') or {{}}) do mgr = c end end
if not mgr then return 'manager not found' end
local ok = pcall(function() mgr:SelectNewCurrentLoadout({loadout_idx}) end)
return 'SelectNewCurrentLoadout({loadout_idx}) ok=' .. tostring(ok)
"""
    return bc.run_lua(lua, timeout=timeout)
