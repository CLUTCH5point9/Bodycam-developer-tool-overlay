# Bodycam Overlay — Documentation

One reference for everything past the README quick-start: how the Console
and Shell tabs work, the Plugin/Saved-Command-Button file format,
troubleshooting, and the internal design decisions behind the trickier parts
of the source (for anyone reading or forking the code). This file replaces
the old separate `CONSOLE_AND_SHELL.txt`, `PLUGINS.txt`, and
`TROUBLESHOOTING.md` — everything they covered is here, under one table of
contents, so there's one place to search instead of three.

## Contents

1. [Console tab — Lua inside the game](#1-console-tab--lua-inside-the-game)
2. [Shell tab — Bash on your PC](#2-shell-tab--bash-on-your-pc)
3. [Plugin / Saved Command Buttons file format](#3-plugin--saved-command-buttons-file-format)
4. [Troubleshooting](#4-troubleshooting)
5. [Internals & design notes](#5-internals--design-notes)

---

## 1. Console tab — Lua inside the game

There are two separate raw-execution tabs. They are **not** the same thing
and do **not** run in the same place — mixing them up is the most common
source of confusion.

- **Console tab** → runs Lua **inside** the running Bodycam game process.
- **Shell tab** → runs a Bash script on **your PC**, outside the game
  entirely (see §2).

### How it works

The Console tab talks to a small UE4SS Lua mod called ClaudeBridge
(`mod/ClaudeBridge/Scripts/main.lua`), which must already be installed and
running inside the game. The flow, every time you press "Run":

1. `overlay_app.py`'s `ConsoleTab` takes what you typed and prepends one
   line: `local pc = UEHelpers.GetPlayerController()`. That's the only
   thing it adds — your code runs exactly as written otherwise.
2. That combined string is written to a request file under
   `%LOCALAPPDATA%\Temp\bodycam_bridge\req.txt` (see `bridge_client.py`).
3. ClaudeBridge, polling that folder from inside the game's own Lua VM
   every 200ms, picks it up, compiles it, and runs it on the game's main
   thread (via `ExecuteInGameThread` — the only thread it's safe to touch
   UObjects from).
4. Whatever it returns (or prints) is written to `resp.txt`.
5. `bridge_client.py` reads that back and the Console tab prints it.

This round trip is why there's a timeout, and why "the game isn't
responding" almost always means one of: Bodycam isn't running, it's a
different install than the one `install_bridge.py` set up, or ClaudeBridge
was added to `mods.txt` but the game was hot-reloaded (Ctrl+R) instead of
fully restarted — a brand new mod folder is only picked up on a real
restart.

### Globals available in every Console payload

(all injected by ClaudeBridge itself — see `main.lua`'s `handle()` function)

| Global | What it does |
|---|---|
| `pawn()` | The local player's current pawn, or `nil`. Checks `PlayerController.Pawn`, then `.AcknowledgedPawn`, then falls back to `UEHelpers.GetPlayer()`. Most snippets build on this. |
| `pc` | **Not** a ClaudeBridge global — added by the Console tab itself, one line prepended to your code. Just `UEHelpers.GetPlayerController()`, for convenience. |
| `UEHelpers` | The standard UE4SS helper module (`GetWorld`, `GetGameInstance`, `GetPlayerController`, `GetPlayer`, etc). |
| `props(obj)` | Dumps every readable property on `obj` (or `pawn()` if omitted), walking the full class chain (own class + every parent up to but not including `UObject`). Only "safe" property types are read (ints, floats, bools, names, object/class references) — structs, arrays, maps and sets are listed by name/type only, never read, because marshalling those across the Lua/native boundary is a known crash risk. Delegates are skipped entirely. |
| `funcs(obj)` | Same class-chain walk as `props()`, but lists every `UFunction` name instead of property values. Doesn't show the signature, just that it exists. |
| `count(clsName)` | `FindAllOf(clsName)` and prints how many instances exist right now, plus up to 20 full paths. Good for "does this class even exist / is anything spawned". |
| `render(v)` | Turns any Lua value (including a UObject/struct) into a readable string — what `print()` uses internally, so multi-value prints render sensibly instead of raw userdata addresses. |
| `valid(o)` | True only if `o` is non-nil AND `o:IsValid()` returns true. Use before touching any object you didn't just get from `pawn()`/`UEHelpers` — a stale/destroyed reference used directly can crash the game. |
| `chainOf(obj)` | The list of `UClass` objects from `obj`'s own class up to (not including) `UObject`. Mostly useful for writing your own `props()`/`funcs()`-style walker. |
| `has(obj, name)` | True only if `obj` really has a live `UFunction` called `name`. Needed because a missing function shows up as UE4SS's `TrivialObject` placeholder, which is also `"userdata"` — `type()` alone can't tell the difference; `has()` checks the actual `tostring()` prefix. |

**Return values**: if your code ends with `return <something>`, ClaudeBridge
appends it after any `print()` output as `-- return: <rendered value>`. The
Console tab shows everything (prints + marker); the structured `game_api.py`
functions strip everything except the value itself.

**History**: Alt+Up / Alt+Down cycle through what you've run this session
(in-memory only, not saved to disk). Same mechanism as the Shell tab (§2).

**Errors**: every payload runs inside a `pcall` on the game's side. A Lua
error in your code comes back as a normal error message in the output box,
not a crash — but that only covers Lua-level errors. Calling a native
engine function with bad arguments, or touching an invalid/freed UObject,
can still crash the actual game process the same way any other UE4SS Lua
mod can. **There is no sandbox here.** Test one small thing at a time.

**Saving a snippet as a button**: "Save as Button..." next to Run opens a
small dialog asking for a name and Run Once vs Toggle — see §3 for what
Toggle mode means and how saved buttons relate to the Plugins tab (same
underlying mechanism).

---

## 2. Shell tab — Bash on your PC

The Shell tab has nothing to do with the game. It runs on **your computer**,
as **your** Windows user, through Git Bash (`shell_client.py` hard-codes
`C:\Program Files\Git\usr\bin\bash.exe`). Whatever you type is written to a
temp `.sh` file and executed with `bash <file>`, same as opening a terminal
yourself and running a script.

There is deliberately **no sandboxing**. It has exactly the same access to
your filesystem, environment variables, and installed tools as a terminal
window you opened by hand — because the moment you sandbox a "shell tab" it
stops being a real shell and starts being a toy. Bash syntax (heredocs,
`export`, pipes, `cat`, `curl`, whatever Git Bash's userland provides) all
works normally.

Practical implications:

- It can read/write any file your Windows account can, not just files
  inside this project folder.
- A script here can launch other programs, hit the network, install things,
  or delete things. There's a Timeout field (default 60s) that kills a
  runaway script — that's the only real guard rail.
- It does **not** reach into the Bodycam process. If you want to affect the
  running game, that's the Console tab's job (§1), not this one. A shell
  script *can* write a Lua file to disk and could, in principle, launch a
  separate tool that talks to the same bridge protocol Console/
  `bridge_client.py` use — but the Shell tab itself never touches the game
  directly.
- History (Alt+Up/Down) works the same as the Console tab, in-memory only.

If a script needs a real path instead of Git Bash's `/c/...` mount style,
remember Git Bash still understands normal Windows paths in quotes, e.g.
`"C:\Users\you\Desktop\file.txt"` — no need to convert everything to
`/c/Users/you/Desktop/file.txt` by hand.

---

## 3. Plugin / Saved Command Buttons file format

### 3.1 The big picture

A "plugin" is a plain JSON file describing a tab full of buttons (plus
optional section labels/dividers). Nothing more. Loading one doesn't run
any Python code from the file, doesn't install anything, and can't do
anything the Console tab couldn't already do by hand — it's purely a way to
package and share a themed set of console commands as one file instead of
retyping them.

Saved Command Buttons (its own tab) and Plugins use the **exact same**
widget format under the hood. The difference is just scope and storage:

| | Saved Command Buttons | Plugin |
|---|---|---|
| Scope | One flat personal list | A named, shareable bundle, with optional labels/separators for layout |
| Added via | "Save as Button..." in the Console tab | "Add Plugin..." in the Plugins tab |
| Stored at | `%LOCALAPPDATA%\BodycamOverlay\snippets.json` | `%LOCALAPPDATA%\BodycamOverlay\plugins\<name>.json` |

Because they're the same shape, exporting your Saved Command Buttons
(Export All / Export Selected) produces a file that already works as a
plugin — add a `"plugin_name"` key at the top and it's a valid plugin file.

### 3.2 The file format

```json
{
  "plugin_name": "Movement Toggles",
  "widgets": [
    { "widget": "label", "label": "Movement" },
    {
      "widget": "button",
      "label": "Fly",
      "mode": "toggle",
      "code": "local p = pawn()\nif not p then return 'no pawn' end\nif TOGGLE_ON then\n  p:SetActorEnableCollision(false)\n  return 'fly ON'\nelse\n  p:SetActorEnableCollision(true)\n  return 'fly OFF'\nend"
    },
    { "widget": "separator" },
    {
      "widget": "button",
      "label": "Kill Self",
      "mode": "run_once",
      "code": "local p = pawn()\nif not p then return 'no pawn' end\npcall(function() p:K2_DestroyActor() end)\nreturn 'done'"
    }
  ]
}
```

Top level:

- `plugin_name` (plugin files only — ignored/optional for a plain Saved
  Command Buttons export): string shown as the tab's title.

`widgets` is a list, rendered top to bottom, in order. Each entry has a
`"widget"` field:

- **`"label"`** — `{ "widget": "label", "label": "Section title text" }`.
  Just a bold heading. No code, no button.
- **`"separator"`** — `{ "widget": "separator" }`. A horizontal divider
  line. No other fields.
- **`"button"`** (or omit `"widget"` entirely — `"button"` is the default,
  which is why a plain Saved Command Buttons file, which predates plugins,
  still loads fine):
  - `label` — button text.
  - `code` — the Lua source to run. Exactly what you'd type into the
    Console tab — same globals available (see §1). **Note**: unlike the
    live Console tab, a plugin button's code does **not** get the `pc`
    (PlayerController) convenience variable prepended — call
    `UEHelpers.GetPlayerController()` yourself if you need it.
  - `mode` — either `"run_once"` or `"toggle"`. Defaults to `"run_once"` if
    omitted.

### 3.3 Run Once vs Toggle

**run_once**: click the button, your code runs once, done. No state is
tracked.

**toggle**: renders as a checkbox instead of a plain button. Every click,
*before* your code runs, the app injects one extra line ahead of it:

```lua
local TOGGLE_ON = true     -- or false, depending on the checkbox's
                            -- NEW state after this click
```

Your code is responsible for checking `TOGGLE_ON` and doing whatever "on"
and "off" mean for that feature — there's no separate on-code/off-code
fields, it's one script that branches on that one variable. This mirrors
how the app's own built-in toggles (bot-fill, explosive bullets) work
internally (see §5), just exposed for anything you write yourself.

If your code errors out, the checkbox automatically reverts to its previous
state (the click is treated as if it didn't happen), so the checkbox never
lies about whether the effect actually applied.

The checkbox's on/off state lives only in the running app — it is **not**
saved to disk and does **not** reflect anything read back from the game. If
you restart the overlay, every toggle button starts unchecked again
regardless of what's actually active in-game. Write your "off" branch so
it's safe to run even if the real state doesn't match the checkbox (e.g.
don't assume a prior "on" definitely ran).

### 3.4 How to actually make one

Easiest path — build it through the UI, then export it:

1. Console tab: write and test your Lua snippet until it does what you
   want. Test run_once and toggle behavior for real before saving it.
2. Click "Save as Button...", give it a name, pick Run Once or Toggle.
3. Repeat for as many commands as you want in the pack.
4. Saved Command Buttons tab: check the boxes next to the ones you want to
   share, click "Export Selected...", save the `.json` file.
5. Open that file in a text editor and add a `"plugin_name"` line at the
   top level (see §3.2), and optionally insert `"label"`/`"separator"`
   widgets to organize it into sections.
6. That file is now a valid plugin. Plugins tab → "Add Plugin..." → pick
   it. It's copied into `%LOCALAPPDATA%\BodycamOverlay\plugins\` and shows
   up as its own sub-tab immediately, and every time the app starts from
   then on.

Or write the JSON by hand from scratch using the format in §3.2 — there's
nothing the UI does that the file format doesn't support directly.

**Removing a plugin**: select its sub-tab in the Plugins tab, click "Remove
Selected Plugin". This deletes the underlying file in
`%LOCALAPPDATA%\BodycamOverlay\plugins\` — there's no per-button removal
inside a plugin the way Saved Command Buttons has (a plugin is meant to be
edited as a file and re-added, not picked apart button by button in the
UI).

### 3.5 Editing an existing plugin

A plugin's live copy sits in `%LOCALAPPDATA%\BodycamOverlay\plugins\` as a
plain JSON file. To change one:

1. Close the overlay (or at least don't rely on hot-editing while it's
   running — plugin tabs are only built at startup / on Add Plugin).
2. Edit the `.json` file directly, or remove it via the UI and re-Add a new
   corrected version.
3. Restart the overlay (or Add Plugin again) to see the change.

There's no in-app JSON editor — deliberately. Plugin authoring is meant to
happen in a real text editor where you have syntax highlighting and can
actually see what you're writing; the app's job is just to load, run, and
manage the resulting files.

### 3.6 Licensing of plugin files

The Bodycam Overlay application itself (everything in this repo except
plugin content) is MIT-licensed — see `0-LICENSE`.

A plugin file you write is your own separate work. This project doesn't
claim any license over plugin content, and loading someone else's plugin
doesn't grant you any rights to redistribute it beyond whatever that
plugin's own author says (in the plugin file itself, a README next to it,
wherever they choose to say it). If you're sharing a plugin publicly,
consider stating your own license/terms somewhere in the file — e.g. a
`"label"` widget at the top, or just alongside it wherever you post it — so
people who receive it know where they stand. This project has no mechanism
to enforce that either way; it's on the honor system between plugin authors
and whoever they share with.

### 3.7 Safety note

A plugin's code runs with exactly the same access as anything you'd type
into the Console tab yourself — full Lua reflection into the live game
process, no sandbox. Adding someone else's plugin means running their Lua
code, unreviewed, inside your game. Read a plugin's `code` fields before
adding it, the same way you'd read a script before running it in a real
shell.

---

## 4. Troubleshooting

### Buttons / tools not working?

If the overlay's buttons or tools stop responding, it's usually caused by
stale or corrupted files from a previous session. Follow these steps in
order.

1. **Check the Console first.** Open it and look for error messages before
   doing anything else — this often tells you exactly what's broken and can
   save you a full reset. If you see clear errors referencing missing
   files, mods, or scripts, note them down. If the console is empty or the
   errors aren't helpful, continue below.
2. **Exit the overlay.** System tray (bottom-right of the taskbar, near the
   clock) → right-click the overlay icon → **Exit**.
3. **Close the game.** Fully close Bodycam — check Task Manager if you're
   unsure it's really gone.
4. **Delete these folders entirely** (not just the files inside them),
   replacing `<YourUsername>` with your actual Windows username:
   - `C:\Users\<YourUsername>\AppData\Local\Temp\bodycam_bridge`
   - `C:\Program Files (x86)\Steam\steamapps\common\Bodycam\Bodycam\Binaries\Win64\ue4ss`
5. **Relaunch the game.** Both folders regenerate automatically with fresh
   files, which resolves most issues caused by corrupted or outdated data.
6. **Still not working?** Open the console again and check for errors. If
   the same error persists after a full folder reset, that's a sign the
   issue isn't just stale files — please report the console output when
   opening an issue on this repo.

| Step | Action |
|---|---|
| 1 | Check console for errors |
| 2 | Exit overlay from system tray |
| 3 | Close the game |
| 4 | Delete `bodycam_bridge` folder AND `ue4ss` folder |
| 5 | Relaunch the game |
| 6 | Re-check console if issue persists |

### "Not responding" in the status bar

Almost always one of: Bodycam isn't running, it's a different install than
the one `install_bridge.py` set up, or the game was hot-reloaded (Ctrl+R)
instead of fully restarted after the mod was installed — a brand new mod
folder needs a real restart. See §1's "How it works" for the full round
trip this message covers.

---

## 5. Internals & design notes

Background for anyone reading, maintaining, or forking the source — the
"why" behind decisions that aren't obvious from the code alone. Per-function
comments in the source point back here instead of repeating this.

### 5.1 Bridge protocol (`bridge_client.py` ↔ `mod/ClaudeBridge/Scripts/main.lua`)

File-based RPC, chosen because it needs no open port and no extra
dependency on either side (game console isn't reachable from Python, and
the game process can't accept a normal client connection). Both sides write
to a temp name and atomically rename into place, so neither ever reads a
half-written file.

```
req.txt   line 1 = request id (integer), lines 2+ = Lua source
resp.txt  line 1 = request id
          line 2 = OK | ERR
          lines 3+ = captured print() output, then "-- return:" + value
```

Lives under `%LOCALAPPDATA%\Temp` because a game launched by a store client
usually cannot write inside Program Files without elevation.

Safety properties, all load-bearing (not decorative):

- **One `LoopAsync`, one `ExecuteInGameThread` per request**, gated by a
  `busy` flag — overlapping in-game-thread callbacks crash in
  `process_simple_actions` (a known unfixed UE4SS bug as of this writing).
- **The request is consumed before execution**, so a payload that crashes
  the game cannot replay itself on the next launch.
- **Every payload runs under `pcall`**; a Lua error is reported, not fatal
  — but that only covers Lua-level errors. A bad native call or a
  touched-invalid-UObject can still crash the real game process. There is
  no sandbox.

### 5.2 GVAS save-file editing (`gvas2.py`)

UE5.5's GVAS property format, for `Loadout.sav`, laid out as:

```
Property := Name:FString  TypeName  Size:int32  HasGuid:u8  Payload[Size]
TypeName := Name:FString  ParamCount:int32  ParamCount * TypeName   (recursive)
```

`gvas2.py` walks this structure to find every property's byte region.
Knowing where each `Size` field lives lets it change a string's length in
place and fix up every *enclosing* property's `Size` by the same delta —
that's what `set_rowname`/`replace_payload` do. `game_api.py` calls
`backup_save()` before every write for exactly this reason: it's a raw
binary patch, not a round-tripped re-serialization, so a parsing mistake on
an unexpected save-file shape is a real risk worth having a `.backup-*`
copy for.

### 5.3 Why some data is hand-maintained (`families.json`)

Reading a weapon's actual in-game category tag crashes the process — it's a
`GameplayTagContainer` read, a documented crash in the UE4SS skills this
tool is built on. So instead: **individual items are always pulled live**
(skins, operators, new maps), but **which family belongs to which slot
category** lives in `families.json`, since that almost never changes.

If a game update adds a **wholly new weapon family** (not just a new skin),
add one line to `families.json`:

```json
"NewGun Basic Bundle": {"category": "primary", "prefixes": ["NewGun"]}
```

`category` is one of `primary`, `secondary`, `melee`, `lethal`, `perk`,
`other`. `prefixes` is the item-name prefix(es) used for that weapon's
"Base X" rows in `DT_NewShopItem` — check with the game running:

```python
import game_api as api
[i for i in api._get_shop_items() if "NewGun" in i]
```

Most weapons use their bundle's own name as the prefix. A few don't (M4A1's
items are named "AR15 Base ...", Remington700 sometimes shows up as
"Rivington 700 ..."), which is why `prefixes` is a list, not a single
string.

`maps.json` and `gamemodes.json` are similarly small and hand-curated —
edit them the same way if the game adds a new map or mode. All three files
live in `%LOCALAPPDATA%\BodycamOverlay\` once the app has run once (seeded
from the copies in this folder on first launch) — edit the copy there to
change behavior without rebuilding the exe.

### 5.4 `install_bridge.py` — why bundling UE4SS is on the right side of the line

`ue4ss_bundle/` holds a straight copy of the UE4SS + enabler-mod files that
were already installed and running on the author's own machine — not
anything downloaded fresh from the internet at install time. Deploying a
user's own, already-vetted files to a game folder they own is a different
thing from an exe silently fetching and planting unknown injection tooling;
that's the line this stays on the right side of. If `ue4ss_bundle/` is ever
missing (e.g. a fresh checkout of just the source, without re-running the
bundling step), setup falls back to pointing at the official UE4SS release
page instead of guessing.

`has_ue4ss()` doesn't just check the `ue4ss/` folder exists — a real
install found live also needs `ue4ss/Mods/shared/UEHelpers/UEHelpers.lua`,
the shared Lua library several mods (ClaudeBridge included) `require()`.
Without it, every such mod crashes on its first line with `module UEHelpers
not found` — a silent, total failure that looks like a connectivity problem
from the overlay's side rather than a missing-file one, so the check has to
catch that specifically.

### 5.5 `game_api.py` — live-game hacks and why they're shaped this way

- **`write_cap` / `host_and_travel`**: each gamemode has its **own**
  persistent cap asset (confirmed live: switching Deathmatch cap=20 →
  Versus came back at Versus's own default of 10), so the cap is only
  written *after* the target mode's GameMode instance actually exists —
  i.e. after the travel, not before. `write_cap` also refuses to write
  while `CurrentPhase` is `StartRound` (returns `'ABORT: ...'`); the caller
  (`write_cap_retrying`) polls until that window passes.
- **Bots**: `HMS_bBotsMethod` lives on the GameMode, not the GameInstance,
  but flipping it doesn't actually change `ShouldSpawnBots()`'s answer —
  that logic is buried in a generic Settings-Manager UI system with no
  direct property/setter reachable by reflection. `spawn_bots_to_target`
  bypasses it entirely, manually calling `GameMode:SpawnBot()` once per 4
  seconds (spawning 25 in one callback froze the game once during testing)
  and is generation-guarded — calling it again supersedes any fill already
  in progress rather than stacking a second timer, the same pattern
  `enable_all_nametags` uses for its own keeper loop.
- **`set_explosive_bullets`**: hooks `WEP_C:SpawnImpactEffects`, which
  fires on every bullet impact — an explicitly hot-path hook, so its
  guards are load-bearing: the enabled check happens first (a disabled
  toggle costs one table lookup, not a damage call), the hook registers
  exactly once per process, and the actual work is a single native
  `ApplyRadialDamage` call, all pcall-wrapped.
- **`cycle_match`**: the game's own mode rotation loads per-mode-prefixed
  level names (`DM_Airsoft`, `TDM_BombHouse`, `GG_CQB`, ...), not the bare
  package name in `maps.json` — a known mode prefix is stripped before
  comparing (confirmed live: rotation landed on `"DM_BombHouse"` while
  `maps.json` has `"BombHouse"`).
- **Spawning arbitrary actors (e.g. grenades) crashes the game** — confirmed
  via crash dump analysis (null-pointer dereference right after
  `SpawnActor`, most likely GAS-related initialization the game's own item
  spawn path does that a raw `SpawnActor` skips). There is no Spawn tab for
  this reason; don't reintroduce raw actor spawning without solving that.

### 5.6 `overlay_app.py` — a few non-obvious mechanisms

- **Single-instance lock**: binding a fixed local TCP port (`47821`) is
  used purely as a mutex — a second launch failing to bind means an
  instance is already running. Two copies would each register their own
  Insert hotkey (the "opens two windows" symptom) *and* race on the same
  bridge req/resp files with no locking between them, which reproduces as
  one instance's response getting consumed by the other's request.
- **Tray icon / `quit_app`**: closing the window (X button) just hides it
  (so Insert keeps working), rather than quitting — the tray's "Exit" is
  the only clean shutdown path besides Task Manager. `quit_app` calls
  `os._exit(0)` rather than relying on the Tk mainloop returning naturally,
  because the `keyboard` library's hook thread isn't guaranteed to be a
  daemon thread; just destroying the Tk root could leave the process
  hanging around after "Exit".
- **Setup check on startup**: `install_bridge.ensure_setup()` runs once
  before the first connection poll, so a fresh install (or one missing
  ClaudeBridge/UE4SS) gets fixed automatically rather than surfacing as a
  confusing "not responding" status. See §5.4 for what that check verifies.
- **About tab**: a plain, editable Python class like anything else — there
  is no way to make credit "tamper-proof" in a project whose source is
  public. The actual, enforceable mechanism for keeping attribution
  attached is the MIT license itself (see `0-LICENSE`): it requires the
  copyright notice to be kept in any redistributed copy, source or binary.
  The tab exists to make that credit visible, not to prevent someone from
  deleting it.
