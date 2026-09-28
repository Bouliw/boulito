<p align="center">
  <img src="app/icon/Boulito.iconset/icon_256x256.png" width="128" alt="Boulito">
</p>

<h1 align="center">Boulito</h1>

<p align="center"><b>A voice assistant for macOS that runs 100% on your Mac.</b><br>
Talk to your Mac in plain English or French: apps, YouTube in Safari, volume, music, timers, reminders, calendar, messages, dictation and quick questions.<br>
No cloud, no account, no server, no open port.</p>

<p align="center"><a href="README.fr.md">Version française</a> · <a href="docs/design.md">Design notes</a></p>

<p align="center"><a href="https://github.com/Bouliw/boulito/actions/workflows/tests.yml"><img src="https://github.com/Bouliw/boulito/actions/workflows/tests.yml/badge.svg" alt="Tests"></a></p>

---

Boulito lives in the menu bar. Hold a key (right ⌥ by default) and talk, or just say its name. Speech recognition ([Parakeet](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3)), the AI model ([Qwen 3.5](https://huggingface.co/mlx-community/Qwen3.5-9B-MLX-4bit), through Apple's [MLX](https://github.com/ml-explore/mlx)) and every action run on your Mac. Simple commands are handled by fast rules in about 0.2 s; the AI model only steps in for free-form requests (about 1 s).

## What you can say

| | Examples |
|---|---|
| **Apps** | "Open Notes", "Close Spotify", "Switch to Safari", "New tab in Safari" |
| **YouTube (Safari)** | "Search for volcano documentaries from this week", "Play the second one", "Play the one about Iceland", "Skip the ad", "Go back 30 seconds", "Next chapter", "Speed 1.5", "Captions in French", "Full screen", "Like", "Subscribe" *(asks you first)* |
| **Summaries** | "Summarize this video", "What is he talking about at 10 minutes?" |
| **Mac** | "Volume up", "Set the volume to 30%", "Mute", "Lock the Mac", "Open lemonde.fr" |
| **Writing** | "Write: buy some bread", "Open Notes then write call the bank", "Dictate" *(long dictation)*, "new line", "new paragraph" |
| **Messages** | "Send a message to Sam on Telegram saying I'll call later" (Discord, Messages, WhatsApp, Telegram) *(read back, sent only after "yes")* |
| **Time** | "Timer 10 minutes", "How much time is left?", "Remind me tomorrow at 9 am to call the bank" *(asks you first)*, "What do I have tomorrow?", "Add a meeting on Monday at 2:30 pm with Paul" *(asks you first)* |
| **Music** | "Play the album Discovery", "Play my workout playlist", "What song is this?" (your Apple Music library) |
| **Questions** | "How do you say invoice in Spanish?", "What is an ETF?", "What's 15% of 80?", "How many kilometers is 10 miles?" |

French works just as well ("Mets pause", "Cherche des vidéos sur…", "Écris : …"). The interface speaks English, French, Spanish, German, Italian or Portuguese. On first launch, Boulito uses your Mac's language and understands it, plus English; change both anytime in the Setup window (Language, Languages understood).

## Two ways to talk

- **Hold a key** (default): the microphone is only open while you hold it. Pick any key or combination in the Setup window.
- **Open listening**: say "Boulito, pause" (or the name you chose). It hears its name even while a video is playing on the speakers, like Siri: macOS echo cancellation removes the Mac's own sound from the microphone, the name is looked for continuously, and the Mac's volume goes down while you give the command ("Lower the sound while listening", which you can uncheck). **Conversation mode** keeps listening for a few seconds after a command (5 by default, `conversation_s` in `config.toml`), for simple follow-ups ("volume up", "next video", "again", "a bit more") without repeating the name.

## Privacy and safety

- **Everything stays on your Mac.** No server runs, not even on `127.0.0.1`: the AI model runs inside Boulito's own process. The network is only used for downloads: speech recognition and AI models when you click **Download** in the Setup window or pick a missing model in the **Model** menu, Python packages by `install.sh` in the source version, and, if you install the timer shortcut, by macOS to sign it. The app from the `.dmg` also asks GitHub once a day whether a new version is out (no account, nothing about you is sent; turn it off in the Setup window). `./voix check --network` loads the speech model and the AI model set in `config.toml`, then verifies that no connection goes out and no port is open.
- **Nothing is recorded.** Audio is never saved. In open listening, only the last 2 seconds stay in memory and are dropped unless they contain the name. The local log (`logs/`, in `~/Library/Application Support/Boulito` for the app) keeps the commands addressed to Boulito and what was done, for 7 days; never audio.
- **A whitelist, not a shell.** The AI can only call a fixed list of tools, each checked again before it runs. It can never run a command, touch files or change settings.
- **Web pages are untrusted.** YouTube titles and descriptions are written by strangers: they are passed to the model as delimited data it must never obey, and the tools that matter are guarded anyway.
- **You confirm what matters.** Subscribing, commenting, sending a message, creating a reminder or an event: Boulito reads it back and waits for a clear "yes". "No", silence, hesitation or any doubt cancels.
- **Your words, not the AI's.** Typed text, messages, comments and reminders are exactly what you dictated, never text written by the AI or taken from a page. Boulito never presses Enter to send, and never types into a terminal or a password field.
- **Permissions belong to Boulito.app**, not to Terminal. JavaScript is only injected into youtube.com tabs.

### Security model

Boulito.app holds powerful permissions: Accessibility (it can type and press keys in other apps), Input Monitoring, the microphone and Automation (it can control Safari, Music and Reminders). Any code that runs inside it gets them too, so only run code from this repository, or from a copy you have reviewed and trust. Safari's *Allow JavaScript from Apple Events* setting lets any app that you allowed to control Safari run JavaScript in your tabs, including the ones where you are signed in: turn it off if you stop using Boulito. The app from the `.dmg` is signed with Boulito's own certificate, not notarized by Apple (that needs a paid account): download it only from this repository's [Releases](https://github.com/Bouliw/boulito/releases) page. Its updater installs a new version only if its SHA-256 matches the one GitHub publishes and it is signed by that same certificate. In open listening, anyone near your Mac, or a video, can say the wake word: if you use it in public spaces, change the default name to one of your own (Setup window → Change…).

## Requirements

- A Mac with Apple Silicon (M1 or later) and macOS 14 or later. Developed and measured on an M4 Max with macOS 26.
- Recommended memory depends on the AI model you choose: 8 GB for Fast, 16 GB for High (the default), 24 GB for Extreme, 32 GB for Ultraboost. It is only advice: nothing is locked. About 8 GB of disk with the High model (speech models 2.3 GB, AI model 5.6 GB).
- Safari, for the YouTube features.
- From source only: [Homebrew](https://brew.sh) with `uv` (`brew install uv`), Python 3.13 (`brew install python@3.13`) and Apple's Command Line Tools (`xcode-select --install`). The app needs none of them.

`./voix check` never blocks on memory: it only advises, for the model set in `config.toml`. It fails when something is missing (Python 3.13, uv, speech models) or when disk space is short.

## Installation

### Download the app (easiest)

1. Download the `.dmg` file from the [latest release](https://github.com/Bouliw/boulito/releases/latest) (about 280 MB), open it and drag **Boulito** into **Applications**.
2. Open Boulito. The first time, macOS says it cannot check it for malicious software: Boulito is free and is not signed with a paid Apple Developer account. Click **Done**, open **System Settings → Privacy & Security**, scroll down to *"Boulito" was blocked* and click **Open Anyway** (your password or Touch ID is asked). This is needed once per version.
3. The Setup window opens: click **Download** next to **Speech recognition** (2.5 GB, once). Boulito starts listening as soon as it is installed; then pick your AI model.

The app contains its own Python: no Homebrew, no Terminal. Its settings, models and logs are kept in `~/Library/Application Support/Boulito`.

### From source

```sh
git clone https://github.com/Bouliw/boulito.git
cd boulito
./install.sh     # Python environment, speech models (~2.3 GB), and the Boulito.app menu bar app
./boulito        # start Boulito (or double-click Boulito.app)
```

Everything stays in the project folder. `./app/package.sh` builds the `.dmg` from the last commit.

### First launch

`install.sh` downloads only the speech models, and the app none: the AI model is chosen afterwards. On first launch, the Setup window guides you. **Pick your AI model** there: each one shows its size and the memory it needs, the one that suits your Mac is marked *recommended*, and a **Download** button installs it in the background (progress shown), then Boulito uses it. Simple commands already work while it downloads. The window also lists the main permissions with their live status and a button that opens the right panel of System Settings; macOS asks for Music and Reminders the first time you use them:

| Permission | Why |
|---|---|
| Microphone | To hear you (only while the key is held, or continuously in open listening) |
| Input Monitoring | To notice the talk key and Esc. Keystrokes are never read or stored |
| Accessibility *(optional)* | To type what you dictate, press standard shortcuts (⌘N, ⌘T, ⌘W, ⌘F) and media keys, send messages, lock the screen, and keep a talk key combined with a letter from typing. Never in a terminal or a password field |
| Automation → Safari | To control YouTube. Also turn on Safari → Settings → Developer → *Allow JavaScript from Apple Events* |
| Automation → Music | To play your Apple Music library and say what is playing (asked the first time) |
| Automation → Reminders | To create and read reminders, and timers of 24 hours or more (asked the first time) |
| Calendar *(optional)* | To read your calendar and add events, always after your "yes" |
| Notifications | To show what was understood and done. macOS asks on first launch; if it didn't, or if they were refused, the **Open** button in the Setup window goes straight to System Settings → Notifications → Boulito: turn on *Allow notifications* |

`install.sh` offers to create a signing certificate on your Mac (`./app/signing.sh trust`, your password is asked once): Boulito.app then keeps its permissions when it is rebuilt.

**Timers in the Clock app** *(optional)*: macOS only lets Shortcuts start them. In the Setup window, **Install** signs the two timer shortcuts shipped in `shortcuts/` on your Mac and opens them; click *Add Shortcut*. Without them, Boulito keeps its own timers.

## AI models

Download and switch models from the Setup window (or the **Model** menu), with one click and no command line. Recommended RAM is only a hint: nothing is locked.

| Level | Model | Disk | Recommended RAM | Median time |
|---|---|---|---|---|
| Fast | Qwen 3.5 4B (4-bit) | 2.9 GB | 8 GB | 0.6 s |
| **High** (default) | Qwen 3.5 9B (4-bit) | 5.6 GB | 16 GB | 1.1 s |
| Extreme | Qwen 3.5 35B-A3B, pruned (REAP 19B) | 11.5 GB | 24 GB | 0.7 s |
| Ultraboost | Qwen 3.5 35B-A3B | 20.4 GB | 32 GB | 0.5 s |

Measured on an M4 Max with `./voix test`, which replays the test set without a microphone or Safari. The High model got 99 to 100% of the end-to-end tests right.

## Settings

The menu holds what you change often (model, listening mode, talk key, languages, lowering the sound while listening, conversation mode). The Setup window (**Setup…**) holds the rest: permissions, AI models, name and wake word, open listening options, spoken tips, beeps, notifications, menu bar icon, open at login, the timer shortcut. Everything is saved in `config.toml`, created from [`config.example.toml`](config.example.toml) on first launch.

## Command line

| Command | What it does |
|---|---|
| `./boulito` / `./boulito stop` | Start or stop Boulito.app |
| `./boulito diag` | Shows the permissions Boulito.app has, and what it sees of the app in front |
| `./boulito diag --notification` | Sends a test notification from Boulito.app |
| `./voix check` | Checks the Mac, the models, the permissions of the app that runs it, and that every cache stays in the project folder; memory is only advice |
| `./voix check --network` | Same, plus 15 s of network monitoring with the speech model and the configured AI model loaded |
| `./voix stats` | Success rate and latency of your voice commands, from the local log (last 24 h; `--hours 168` for 7 days) |
| `./voix run "play the second one"` | Runs a written sentence as if it had been spoken (from Terminal: macOS then asks Terminal for its own permissions, e.g. to control Safari) |
| `./voix route "volume up"` | Shows what a sentence would do, without doing it |
| `./voix route --test` | Replays the rule tests: sentences, durations, dates, confirmations, dictation, calculations |
| `./voix test` | Replays the end-to-end tests with the AI model, on simulated YouTube pages |
| `./voix download --model 35b` | Downloads another AI model |

`voix` (French for *voice*) is the project's internal name.

## Updating

**App**: when a new version is out, Boulito shows a notification. Click **Update** in the menu or in the Setup window: it downloads the new version, checks it, replaces itself and restarts. Settings, models and permissions are kept. You can also download the new `.dmg` and replace Boulito in Applications by hand. (Coming from 0.2.0: download 0.2.1 by hand once, and grant the permissions again.)

**From source**:

```sh
git pull && ./install.sh
```

`install.sh` updates the Python environment, keeps the models already downloaded and rebuilds Boulito.app. With the local signing certificate (`./app/signing.sh trust`), Boulito.app keeps its permissions; without it, macOS may ask for them again.

Keep the project folder where it is: moving it breaks the Python environment (`.venv`), the login item and the permissions granted to Boulito.app. If you move it anyway: delete `.venv`, run `./install.sh` again, turn *Open at login* off and on in the Setup window, and grant the permissions again.

## Troubleshooting

- **YouTube does nothing, or an error mentions JavaScript**: in Safari, turn on Settings → Advanced → *Show features for web developers*, then Settings → Developer → *Allow JavaScript from Apple Events*.
- **A feature does not work (typing, talk key, calendar…)**: open **Setup…** from the menu: each permission shows ✅ or ❌ (⚪️ when macOS cannot tell, like Safari's setting), with a button that opens the right panel of System Settings. After allowing Input Monitoring, restart Boulito.
- **No notifications**: they were probably refused. In the Setup window, the **Open** button next to Notifications goes to System Settings → Notifications → Boulito: turn on *Allow notifications*. `./boulito diag --notification` sends a test one.
- **Something else**: Boulito's output is in `logs/YYYY-MM-DD.app.log`, kept 7 days (in `~/Library/Application Support/Boulito` for the app, in the project folder from source), and `./voix check` lists what is missing.

## Uninstall

**App**: in the Setup window, **Erase everything…** deletes settings, models and logs, turns off *Open at login* and quits Boulito. Then drag Boulito from Applications to the Trash. By hand, if you want: remove its permissions in System Settings → Privacy & Security, turn off Safari's *Allow JavaScript from Apple Events*, delete the timer shortcuts in Shortcuts and the "Boulito queue" playlist in Music.

**From source**:

1. `./uninstall.sh`: stops Boulito, removes the login item and the trust of the local signing certificate if any, and lists what to undo by hand (permissions, the Safari setting, the timer shortcuts, the "Boulito queue" playlist).
2. Move the project folder to the Trash. Models, caches, logs and settings all live inside it.

## How it works

Key or wake word → microphone (16 kHz, echo cancellation in open listening) → Silero VAD → Parakeet → **rules** (regular expressions, French and English, under 3 ms) → otherwise **Qwen 3.5** with tool calling and a text snapshot of the YouTube page → **executor** (whitelist, checks, confirmations) → Safari (JavaScript in the YouTube tab), `open -a`, AppleScript or Shortcuts → sound, notification and spoken answer.

Design choices, measurements and limits are detailed in [docs/design.md](docs/design.md). All YouTube selectors live at the top of [`src/voix/youtube.js`](src/voix/youtube.js), to be fixed quickly when YouTube changes its interface; all interface texts live in [`src/voix/i18n.py`](src/voix/i18n.py), one column per language.

## Limits

- YouTube control relies on YouTube's web interface: a redesign can break an action until its selector is updated.
- Only Safari is supported for YouTube, and only your Apple Music library (not the whole catalog).
- Boulito does not click through other apps' interfaces (too risky: Send, Delete or Pay buttons are one click away); it uses targeted recipes (messaging apps) and standard shortcuts.
- The AI model works offline: it cannot know the news, the weather or prices, and its knowledge stops at its training date.

## License

[GPL-3.0](LICENSE). Copyright © 2026 Bouliw.

Boulito is an independent project, not affiliated with Apple, Google or YouTube. The models keep their own licenses (Qwen 3.5: Apache 2.0; Parakeet: CC BY 4.0; Silero VAD: MIT).
