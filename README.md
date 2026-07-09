<p align="center">
  <img src="assets/logo.png" width="320" alt="Hyperion Account Manager" />
</p>

<h1 align="center">Hyperion Account Manager</h1>

<p align="center">
  A modern, local-first manager for multiple Roblox accounts — add accounts, launch them into games,
  and keep them running, all from one encrypted vault with a clean web-based UI.
</p>

<p align="center">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-blue.svg" />
  <img alt="python" src="https://img.shields.io/badge/python-3.12-blue.svg" />
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-lightgrey.svg" />
</p>

---

> **⚠️ Disclaimer.** This tool manages **your own** Roblox accounts. The optional
> multi-instance feature holds Roblox's singleton lock to run several clients at
> once, which interacts with Roblox's anti-cheat and may carry ban risk — it is
> **off by default**. Use at your own risk. Not affiliated with Roblox Corporation.

## Features

- 🎯 **Join by Username + Auto-Detect** — type a username, hit **Auto-Detect** to find the exact
  game & server they're in right now, then send your bots straight in (the primary, front-and-center flow).
- 🔐 **Encrypted vault** — accounts are sealed with **AES‑256‑GCM** and a key derived
  from your master password via **Argon2id**. The password is never stored.
- ➕ **Add accounts** by `.ROBLOSECURITY` **cookie** or by **username + password**
  (opens a real Roblox login so you can clear any captcha/2FA; the session is captured automatically).
- 🚀 **Launch into games** by Place ID, a specific Job ID, by following a user, or via a VIP/private‑server link.
- 🧵 **Sequential launch queue** — bots join one-by-one with a configurable delay; if a specific server
  is full it **waits and retries that exact server** instead of hopping to a different one.
- 🏠 **Launch to Home** — open an account to the Roblox home screen without joining a game.
- 🌐 **Open in Chrome** — open any account in its own isolated, auto-logged-in Chromium profile.
- 🟢 **Live status** — presence (Online / In‑Game / In‑Studio), avatars, and cookie validity, with full
  display names and usernames.
- 🪟 **Multi-instance** — run multiple Roblox clients at once (engaged automatically on launch).
- ⚙️ **Optimizer** — keep your MAIN account at full power while ALT clients get minimized,
  CPU-core-pinned, and RAM-trimmed (configurable RAM limit / trim interval / warm-up / cores).
- ⛔ **Kill Roblox** — one button to end every running Roblox client on the PC.
- 👀 **Watcher** — auto-relaunch "📌 keep online" accounts that get kicked or disconnect.
- ⚡ **FPS unlocker**, **saved places** (Place/Job/username presets), and **encrypted backup / restore**.
- 🔌 **Local control API** *(off by default)* — drive the app from local scripts with an API key.
- 📥 **Import** your accounts from the classic Roblox Account Manager (`AccountData.json`).

## Quick start (from source)

> Requires **Windows** and **Python 3.12**.

```powershell
git clone https://github.com/HumanoidRootPartX/Hyperion-Roblox-Account-Manager.git
cd Hyperion-Roblox-Account-Manager

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium   # for username/password login

.\.venv\Scripts\python.exe main.py
```

Or just run **`start.bat`** (it sets everything up on first run).

On first launch you set a **master password** that encrypts your vault. To develop in a
normal browser instead of the desktop window: `python main.py --no-window` → http://127.0.0.1:8777/app/

## Build a standalone .exe

```powershell
.\build.bat
```

Produces a single **`dist\Hyperion Account Manager.exe`** you can run by double-clicking —
no Python install needed. (The Chromium browser used for username/password login stays
external; `playwright install chromium` fetches it once.)

## How it works

```
hyperion_am/
  core/      # vault (crypto), Roblox API, launcher, watcher, multi-instance, FPS, migration
  server/    # FastAPI REST + WebSocket event bus + static UI mount
  web/       # vanilla HTML/CSS/JS front-end, state-driven off /ws
main.py      # pywebview desktop shell (uvicorn on a background thread)
```

- **Your data never lives in this repo.** The vault and settings are written to
  `%APPDATA%\HyperionAccountManager\` (`vault.dat`, `config.json`) — both are git-ignored.
- Only the **question text** of a username search ever leaves your machine; cookies stay local and encrypted.

## Local control API

Off by default. Enable it in **Settings**, then call it with the generated key:

```bash
curl -H "X-API-Key: <key>" http://127.0.0.1:8777/external/accounts
curl -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
     -d "{\"account\":\"myuser\",\"place_id\":5315046213}" \
     http://127.0.0.1:8777/external/launch
```

Each action (list / launch / read-cookie) has its own permission toggle; reading cookies is off by default.

## Testing

```powershell
.\.venv\Scripts\python.exe scripts\test_vault.py
.\.venv\Scripts\python.exe scripts\test_engine.py
```

## License

[MIT](LICENSE) © HumanoidRootPart

## Credits

Architecture and Roblox integration referenced from
[Roblox-Account-Manager](https://github.com/ic3w0lf22/Roblox-Account-Manager) by ic3w0lf22
and the Rust-based [robloxmanager](https://gitlab.com/centerepic/robloxmanager).
