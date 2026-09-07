"""Bodycam control overlay -- a separate desktop window (NOT injected into the
game process), toggled with the Insert key. Requires the game to run in
windowed or borderless mode so this window can sit visually on top of it.

Run with:  python overlay_app.py
Requires the game to be running with the ClaudeBridge UE4SS mod loaded.
"""
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import keyboard  # global hotkey
import pystray
from PIL import Image, ImageDraw

import game_api as api
import shell_client
import install_bridge

BG = "#1e1e24"
FG = "#e8e8ec"
ACCENT = "#5b8dee"
BAD = "#e05a5a"
GOOD = "#4caf6a"


class AsyncRunner:
    """Runs blocking calls off the Tk main thread; delivers results back via after()."""

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self._poll()

    def run(self, fn, on_done=None, on_error=None):
        def worker():
            try:
                result = fn()
                self.q.put(("ok", result, on_done, on_error))
            except Exception as e:  # noqa: BLE001
                self.q.put(("err", e, on_done, on_error))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload, on_done, on_error = self.q.get_nowait()
                if kind == "ok" and on_done:
                    on_done(payload)
                elif kind == "err" and on_error:
                    on_error(payload)
                elif kind == "err":
                    print("async error:", payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)


class PickerDialog(tk.Toplevel):
    """A search-as-you-type scrollable list picker. Calls on_pick(value) and closes."""

    def __init__(self, parent, title, items, on_pick):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("420x480")
        self.attributes("-topmost", True)
        self.on_pick = on_pick
        self.all_items = sorted(items)

        self.search_var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self.search_var, bg="#2a2a33", fg=FG, insertbackground=FG)
        entry.pack(fill="x", padx=8, pady=8)
        entry.bind("<KeyRelease>", self._filter)
        entry.focus_set()

        self.listbox = tk.Listbox(self, bg="#2a2a33", fg=FG, selectbackground=ACCENT, activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.listbox.bind("<Double-Button-1>", self._pick)
        entry.bind("<Return>", self._pick)

        self._populate(self.all_items)

    def _populate(self, items):
        self.listbox.delete(0, tk.END)
        for it in items:
            self.listbox.insert(tk.END, it)

    def _filter(self, _evt=None):
        q = self.search_var.get().lower()
        self._populate([it for it in self.all_items if q in it.lower()])

    def _pick(self, _evt=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        value = self.listbox.get(sel[0])
        self.destroy()
        self.on_pick(value)


class SaveButtonDialog(tk.Toplevel):
    """Prompts for a name and Run Once/Toggle when saving a console snippet as
    a reusable button. A toggle button renders as a checkbox and injects
    `local TOGGLE_ON = true/false` ahead of the snippet's own code on every
    click, so a single saved script (checking TOGGLE_ON itself) drives both
    states -- the same shape as the built-in bot-fill/explosive-bullets
    toggles, just authored by whoever wrote the snippet."""

    def __init__(self, parent, on_save):
        super().__init__(parent)
        self.title("Save as Button")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.on_save = on_save

        tk.Label(self, text="Button name:", bg=BG, fg=FG).pack(anchor="w", padx=10, pady=(10, 2))
        self.name_var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self.name_var, bg="#2a2a33", fg=FG,
                          insertbackground=FG, width=32)
        entry.pack(padx=10, fill="x")
        entry.focus_set()

        self.mode_var = tk.StringVar(value="run_once")
        tk.Radiobutton(self, text="Run Once", variable=self.mode_var, value="run_once",
                       bg=BG, fg=FG, selectcolor="#2a2a33").pack(anchor="w", padx=10, pady=(10, 0))
        tk.Radiobutton(self, text="Toggle (adds a checkbox; your code checks TOGGLE_ON)",
                       variable=self.mode_var, value="toggle",
                       bg=BG, fg=FG, selectcolor="#2a2a33").pack(anchor="w", padx=10)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=10)
        tk.Button(btn_row, text="Save", bg=GOOD, fg="white", command=self._save).pack(side="left", padx=6)
        tk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left")
        entry.bind("<Return>", lambda e: self._save())

    def _save(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Enter a button name.", parent=self)
            return
        self.destroy()
        self.on_save(name, self.mode_var.get())


def render_command_widgets(parent, app, widgets, on_delete=None, selection_vars=None):
    """Renders a list of {"widget": "button"/"label"/"separator", ...} specs
    into `parent`, one per row -- shared by SavedButtonsTab and each Plugins
    sub-tab so a Plugin file and a Saved Command Buttons export are the same
    shape. Pass on_delete(label) to add a per-row delete button (used for
    Saved Command Buttons; plugins are removed as a whole file instead).
    Pass a dict as selection_vars to add a plain checkbox to the left of every
    button row -- filled in as {label: BooleanVar} so the caller can read back
    which rows are checked (used for Export Selected)."""
    for spec in widgets:
        kind = spec.get("widget", "button")
        if kind == "label":
            tk.Label(parent, text=spec.get("label", ""), bg=BG, fg=FG,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=6, pady=(10, 2))
            continue
        if kind == "separator":
            ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=6, pady=6)
            continue

        label = spec.get("label", "?")
        code = spec.get("code", "")
        mode = spec.get("mode", "run_once")
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=6, pady=2)

        if selection_vars is not None:
            sel_var = tk.BooleanVar(value=False)
            tk.Checkbutton(row, variable=sel_var, bg=BG, selectcolor="white",
                           activebackground=BG, highlightthickness=0
                           ).pack(side="left", padx=(0, 6))
            selection_vars[label] = sel_var

        var = tk.BooleanVar(value=False) if mode == "toggle" else None

        def make_runner(code=code, mode=mode, label=label, var=var):
            def run():
                if mode == "toggle":
                    state = var.get()
                    prefixed = f"local TOGGLE_ON = {'true' if state else 'false'}\n{code}"
                else:
                    prefixed = code

                def work():
                    return api.run_raw_lua(prefixed)

                def done(result):
                    app.status(f"{label}: {result.strip() if result.strip() else 'OK'}")

                def err(e):
                    app.status(f"ERROR running {label}: {e}", bad=True)
                    if mode == "toggle":
                        var.set(not state)  # run failed -- the checkbox shouldn't have flipped

                app.runner.run(work, done, err)
            return run

        runner = make_runner()
        if mode == "toggle":
            tk.Checkbutton(row, text=label, variable=var, command=runner, bg=BG, fg=FG,
                           selectcolor="#2a2a33", activebackground=BG, anchor="w"
                           ).pack(side="left", fill="x", expand=True)
        else:
            tk.Button(row, text=label, anchor="w", command=runner).pack(side="left", fill="x", expand=True)

        if on_delete:
            tk.Button(row, text="Delete", fg=BAD, command=lambda label=label: on_delete(label)
                      ).pack(side="right", padx=(4, 0))


class HostTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.maps = api.list_maps()
        self.gamemodes = api.list_gamemodes()
        self.last_hosted = None  # dict remembered for Cycle

        left = tk.Frame(self, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        right = tk.Frame(self, bg=BG)
        right.pack(side="right", fill="y", padx=8, pady=8)

        tk.Label(left, text="Map", bg=BG, fg=FG).pack(anchor="w")
        map_names = sorted([m for m, i in self.maps.items() if i["playlist"]]) + \
                    ["--- non-playlist / dev maps ---"] + \
                    sorted([m for m, i in self.maps.items() if not i["playlist"]])
        self.map_list = tk.Listbox(left, bg="#2a2a33", fg=FG, selectbackground=ACCENT, height=12)
        for m in map_names:
            self.map_list.insert(tk.END, m)
        self.map_list.pack(fill="both", expand=True)

        row = tk.Frame(left, bg=BG)
        row.pack(fill="x", pady=(8, 0))

        tk.Label(row, text="Gamemode", bg=BG, fg=FG).grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value=list(self.gamemodes.keys())[0])
        self.mode_cb = ttk.Combobox(row, textvariable=self.mode_var, values=list(self.gamemodes.keys()), state="readonly")
        self.mode_cb.grid(row=0, column=1, sticky="ew", padx=6)
        self.mode_cb.bind("<<ComboboxSelected>>", self._on_mode_change)

        tk.Label(row, text="Cap", bg=BG, fg=FG).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.cap_var = tk.IntVar(value=7)
        tk.Spinbox(row, from_=1, to=64, textvariable=self.cap_var, width=6, bg="#2a2a33", fg=FG).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0))

        tk.Label(row, text="Team Cap", bg=BG, fg=FG).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.team_var = tk.IntVar(value=1)
        self.team_spin = tk.Spinbox(row, from_=1, to=32, textvariable=self.team_var, width=6, bg="#2a2a33", fg=FG)
        self.team_spin.grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))

        self.private_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Private", variable=self.private_var, bg=BG, fg=FG, selectcolor="#2a2a33").grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        self.bots_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="Bots", variable=self.bots_var, bg=BG, fg=FG, selectcolor="#2a2a33").grid(
            row=3, column=1, sticky="w", pady=(6, 0))

        row.columnconfigure(1, weight=1)

        load_btn = tk.Button(left, text="Load Custom Match", bg=ACCENT, fg="white",
                              command=self._load_match)
        load_btn.pack(fill="x", pady=(10, 0))

        # right column: cycle + live state
        tk.Label(right, text="Live State", bg=BG, fg=FG, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.state_box = tk.Text(right, width=28, height=10, bg="#2a2a33", fg=FG, state="disabled")
        self.state_box.pack(pady=(4, 8))

        tk.Button(right, text="Refresh State", command=self._refresh_state).pack(fill="x")
        tk.Button(right, text="Reload Maps/Modes (from disk)", command=self._reload_config).pack(fill="x", pady=(4, 0))
        tk.Button(right, text="↻  Cycle Current Match", bg=GOOD, fg="white",
                  command=self._cycle).pack(fill="x", pady=(8, 0))
        tk.Button(right, text="Force Round End", command=self._force_end).pack(fill="x", pady=(8, 0))

        self._on_mode_change()
        self._refresh_state()

    def _reload_config(self):
        api.reload_configs()
        self.maps = api.list_maps()
        self.gamemodes = api.list_gamemodes()

        self.map_list.delete(0, tk.END)
        for m in sorted([n for n, i in self.maps.items() if i["playlist"]]) + \
                 ["--- non-playlist / dev maps ---"] + \
                 sorted([n for n, i in self.maps.items() if not i["playlist"]]):
            self.map_list.insert(tk.END, m)

        mode_names = list(self.gamemodes.keys())
        self.mode_cb.configure(values=mode_names)
        if mode_names:
            self.mode_var.set(mode_names[0])
            self._on_mode_change()
        self.app.status(f"Reloaded config: {len(self.maps)} maps, {len(self.gamemodes)} gamemodes")

    def _on_mode_change(self, _evt=None):
        info = self.gamemodes[self.mode_var.get()]
        self.cap_var.set(info["default_cap"])
        self.team_var.set(info["default_team_size"])
        self.team_spin.configure(state="normal" if info["team_based"] else "disabled")

    def _selected_map(self):
        sel = self.map_list.curselection()
        if not sel:
            return None
        name = self.map_list.get(sel[0])
        if name.startswith("---"):
            return None
        return name

    def _load_match(self):
        name = self._selected_map()
        if not name:
            messagebox.showwarning("No map selected", "Pick a map from the list first.")
            return
        map_path = self.maps[name]["path"]
        mode_name = self.mode_var.get()
        gm_class = self.gamemodes[mode_name]["class"]
        cap = self.cap_var.get()
        team = self.team_var.get() if self.gamemodes[mode_name]["team_based"] else None
        private = self.private_var.get()
        bots = self.bots_var.get()

        self.app.status(f"Loading {name} ({mode_name})...")

        def work():
            return api.host_and_travel(map_path, gm_class, cap, team or 1, private, bots)

        def done(result):
            self.last_hosted = dict(map_path=map_path, private=private, bots=bots)
            self.app.status(f"Loaded {name}: {result}")
            self.app.root.after(4000, self._refresh_state)

        self.app.runner.run(work, done, self.app.on_error("ERROR loading match"))

    def _refresh_state(self):
        def work():
            return api.get_live_state()

        def done(state):
            self.state_box.configure(state="normal")
            self.state_box.delete("1.0", tk.END)
            if not state.get("in_match"):
                self.state_box.insert(tk.END, "Not in a match\n(in Lobby / menu)")
            else:
                for k in ("mode_name", "phase", "count", "max", "team_size"):
                    self.state_box.insert(tk.END, f"{k}: {state.get(k)}\n")
            self.state_box.configure(state="disabled")

        self.app.runner.run(work, done, self.app.on_error("state check failed"))

    def _cycle(self):
        fallback = self.last_hosted["map_path"] if self.last_hosted else None
        private = self.last_hosted["private"] if self.last_hosted else False
        bots = self.last_hosted["bots"] if self.last_hosted else True
        self.app.status("Cycling current match...")

        def work():
            return api.cycle_match(fallback_map_path=fallback, private=private, bots=bots)

        def done(result):
            self.app.status(f"Cycled: {result['mode_name']} cap={result['cap']} team={result['team_size']}")
            self.app.root.after(4000, self._refresh_state)

        self.app.runner.run(work, done, self.app.on_error("ERROR cycling"))

    def _force_end(self):
        self.app.status("Forcing round end...")

        def work():
            return api.force_round_end()

        def done(result):
            self.app.status(f"Round end: {result}")
            self.app.root.after(2000, self._refresh_state)

        self.app.runner.run(work, done, self.app.on_error())


class LoadoutTab(ttk.Frame):
    CATEGORY_LABELS = {
        "primary": "Primary",
        "secondary": "Secondary (Pistols/Revolvers)",
        "melee": "Melee (Knives)",
        "lethal": "Lethal (Throwables)",
        "perk": "Perk (RC Cars / Drones)",
        "other": "Other",
    }
    SLOT_CATEGORY_HINT = ["primary", "secondary", "melee", "lethal", "perk"]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.loadout_idx = 0

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=8, pady=8)
        tk.Label(top, text="Edit Loadout", bg=BG, fg=FG).pack(side="left")
        self.loadout_var = tk.StringVar()
        self.loadout_cb = ttk.Combobox(top, textvariable=self.loadout_var, state="readonly", width=6)
        self.loadout_cb.pack(side="left", padx=6)
        self.loadout_cb.bind("<<ComboboxSelected>>", self._on_loadout_change)
        tk.Button(top, text="Refresh", command=self._refresh).pack(side="left", padx=6)
        tk.Button(top, text="Set as Active Loadout", bg=ACCENT, fg="white",
                  command=self._set_active).pack(side="left", padx=6)

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=8, pady=8)

        self._rows = {}
        self._build_rows()
        self._load_loadout_count()

    def _build_rows(self):
        specs = [("operator", "Operator")] + [
            (f"slot{i}", f"Slot {i+1} ({self.CATEGORY_LABELS[self.SLOT_CATEGORY_HINT[i]]})") for i in range(5)
        ]
        for key, label in specs:
            row = tk.Frame(self.body, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, bg=BG, fg=FG, width=28, anchor="w").pack(side="left")
            val_lbl = tk.Label(row, text="-", bg="#2a2a33", fg=FG, anchor="w", width=32)
            val_lbl.pack(side="left", padx=6)
            btn = tk.Button(row, text="Change", command=lambda k=key: self._change(k))
            btn.pack(side="left")
            self._rows[key] = val_lbl

    def _load_loadout_count(self):
        def work():
            return api.loadout_count()

        def done(n):
            self.loadout_cb.configure(values=[str(i + 1) for i in range(n)])
            self.loadout_var.set("1")
            self._refresh()

        self.app.runner.run(work, done, self.app.on_error("loadout load failed"))

    def _on_loadout_change(self, _evt=None):
        self.loadout_idx = int(self.loadout_var.get()) - 1
        self._refresh()

    def _refresh(self):
        idx = self.loadout_idx

        def work():
            return api.dump_loadout(idx)

        def done(data):
            self._rows["operator"].configure(text=data["operator"] or "-")
            for i, s in enumerate(data["slots"]):
                self._rows[f"slot{i}"].configure(text=f'{s["weapon"]}  [{s["bundle"]}]')

        self.app.runner.run(work, done, self.app.on_error("refresh failed"))

    def _set_active(self):
        idx = self.loadout_idx
        self.app.status(f"Selecting Loadout {idx+1} as active...")

        def work():
            return api.select_active_loadout(idx)

        def done(result):
            self.app.status(f"Loadout {idx+1} active: {result}")

        self.app.runner.run(work, done, self.app.on_error())

    def _change(self, key):
        if key == "operator":
            self.app.status("Loading operator list...")

            def work():
                return api.get_operators()

            def done(ops):
                self.app.status(f"{len(ops)} operators loaded")
                PickerDialog(self.app.root, "Choose Operator", ops, self._apply_operator)

            self.app.runner.run(work, done, self.app.on_error())
            return

        slot_idx = int(key.replace("slot", ""))
        hint_category = self.SLOT_CATEGORY_HINT[slot_idx]
        self._pick_category_then_item(slot_idx, hint_category)

    def _pick_category_then_item(self, slot_idx, default_category):
        win = tk.Toplevel(self.app.root)
        win.title("Choose category")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        tk.Label(win, text="Slot category (default matches this slot, but you can pick any):",
                 bg=BG, fg=FG).pack(padx=10, pady=(10, 4))
        cat_var = tk.StringVar(value=default_category)
        for cat, label in self.CATEGORY_LABELS.items():
            tk.Radiobutton(win, text=label, variable=cat_var, value=cat, bg=BG, fg=FG,
                           selectcolor="#2a2a33").pack(anchor="w", padx=16)

        def next_step():
            win.destroy()
            self._pick_bundle(slot_idx, cat_var.get())

        tk.Button(win, text="Next →", bg=ACCENT, fg="white", command=next_step).pack(pady=10)

    def _pick_bundle(self, slot_idx, category):
        bundles = api.bundles_by_category(category)
        PickerDialog(self.app.root, "Choose Weapon Family", bundles,
                     lambda bundle: self._pick_variant(slot_idx, bundle))

    def _pick_variant(self, slot_idx, bundle):
        self.app.status(f"Loading {bundle} variants...")

        def work():
            return api.find_weapon_variants(bundle)

        def done(items):
            if not items:
                messagebox.showinfo("No items found", f"No live catalog items found for {bundle}.")
                return
            PickerDialog(self.app.root, f"Choose {bundle} skin", items,
                         lambda item: self._apply_slot(slot_idx, bundle, item))

        self.app.runner.run(work, done, self.app.on_error())

    def _apply_slot(self, slot_idx, bundle, item):
        idx = self.loadout_idx
        self.app.status(f"Setting Loadout {idx+1} slot {slot_idx+1} = {item}...")

        def work():
            return api.set_slot_weapon(idx, slot_idx, item, bundle_name=bundle)

        def done(_result):
            self.app.status(f"Loadout {idx+1} slot {slot_idx+1} -> {item}")
            self._refresh()

        self.app.runner.run(work, done, self.app.on_error())

    def _apply_operator(self, operator_name):
        idx = self.loadout_idx
        self.app.status(f"Setting Loadout {idx+1} operator = {operator_name}...")

        def work():
            return api.set_operator(idx, operator_name)

        def done(_result):
            self.app.status(f"Loadout {idx+1} operator -> {operator_name}")
            self._refresh()

        self.app.runner.run(work, done, self.app.on_error())


def run_cheat_snippet(app, snippet, on_extra_done=None):
    """Used by SpeedTab: run a snippet as `pc.<...>`, echo it into
    the Console tab's log either way, no appended `return` (a saved custom
    snippet might already end with its own, and Lua only allows one at the end
    of a block)."""
    app.status(f"Running: {snippet}")

    def work():
        return api.run_raw_lua("local pc = UEHelpers.GetPlayerController()\n" + snippet)

    def done(result):
        app.status(f"Ran: {snippet}")
        if hasattr(app, "console_tab"):
            app.console_tab._log(f">>> [cheat] {snippet}", "cmd")
            app.console_tab._log(result.strip() if result.strip() else "ok", "ok")
        if on_extra_done:
            on_extra_done(result)

    def err(e):
        app.status(f"ERROR: {e}", bad=True)
        if hasattr(app, "console_tab"):
            app.console_tab._log(f">>> [cheat] {snippet}", "cmd")
            app.console_tab._log(f"ERROR: {e}", "err")

    app.runner.run(work, done, err)


class SpeedTab(ttk.Frame):
    """Game-speed controls (Slomo), split out on their own."""

    PRESETS = [0.10, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        tk.Label(self, text="Game Speed (Slomo)", bg=BG, fg=FG, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=8, pady=(8, 2))

        preset_frame = tk.Frame(self, bg=BG)
        preset_frame.pack(fill="x", padx=8, pady=4)
        for val in self.PRESETS:
            label = "Normal (1x)" if val == 1.0 else f"{val}x"
            tk.Button(preset_frame, text=label, width=12,
                      command=lambda v=val: self._set_speed(v)).pack(side="left", padx=3, pady=3)

        custom_frame = tk.Frame(self, bg=BG)
        custom_frame.pack(fill="x", padx=8, pady=(16, 4))
        tk.Label(custom_frame, text="Custom:", bg=BG, fg=FG).pack(side="left")
        self.custom_var = tk.StringVar(value="1.0")
        tk.Entry(custom_frame, textvariable=self.custom_var, width=8, bg="#2a2a33", fg=FG,
                 insertbackground=FG).pack(side="left", padx=6)
        tk.Button(custom_frame, text="Set", bg=ACCENT, fg="white",
                  command=self._set_custom_speed).pack(side="left")

        self.current_lbl = tk.Label(self, text="Current TimeDilation: unknown", bg=BG, fg="#888")
        self.current_lbl.pack(anchor="w", padx=8, pady=(16, 0))
        tk.Button(self, text="Refresh Current Speed", command=self._refresh_current).pack(
            anchor="w", padx=8, pady=(4, 0))

        self._refresh_current()

    def _set_speed(self, value):
        run_cheat_snippet(self.app, f"pc.CheatManager:Slomo({value})",
                           on_extra_done=lambda _r: self._refresh_current())

    def _set_custom_speed(self):
        try:
            value = float(self.custom_var.get())
        except ValueError:
            messagebox.showwarning("Invalid value", "Enter a number, e.g. 0.5 or 2.0")
            return
        self._set_speed(value)

    def _refresh_current(self):
        def work():
            return api.run_raw_lua(
                "local ws=(FindAllOf('WorldSettings') or {})[1]\n"
                "local td='?'; pcall(function() td=tostring(ws.TimeDilation) end)\n"
                "return td"
            )

        def done(result):
            self.current_lbl.configure(text=f"Current TimeDilation: {result.strip()}")

        self.app.runner.run(work, done, lambda e: None)


class ConsoleShellMixin:
    """Shared history + output-log behavior for ConsoleTab and ShellTab --
    both bind Alt+Up/Alt+Down history and log to a colored Text widget the
    same way, so it lives here once instead of twice. Each subclass's
    __init__ still sets up self.history=[], self.hist_idx=0 and its own
    input_box before calling _build_output_area()/_bind_history_keys()."""

    def _build_output_area(self):
        tk.Label(self, text="Output:", bg=BG, fg=FG).pack(anchor="w", padx=8, pady=(8, 0))
        out_frame = tk.Frame(self, bg=BG)
        out_frame.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        scrollbar = tk.Scrollbar(out_frame)
        scrollbar.pack(side="right", fill="y")
        self.output_box = tk.Text(out_frame, bg="#141418", fg=FG, font=("Consolas", 10),
                                   yscrollcommand=scrollbar.set, state="disabled")
        self.output_box.pack(fill="both", expand=True)
        scrollbar.config(command=self.output_box.yview)
        self.output_box.tag_configure("cmd", foreground=ACCENT)
        self.output_box.tag_configure("err", foreground=BAD)
        self.output_box.tag_configure("ok", foreground=GOOD)

    def _bind_history_keys(self):
        self.input_box.bind("<Control-Return>", self._run_from_input)
        self.input_box.bind("<Alt-Up>", self._history_prev)
        self.input_box.bind("<Alt-Down>", self._history_next)

    def _log(self, text, tag=None):
        self.output_box.configure(state="normal")
        self.output_box.insert(tk.END, text + "\n", tag or ())
        self.output_box.see(tk.END)
        self.output_box.configure(state="disabled")

    def _clear_output(self):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)
        self.output_box.configure(state="disabled")

    def _history_prev(self, _evt=None):
        if not self.history:
            return "break"
        self.hist_idx = max(0, self.hist_idx - 1)
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", self.history[self.hist_idx])
        return "break"

    def _history_next(self, _evt=None):
        if not self.history:
            return "break"
        self.hist_idx = min(len(self.history), self.hist_idx + 1)
        self.input_box.delete("1.0", tk.END)
        if self.hist_idx < len(self.history):
            self.input_box.insert("1.0", self.history[self.hist_idx])
        return "break"


class ConsoleTab(ConsoleShellMixin, ttk.Frame):
    """Raw Lua console -- same bridge everything else uses, no training wheels."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.history = []
        self.hist_idx = 0

        tk.Label(self, text="Lua (payload globals: pawn() props() funcs() count() render() valid() "
                             "UEHelpers, plus 'pc' = current PlayerController):",
                 bg=BG, fg=FG).pack(anchor="w", padx=8, pady=(8, 0))

        self.input_box = tk.Text(self, height=6, bg="#2a2a33", fg=FG, insertbackground=FG,
                                  font=("Consolas", 10))
        self.input_box.pack(fill="x", padx=8, pady=(2, 4))
        self.input_box.insert("1.0", "return 1+1")
        self._bind_history_keys()

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=8)
        tk.Button(btn_row, text="Run  (Ctrl+Enter)", bg=ACCENT, fg="white",
                  command=self._run_from_input).pack(side="left")
        tk.Button(btn_row, text="Save as Button...", bg=GOOD, fg="white",
                  command=self._save_as_button).pack(side="left", padx=6)
        tk.Button(btn_row, text="Clear Output", command=self._clear_output).pack(side="left", padx=6)
        tk.Label(btn_row, text="Alt+Up/Down: history", bg=BG, fg="#888").pack(side="right")

        self._build_output_area()

    def _run_from_input(self, _evt=None):
        code = self.input_box.get("1.0", tk.END).strip()
        if not code:
            return "break"
        self.history.append(code)
        self.hist_idx = len(self.history)
        self._log(f">>> {code}", "cmd")
        # `pc` is just a local declaration -- safe to prepend to ANY payload,
        # including one that ends with the user's own `return`.
        self._execute("local pc = UEHelpers.GetPlayerController()\n" + code)
        return "break"  # swallow the Enter keypress in Ctrl+Enter binding

    def _save_as_button(self):
        code = self.input_box.get("1.0", tk.END).strip()
        if not code:
            messagebox.showwarning("Nothing to save", "The input box is empty.")
            return

        def on_save(name, mode):
            api.save_snippet(name, code, mode)
            if hasattr(self.app, "saved_buttons_tab"):
                self.app.saved_buttons_tab.refresh()
            self.app.status(f"Saved '{name}' ({mode}) to Saved Command Buttons")

        SaveButtonDialog(self.app.root, on_save)

    def _execute(self, exec_code):
        self.app.status("Running Lua...")

        def work():
            return api.run_raw_lua(exec_code)

        def done(result):
            self._log(result if result.strip() else "(no output)", "ok")
            self.app.status("Ran Lua OK")

        def err(e):
            self._log(f"ERROR: {e}", "err")
            self.app.status("Lua error -- see console output", bad=True)

        self.app.runner.run(work, done, err)


def _make_scrollable(parent):
    """Standard scrollable-frame pattern: a Canvas + inner Frame that grows
    with its contents and scrolls with a Scrollbar or the mouse wheel (bound
    only while the cursor is over this canvas, so it doesn't hijack scrolling
    on other tabs). Returns the inner frame to pack widgets into."""
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    vsb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=BG)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    def _wheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return inner


class SavedButtonsTab(ttk.Frame):
    """Buttons saved from the Console tab's 'Save as Button...' -- a
    scrollable, self-expanding list, plus import/export so people can share a
    JSON file of their saved commands (the same widget shape a Plugin file
    uses, just without a plugin_name)."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", padx=8, pady=8)
        tk.Button(toolbar, text="Import...", command=self._import).pack(side="left")
        tk.Button(toolbar, text="Export All...", command=self._export).pack(side="left", padx=6)
        tk.Button(toolbar, text="Export Selected...", command=self._export_selected).pack(side="left", padx=6)
        tk.Label(toolbar, text="Save commands as buttons from the Console tab.",
                 bg=BG, fg="#888").pack(side="left", padx=10)

        self.selection_vars = {}
        self.inner = _make_scrollable(self)
        self.refresh()

    def refresh(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.selection_vars = {}
        widgets = api.list_snippets()
        if not widgets:
            tk.Label(self.inner, text="No saved buttons yet -- use 'Save as Button...' in the Console tab.",
                     bg=BG, fg="#888").pack(anchor="w", padx=6, pady=10)
            return
        render_command_widgets(self.inner, self.app, widgets, on_delete=self._delete,
                                selection_vars=self.selection_vars)

    def _export_selected(self):
        selected = [name for name, var in self.selection_vars.items() if var.get()]
        if not selected:
            messagebox.showinfo("Nothing selected", "Check the box next to at least one button first.")
            return
        path = filedialog.asksaveasfilename(title="Export selected buttons", defaultextension=".json",
                                             filetypes=[("JSON", "*.json")])
        if not path:
            return
        api.export_snippets(path, labels=selected)
        self.app.status(f"Exported {len(selected)} selected button(s) to {path}")

    def _delete(self, name):
        if messagebox.askyesno("Delete button", f"Delete saved button '{name}'?"):
            api.delete_snippet(name)
            self.refresh()

    def _import(self):
        path = filedialog.askopenfilename(title="Import buttons",
                                           filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            imported = api.import_snippets(path)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        existing = {w.get("label") for w in api.list_snippets()}
        added, skipped = 0, 0
        for w in imported:
            name = w.get("label", "?")
            if name in existing and not messagebox.askyesno(
                    "Overwrite?", f"'{name}' already exists. Overwrite it?"):
                skipped += 1
                continue
            api.save_snippet(name, w.get("code", ""), w.get("mode", "run_once"))
            added += 1
        self.refresh()
        self.app.status(f"Imported {added} button(s), skipped {skipped}")

    def _export(self):
        path = filedialog.asksaveasfilename(title="Export buttons", defaultextension=".json",
                                             filetypes=[("JSON", "*.json")])
        if not path:
            return
        api.export_snippets(path)
        self.app.status(f"Exported buttons to {path}")


class PluginsTab(ttk.Frame):
    """Loads a JSON 'plugin' file -- same widget shape as Saved Command
    Buttons, plus a plugin_name and optional label/separator widgets for
    layout -- as its own sub-tab. Lets someone package a themed set of
    buttons (e.g. a 'Movement' plugin with Fly + Noclip toggles) as one
    shareable file, persisted the same way families.json/snippets.json are."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._tab_to_id = {}

        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", padx=8, pady=8)
        tk.Button(toolbar, text="Add Plugin...", bg=GOOD, fg="white",
                  command=self._add_plugin).pack(side="left")
        tk.Button(toolbar, text="Remove Selected Plugin", bg=BAD, fg="white",
                  command=self._remove_selected).pack(side="left", padx=6)

        self.inner_nb = ttk.Notebook(self)
        self.inner_nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        for plugin in api.list_plugins():
            self._add_tab(plugin)

    def _add_tab(self, plugin):
        frame = tk.Frame(self.inner_nb, bg=BG)
        self.inner_nb.add(frame, text=plugin["plugin_name"])
        inner = _make_scrollable(frame)
        render_command_widgets(inner, self.app, plugin["widgets"])
        self._tab_to_id[str(frame)] = plugin["id"]
        self.inner_nb.select(frame)

    def _add_plugin(self):
        path = filedialog.askopenfilename(title="Add plugin",
                                           filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            plugin = api.add_plugin(path)
        except Exception as e:
            messagebox.showerror("Invalid plugin", str(e))
            return
        self._add_tab(plugin)
        self.app.status(f"Loaded plugin '{plugin['plugin_name']}'")

    def _remove_selected(self):
        sel = self.inner_nb.select()
        if not sel:
            messagebox.showinfo("No plugin selected", "Open a plugin tab first.")
            return
        plugin_id = self._tab_to_id.get(sel)
        name = self.inner_nb.tab(sel, "text")
        if not messagebox.askyesno("Remove plugin", f"Remove plugin '{name}'? This deletes its file."):
            return
        api.remove_plugin(plugin_id)
        self.inner_nb.forget(sel)
        self._tab_to_id.pop(sel, None)
        self.app.status(f"Removed plugin '{name}'")


class AboutTab(ttk.Frame):
    """Credits/license info -- see 1-DOCUMENTATION.md §5.6 for why this tab
    can't be made "tamper-proof" and what actually enforces attribution."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        tk.Label(self, text="Bodycam Overlay", bg=BG, fg=FG,
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=16, pady=(20, 0))
        tk.Label(self, text="A standalone desktop control panel for Bodycam -- not injected "
                             "into the game process.", bg=BG, fg="#888", wraplength=600,
                 justify="left").pack(anchor="w", padx=16, pady=(2, 16))

        tk.Label(self, text="Created by clutch5.9", bg=BG, fg=FG,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(0, 2))
        tk.Label(self, text="Licensed under the MIT License. See 0-LICENSE in the project folder.",
                 bg=BG, fg="#888").pack(anchor="w", padx=16, pady=(0, 16))

        tk.Label(self, text="Third-party components:", bg=BG, fg=FG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(0, 2))
        tk.Label(self, text="RE-UE4SS -- MIT License, Copyright (c) 2022 Narknon\n"
                             "(bundled under ue4ss_bundle/, see its own LICENSE file)",
                 bg=BG, fg="#888", justify="left").pack(anchor="w", padx=16, pady=(0, 16))

        tk.Label(self, text="Docs: 1-DOCUMENTATION.md in the project folder explains the Console/"
                             "Shell tabs, the plugin format, and troubleshooting.",
                 bg=BG, fg="#888", wraplength=600, justify="left").pack(anchor="w", padx=16)


class ShellTab(ConsoleShellMixin, ttk.Frame):
    """Runs arbitrary shell scripts locally via Git Bash -- see
    1-DOCUMENTATION.md §2 for the full safety rationale (no sandboxing, by
    design)."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.history = []
        self.hist_idx = 0

        tk.Label(self, text="Shell (Git Bash) -- runs on this PC, not in the game. "
                             "No sandboxing: same access as a terminal you opened yourself.",
                 bg=BG, fg=FG).pack(anchor="w", padx=8, pady=(8, 0))

        self.input_box = tk.Text(self, height=8, bg="#2a2a33", fg=FG, insertbackground=FG,
                                  font=("Consolas", 10))
        self.input_box.pack(fill="x", padx=8, pady=(2, 4))
        self.input_box.insert("1.0", "echo hello from bash\npwd")
        self._bind_history_keys()

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=8)
        tk.Button(btn_row, text="Run  (Ctrl+Enter)", bg=ACCENT, fg="white",
                  command=self._run_from_input).pack(side="left")
        tk.Label(btn_row, text="Timeout (s):", bg=BG, fg=FG).pack(side="left", padx=(16, 2))
        self.timeout_var = tk.StringVar(value="60")
        tk.Entry(btn_row, textvariable=self.timeout_var, width=6, bg="#2a2a33", fg=FG,
                 insertbackground=FG).pack(side="left")
        tk.Button(btn_row, text="Clear Output", command=self._clear_output).pack(side="left", padx=6)
        tk.Label(btn_row, text="Alt+Up/Down: history", bg=BG, fg="#888").pack(side="right")

        self._build_output_area()

    def _run_from_input(self, _evt=None):
        script = self.input_box.get("1.0", tk.END)
        if not script.strip():
            return "break"
        self.history.append(script)
        self.hist_idx = len(self.history)
        try:
            timeout = float(self.timeout_var.get())
        except ValueError:
            timeout = 60
        self._log(f">>> (running {len(script.splitlines())} line(s), timeout={timeout}s)", "cmd")
        self.app.status("Running shell script...")

        def work():
            return shell_client.run_shell(script, timeout=timeout)

        def done(result):
            if result["stdout"]:
                self._log(result["stdout"].rstrip(), "ok")
            if result["stderr"]:
                self._log(result["stderr"].rstrip(), "err")
            if not result["stdout"] and not result["stderr"]:
                self._log("(no output)")
            status = "TIMED OUT" if result["timed_out"] else f"exit code {result['returncode']}"
            self.app.status(f"Shell script finished: {status}")

        def err(e):
            self._log(f"ERROR: {e}", "err")
            self.app.status("Shell script failed to launch", bad=True)

        self.app.runner.run(work, done, err)
        return "break"


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Bodycam Overlay")
        self.root.geometry("760x560")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self.runner = AsyncRunner(self.root)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)
        self.host_tab = HostTab(nb, self)
        self.loadout_tab = LoadoutTab(nb, self)
        self.speed_tab = SpeedTab(nb, self)
        self.console_tab = ConsoleTab(nb, self)
        self.saved_buttons_tab = SavedButtonsTab(nb, self)
        self.plugins_tab = PluginsTab(nb, self)
        self.shell_tab = ShellTab(nb, self)
        self.about_tab = AboutTab(nb, self)
        nb.add(self.host_tab, text="Host / Create Match")
        nb.add(self.loadout_tab, text="Loadout Editor")
        nb.add(self.speed_tab, text="Game Speed")
        nb.add(self.console_tab, text="Console")
        nb.add(self.saved_buttons_tab, text="Saved Command Buttons")
        nb.add(self.plugins_tab, text="Plugins")
        nb.add(self.shell_tab, text="Shell")
        nb.add(self.about_tab, text="About")

        self.status_var = tk.StringVar(value="Starting...")
        self.status_lbl = tk.Label(self.root, textvariable=self.status_var, bg="#141418", fg=FG, anchor="w")
        self.status_lbl.pack(fill="x", side="bottom")

        self.conn_var = tk.StringVar(value="checking...")
        tk.Label(self.root, textvariable=self.conn_var, bg="#141418", fg=FG, anchor="e").pack(
            fill="x", side="bottom")

        keyboard.add_hotkey("insert", self.toggle)
        self.root.withdraw()
        self._start_tray_icon()
        self._run_setup_check()
        self.status("Press Insert to show/hide this window.")

    def _start_tray_icon(self):
        """A real taskbar/system-tray presence with a proper Exit option --
        see 1-DOCUMENTATION.md §5.6 for why the window itself only hides."""
        try:
            image = Image.open(os.path.join(api._HERE, "app_icon.ico")).convert("RGBA")
        except Exception:
            # fallback if app_icon.ico wasn't bundled -- a plain generated icon
            # beats a missing tray icon entirely
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((4, 4, 60, 60), fill=(91, 141, 238, 255))
            draw.text((20, 18), "B", fill=(255, 255, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Show/Hide (Insert)", lambda: self.toggle()),
            pystray.MenuItem("Exit", lambda: self.quit_app()),
        )
        self.tray_icon = pystray.Icon("BodycamOverlay", image, "Bodycam Overlay", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def quit_app(self):
        """Actually terminates the app (the tray's Exit item). Uses os._exit
        rather than a normal mainloop return -- see 1-DOCUMENTATION.md §5.6."""
        try:
            self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _run_setup_check(self):
        """Runs once at startup: makes sure ClaudeBridge (and, if it's already
        on this machine, the bundled UE4SS copy) is in place before the first
        connection poll. See install_bridge.py / 1-DOCUMENTATION.md §5.4."""
        def prompt_for_path():
            messagebox.showinfo(
                "Bodycam not found",
                "Couldn't auto-find your Bodycam install. Pick its Binaries\\Win64 folder next.")
            path = filedialog.askdirectory(title="Select Bodycam's Binaries\\Win64 folder")
            return path or None

        def work():
            return install_bridge.ensure_setup(prompt_for_path=prompt_for_path, on_status=self.status)

        def done(result):
            if result["reason"] == "needs_ue4ss":
                messagebox.showwarning(
                    "UE4SS not installed",
                    "UE4SS isn't installed in your Bodycam folder, and no bundled copy was "
                    "available to deploy. I've opened the official release page -- install it, "
                    "then restart this app.")
                self.status("Waiting on UE4SS install.", bad=True)
            elif result["reason"] == "not_found":
                self.status("Couldn't locate your Bodycam install.", bad=True)
            elif result["reason"] == "installed_ue4ss_and_bridge":
                messagebox.showinfo(
                    "Setup complete",
                    "Deployed UE4SS + ClaudeBridge. Fully restart Bodycam (not just Ctrl+R) "
                    "for it to load.")
                self.status("UE4SS + ClaudeBridge installed -- restart Bodycam.")
            elif result["reason"] == "installed_bridge":
                self.status("ClaudeBridge installed on top of existing UE4SS -- restart Bodycam if it's running.")
            self._poll_connection()

        def err(e):
            self.status(f"Setup check failed: {e}", bad=True)
            self._poll_connection()

        self.runner.run(work, done, err)

    def status(self, text, bad=False):
        self.status_var.set(text)
        self.status_lbl.configure(fg=BAD if bad else FG)

    def on_error(self, prefix="ERROR"):
        """Shorthand for the common AsyncRunner error handler: show the
        exception in the status bar with a prefix, e.g. as the third argument
        to self.runner.run(work, done, ...)."""
        return lambda e: self.status(f"{prefix}: {e}", bad=True)

    def _poll_connection(self):
        def work():
            return api.is_connected()

        def done(ok):
            self.conn_var.set("Bodycam: CONNECTED" if ok else "Bodycam: not responding (run install_bridge.py?)")
            self.root.after(5000, self._poll_connection)

        self.runner.run(work, done, lambda e: self.root.after(5000, self._poll_connection))

    def toggle(self):
        self.root.after(0, self._toggle_ui)

    def _toggle_ui(self):
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.withdraw()

    def hide(self):
        self.root.withdraw()

    def run(self):
        self.root.mainloop()


# Arbitrary fixed local port used purely as a single-instance lock -- binding
# it is the mutex. See 1-DOCUMENTATION.md §5.6 for what breaks without it.
_SINGLE_INSTANCE_PORT = 47821


def _acquire_single_instance_lock():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s  # keep this alive for the process lifetime -- closing it releases the lock
    except OSError:
        s.close()
        return None


if __name__ == "__main__":
    _lock_socket = _acquire_single_instance_lock()
    if _lock_socket is None:
        import tkinter.messagebox as _mb
        _root = tk.Tk()
        _root.withdraw()
        _mb.showwarning(
            "Already running",
            "Bodycam Overlay is already running (check your system tray / taskbar, "
            "or press Insert). This copy will now close instead of opening a second, "
            "conflicting instance.")
        sys.exit(0)
    App().run()
