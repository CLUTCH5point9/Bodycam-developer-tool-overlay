## IF YOU JUST WANT TO USE THE APP, IGNORE ALL OTHER FILES

**Go to the folder named `0 - THE APP IF U JUST WANT TO USE IT IGNORE OTHER FILES`
and download `Bodycam Overlay.exe`. That's the whole app. Everything else in
this repo is source code you don't need to touch.**

To use it: **close Bodycam first**, then run `Bodycam Overlay.exe` — it
installs UE4SS/ClaudeBridge automatically. Once that's done, launch Bodycam.
The app runs in your system tray; press **Insert** to show/hide it, and
right-click the tray icon any time to exit.

---

# Bodycam Overlay

A standalone desktop control panel for Bodycam — **not injected into the game
process**. Toggle it with **Insert**. Requires the game to run in **windowed
or borderless** mode (a separate window can't render on top of exclusive
fullscreen).

Created by **clutch5.9**. Licensed under the [MIT License](0-LICENSE).

## Setup (one-time)

This folder is self-contained — it bundles its own copy of the ClaudeBridge
UE4SS mod under `mod/ClaudeBridge/`, and a full copy of UE4SS itself under
`ue4ss_bundle/`, so it doesn't depend on anything outside this directory.

```
pip install -r requirements.txt
python install_bridge.py
```

`install_bridge.py` finds your Bodycam install (or asks for the path). If
UE4SS isn't already installed there, it deploys the bundled copy; either way
it then installs/updates the ClaudeBridge mod and adds `ClaudeBridge : 1` to
`mods.txt` if it isn't already there. **Fully restart Bodycam afterward** —
a brand new mod folder isn't picked up by Ctrl+R hot-reload, only a real
restart. The packaged .exe (see below) runs this same check automatically on
startup, so most people never need to run this script by hand.

## Run it

```
python overlay_app.py
```

Or run the packaged `dist/BodycamOverlay.exe` (see **Packaging as an .exe**
below) — same behavior, no Python install required, and it repairs its own
UE4SS/ClaudeBridge setup on first launch.

Press **Insert** in-game to show/hide the window. Closing the window (the X
button) just hides it, same as Insert — to fully quit, use **Exit** from the
system tray icon (right-click it), or **Task Manager** if running from
source without the tray.

If the status bar says "not responding," the game either isn't running,
isn't the same install `install_bridge.py` set up, or wasn't fully restarted
after installing the mod.

## What it does

**Host / Create Match tab**
- Pick any map — playlist maps and dev/unreleased maps (dev inventory room,
  empty loadout map, MoonTown, drone racetracks, etc.) are both listed the
  same way.
- Pick a gamemode, player cap, and (for team modes) team cap.
- Toggle Private (sets a session password) and Bots.
- **Load Custom Match** hosts and travels there directly.
- **↻ Cycle Current Match** reads your *actual current* map/mode/cap/team
  size live from the game, ends the round, and reloads the exact same
  configuration.
- **Force Round End** forces a safe window for cap writes via the score-limit
  trick.
- Live State panel shows phase/population/cap live from the game.

**Loadout Editor tab**
- Pick any of your loadouts (count is read from the save file, not
  hardcoded).
- **Operator**, and each of the 5 slots, has a **Change** button that opens a
  searchable list pulled *live* from the running game (`DT_OperatorSkins`,
  `DT_NewShopItem`). New skins/operators added by a game update show up here
  automatically, no code changes needed.
- Slot changes let you pick *any* category (Primary/Secondary/Melee/Lethal/
  Perk/Other), not just the slot's usual one.
- **Set as Active Loadout** calls the game's own `SelectNewCurrentLoadout`.

**Game Speed tab** — Slomo control plus quick 3x speed / reset buttons.

**Console tab** — a raw Lua console into the live game process, with saved
history and a "Save as Button..." action. See
**[1-DOCUMENTATION.md](1-DOCUMENTATION.md)** §1 for exactly how this works,
what globals are available, and its safety model.

**Saved Command Buttons tab** — every snippet you've saved from the Console
tab, as a scrollable list of Run Once buttons and Toggle checkboxes. Supports
per-button delete, and **Import** / **Export All** / **Export Selected**
(via a checkbox next to each button) using JSON files, so you can share a
set of commands with someone else.

**Plugins tab** — load a shareable JSON "plugin" file (same shape as a Saved
Command Buttons export, plus a name and optional section labels/dividers)
as its own sub-tab. See **[1-DOCUMENTATION.md](1-DOCUMENTATION.md)** §3 for
the exact file format, the Run Once vs Toggle mechanism, and how to write
one from scratch or by exporting from Console.

**Shell tab** — runs raw Bash scripts on your own PC via Git Bash, entirely
separate from the game. Also covered in **[1-DOCUMENTATION.md](1-DOCUMENTATION.md)** §2.

**About tab** — credits and license info.

## Why some data is hand-maintained (`families.json`)

Reading a weapon's actual in-game category tag crashes the process, so
**individual items are always pulled live** (skins, operators, new maps),
but **which family belongs to which slot category** is hand-curated in
`families.json` (with `maps.json`/`gamemodes.json` alongside it for maps and
modes) since that almost never changes. All three live in
`%LOCALAPPDATA%\BodycamOverlay\` once the app has run once — edit the copy
there to change behavior without rebuilding the exe. See
**[1-DOCUMENTATION.md](1-DOCUMENTATION.md)** §5.3 for exactly how to add a
new weapon family.

## Packaging as an .exe

```
build.bat
```
Produces `dist\BodycamOverlay.exe`. **Re-run this any time the `.py` or
`.json` files change** — the exe is a build artifact, the Python source is
what you actually edit.

## Project layout

```
overlay_app.py       Tkinter UI -- all tabs
game_api.py           High-level API: bridges live Lua calls + save-file edits
bridge_client.py       Talks to ClaudeBridge (file-based RPC into the game)
gvas2.py                Loadout.sav binary format reader/writer
shell_client.py        Runs Shell tab scripts via Git Bash
install_bridge.py      Finds the game, deploys UE4SS + ClaudeBridge
families.json, maps.json, gamemodes.json    Hand-curated config (see above)
mod/ClaudeBridge/       The UE4SS Lua mod this whole app talks to
ue4ss_bundle/            A full copy of RE-UE4SS (MIT-licensed, see its own LICENSE)
1-DOCUMENTATION.md    Console/Shell/Plugins reference, troubleshooting, and internals
```

## Known limitations / things worth testing more

- **Attachments aren't fully rebuilt on a slot change** — only the weapon +
  bundle are swapped, plus a couple of attachment rows for weapons known to
  need specific parts to fire (currently just the Crossbow: Trigger +
  Arrow). Other weapons keep whatever attachments the slot already had.
- **Bot on/off** writes `HMS_bBotsMethod`, but `ShouldSpawnBots()` doesn't
  always honor that flag — the player cap is what actually controls how
  many bots get filled.
- **Global hotkey needs the `keyboard` library**, which on some systems
  needs the app run as Administrator to hook Insert while the game has
  focus.
- **The save file is Steam Cloud-synced** — edits made through the Loadout
  tab apply in-session, but a full game restart before the game itself
  re-saves can revert them. Cycle or reselect the loadout in the same
  session to make it stick.
- **Spawning arbitrary actors (e.g. grenades) crashes the game** — confirmed
  via crash dump analysis (null-pointer dereference right after
  `SpawnActor`, most likely GAS-related initialization the game's own item
  spawn path does that a raw `SpawnActor` skips). There is no Spawn tab for
  this reason; don't reintroduce raw actor spawning without solving that.

## License

[MIT](0-LICENSE) for this application's own code. The bundled UE4SS under
`ue4ss_bundle/` carries its own MIT license from its original author. Plugin
files people write for the Plugins tab are their own separate work — see
**[1-DOCUMENTATION.md](1-DOCUMENTATION.md)** §3.6.
