# Troubleshooting: Buttons / Tools Not Working

If the overlay's buttons or tools stop responding, it's usually caused by stale or corrupted files from a previous session. Follow these steps in order.

## 1. Check the Console First

Before doing anything else, open the console and look for error messages. This will often tell you exactly what's broken and can save you from needing to do a full reset.

- If you see clear errors referencing missing files, mods, or scripts, note them down.
- If the console is empty or the errors aren't helpful, continue to the steps below.

## 2. Exit the Overlay

1. Look in your system tray (bottom-right of your taskbar, near the clock).
2. Right-click the overlay icon.
3. Select **Exit**.

## 3. Close the Game

Fully close Bodycam. Make sure it's not still running in the background (check Task Manager if you're unsure).

## 4. Delete the Following Folders

> **Important:** Delete the entire folder, not just the files inside it.

- `C:\Users\<YourUsername>\AppData\Local\Temp\bodycam_bridge`
- `C:\Program Files (x86)\Steam\steamapps\common\Bodycam\Bodycam\Binaries\Win64\ue4ss`

Replace `<YourUsername>` with your actual Windows username.

## 5. Relaunch the Game

Start Bodycam again. Both folders will be regenerated automatically with fresh files, which resolves most issues caused by corrupted or outdated data.

## 6. Still Not Working?

Open the console again and check for errors. If the same error persists after a full folder reset, that's a sign the issue isn't just stale files — please report the console output when opening an issue on this repo.

---

### Quick Reference

| Step | Action |
|------|--------|
| 1 | Check console for errors |
| 2 | Exit overlay from system tray |
| 3 | Close the game |
| 4 | Delete `bodycam_bridge` folder AND `ue4ss` folder |
| 5 | Relaunch the game |
| 6 | Re-check console if issue persists |
