<h1 align="center">
  <img src="extras/livearea/pic0.png" alt="PES 2012 PS Vita Banner" width="700"><br>
  PES 2012 · PS Vita Port
</h1>

<p align="center">
  <b>Native port of Pro Evolution Soccer 2012 (iOS, v1.0.2) for PlayStation Vita and PlayStation TV.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-PS%20Vita%20%7C%20PS%20TV-003791.svg?style=flat-square&logo=playstation" alt="Platform" />
  <img src="https://img.shields.io/badge/Origin-iOS-000000.svg?style=flat-square&logo=apple" alt="Origin" />
  <img src="https://img.shields.io/badge/Title%20ID-PES201201-ff69b4.svg?style=flat-square" alt="Title ID" />
  <img src="https://img.shields.io/badge/Engine-Marmalade-brightgreen.svg?style=flat-square" alt="Engine" />
  <img src="https://img.shields.io/badge/Renderer-vitaGL%20%28GLES%201.1%29-orange.svg?style=flat-square" alt="Renderer" />
  <img src="https://img.shields.io/badge/Status-In--game-yellow.svg?style=flat-square" alt="Status" />
</p>

<p align="center">
  <a href="#-description">Description</a> •
  <a href="#-features">Features</a> •
  <a href="#-controls">Controls</a> •
  <a href="#-installation">Installation</a> •
  <a href="#-known-issues">Known Issues</a> •
  <a href="#-building-from-source">Build</a> •
  <a href="#-credits">Credits</a>
</p>

---

## 📖 Description

An unofficial, native **iOS-to-PS Vita wrapper port** of *Pro Evolution Soccer 2012* (iOS version 1.0.2, originally released by **Konami**). The game is a 32-bit armv6 Mach-O executable with the Marmalade runtime linked statically and loose asset files. This port ships its own **Mach-O loader** and **Objective-C runtime**: it maps the executable into PS Vita RAM at `MACHO_SLIDE = 0x98000000` and replaces the Apple frameworks it uses with native implementations on top of **vitaGL** and the Vita system libraries. Marmalade's `s3e` threads are run on the Vita's pthreads.

The game is forced to its OpenGL ES 1.1 path (IwGx ES1, with CPU skinning). The iPhone build targets ES2, which this port does not use yet.

This port is independent of the Android port (`PES201200`); the two Vita titles are different and can be installed side by side.

You need your own legally obtained IPA. No game files are included in this repository.

---

## 🎮 Status & Playability

**Status: In-game (v0.1.0)**

The game reaches the title screen, the menus and an in-game exhibition match on physical PS Vita hardware. Match play runs at about 30 FPS; the menus still drop frames. Menu music is not confirmed yet. See [`port_progress.md`](port_progress.md) for the console run log.

- [x] **Title screen**: first screen reached on console (run #091, confirmed on the console by the maintainer).
- [x] **Mach-O loader & runtime**: rebase, binds and the Marmalade `s3e` layer run on the console (see `port_progress.md`).
- [x] **Match peaks**: the spikes in the match were fixed; the cause was 5 falsely rebased words in the `IwGeom` sine table (bone SLERP ×7), now skipped via `tools/rebase_skip.txt`. v0.1.0 match play at a sustained 30 FPS.
- [x] **Controls**: virtual joystick, Cross, Circle, Square and Start mapped through `controls.txt`; confirmed OK on console.
- [ ] **Menu frame rate**: the selection menus still drop frames; the remaining spikes are in IwGx's ES1 / software path.
- [ ] **Music**: `AudioFileOpenWithCallbacks` and the `AudioQueueNewOutput` format were unimplemented in the first console runs; current state not recorded.
- [ ] **Options menu (SELECT + START)**: new, not yet tested on console.

---

## ✨ Features

| Feature | Status | Notes |
| :--- | :---: | :--- |
| Boots to gameplay | ✅ | Title screen, menus and an in-game exhibition match reached on hardware |
| Graphics / renderer | 🟡 | vitaGL GLES 1.1 (ES1 forced); match ~30 FPS, menus drop frames |
| Audio & music | ❔ <!-- TODO: verify --> | Menu music was muted in the first runs; current state not recorded |
| Video / cutscenes | ❔ <!-- TODO: verify --> | Not documented in this repo |
| Physical buttons | ✅ | Mapped to on-screen touches through `controls.txt` (see [Controls](#-controls)) |
| Analog sticks / camera | ✅ | Left stick drives a virtual joystick; confirmed OK on console |
| Touch screen | ✅ | Virtual touch buttons drive the game; hiding them by default is planned for the next build |
| Button remapping / port menu | 🧪 | SELECT + START options menu (screen, filter, MSAA, rotation, FPS) not yet tested on console |
| Save data | ❔ <!-- TODO: verify --> | Not documented in this repo |
| Native trophies | ❌ | Not implemented |
| Languages | ❔ <!-- TODO: verify --> | Not documented in this repo |
| Performance | 🟡 | About 30 FPS sustained in an exhibition match; menu frame drops |
| Online / IAP / ads | ❔ <!-- TODO: verify --> | Not documented in this repo |
| PS TV | 🧪 | Same target as Vita per the description; not separately verified |

Legend: `✅ Working (verified on console) · 🧪 Implemented, not yet verified on console · 🟡 Partial · ❌ Not working · ➖ Not applicable · ❔ Not documented yet`

---

## 🎯 Controls

The physical buttons press on-screen touch points defined in `ux0:data/pes2012ios/controls.txt`. The file is edited over FTP, without rebuilding. Coordinates are in the game's screen space.

| Vita input | Action |
| :---: | :--- |
| **Left Stick** | Virtual joystick (drag around the stick's point in `controls.txt`); dead zone 0.35 |
| **Cross (✖)** | A |
| **Circle (●) / Square (◼)** | B |
| **Start** | Pause |
| **Select + Start** | Options menu (see [Options](#options)) |

> [!TIP]
> The `controls.txt` format is described in [`tools/controls.example.txt`](tools/controls.example.txt) (at most 6 lines).

---

## 📋 Prerequisites

To run this port on your PS Vita or PS TV, you will need:

1. A PS Vita / PS TV console running Custom Firmware (**Enso 3.60 / 3.65** recommended) with `kubridge` under `*KERNEL` in `ur0:tai/config.txt`.
2. [**libshacccg.suprx**](https://github.com/Rinnegatamante/ShaRKBR33D/releases/latest) installed in `ur0:data/` (extract it with **ShaRKBR33D**).
3. A legally obtained, decrypted copy of **PES 2012 v1.0.2** for iOS (`PES2012_v1.0.2_os30.ipa`, `cryptid 0`). No game files are included in this repository.

---

## 📦 Installation

1. Install `pes2012ios.vpk` on your console using **VitaShell**.
2. Stage the game data on your PC with `tools/stage_data.sh`, then upload it with `python3 -I tools/deploy_data.py` (about 640 MB, to `ux0:data/pes2012ios/`).
3. Launch the game from the LiveArea.

### Options

Once the game is running, press **SELECT + START** to open the options menu. The game pauses behind the menu. D-pad ↑/↓ moves between rows, ←/→ or ✕ changes a value, ○ saves and closes. Settings are saved to `ux0:data/pes2012ios/options.txt`.

| Option | Values | Notes |
| :--- | :--- | :--- |
| **Screen size** | original aspect · full screen | |
| **Screen filter** | bilinear · sharp bilinear | |
| **Anti-aliasing (MSAA)** | off · 2x · 4x | Applies after restarting the game. |
| **Rotation** | none · landscape right · landscape left | |
| **FPS counter / limit** | off · counter · limit 30 · counter + limit 30 | New, *not yet tested on console*. Counter in the top-left corner; the limit caps the game at 30 FPS. Saved as `show_fps=` / `fps_cap=`. |

---

## 🐛 Known Issues

- **Menu frame rate**: the selection menus drop frames (the match is at about 30 FPS). The remaining spikes are in IwGx's ES1 / software skinning path. The iPhone build targets ES2, and a GLES2 path (GLSL shaders via vitaGL) is the next proposed step.
- **Music**: the first console runs had the menu music muted (`AudioFileOpenWithCallbacks` unimplemented, `AudioQueueNewOutput` format); current state not recorded.
- **Virtual touch controls** are visible on screen in v0.1.0; hiding them by default is planned for the next build.
- **Info.plist**: the IPA's `Info.plist` is XML and the loader reads only the binary plist format (no effect for now).
- **Crash dumps and logs**: the system writes `.psp2dmp` crash dumps to `ux0:data/`; the game writes its logs to `ux0:data/pes2012ios/logs/`. Pull them with `python3 -I tools/deploy_data.py --logs`.
- **Crash triage**: a program counter minus `0x98000000` is the address in the Mach-O (`otool -tV`, with `_s3e*` symbols). A data address below `0x02000000` means a missing rebase (see `report.txt` in the staged data). Stop reason `0x10006` means the stack pointer is outside the thread stack (s3e fibres, see `PORTING_PLAN.md`).

---

## 🔨 Building from Source

### Prerequisites

- [VitaSDK](https://vitasdk.org/) (`$VITASDK`) with `kubridge` and `SceShaccCg`, and the softfp toolchain.
- CMake 3.14+ and Make; Python 3 (for the tools).
- The decrypted IPA, kept locally (never committed).
- vitaGL is vendored in `vendor/vitaGL` and built automatically with `SOFTFP_ABI=1 HAVE_SHADER_CACHE=1` (see `CMakeLists.txt`).

### Compilation Commands

```bash
# Stage the console data (copies the .app and generates rebase.bin / binds.bin / report.txt)
tools/stage_data.sh

# Configure and build the VPK (build/pes2012ios.vpk)
cmake -S . -B build
cmake --build build
```

### Deploy & Debug

```bash
python3 -I tools/deploy_data.py          # upload ux0_data/pes2012ios/ (~640 MB)
python3 -I tools/deploy_data.py --logs   # fetch the logs and crash dumps
```

Architecture and progress notes: [`PORTING_PLAN.md`](PORTING_PLAN.md) and [`port_progress.md`](port_progress.md).

## 📁 Project Structure

- `source/`: loader and runtime (Mach-O loader, ObjC runtime, Marmalade `s3e` layer, IwGx ES1 path, options menu, `port_config.h`).
- `tools/`: `stage_data.sh`, `ios_prep.py` (Mach-O analysis → `rebase.bin` / `binds.bin`), `deploy_data.py`, `macho_prep.py`, `annotate.py`, `controls.example.txt`, and the rebase rule lists (`code_roots.txt`, `rebase_force.txt`, `rebase_skip.txt`).
- `vendor/`: vitaGL built from source.
- `extras/livearea/`: LiveArea assets (`icon0.png`, `bg0.png`, `pic0.png`, `startup.png`, `template.xml`).
- `PORTING_PLAN.md`: architecture and porting plan. `port_progress.md`: console run log and bug-by-bug progress.

---

## 📜 Disclaimer

**Pro Evolution Soccer 2012** is a trademark of **Konami**. The work presented in this repository is unofficial and is not produced, endorsed, or sanctioned by Konami or any affiliated entities.

This software does NOT contain original game code, binaries, copyrighted graphics, or game assets. The authors do not promote or condone piracy in any way. To play, you must provide your own legally obtained, **decrypted** copy of the iOS `.ipa`.

---

## ☕ Support My Work

This project is **free** and will stay free. If you enjoy it, a tip from **$1** helps me keep porting games to the PS Vita:

<p align="center">
  <a href="https://ko-fi.com/metalsyntax"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi"></a>
</p>

- **Ko-fi:** [ko-fi.com/metalsyntax](https://ko-fi.com/metalsyntax)
- **PayPal:** [paypal.me/MetalSyntax](https://paypal.me/MetalSyntax)
- **Bitcoin (BTC):** `133HdCsaWxdjTfmwpgGVykByhNBV338uRc`

**Ko-fi supporters get:**
- **Early access** to new ports before their public release (test builds of upcoming projects).
- **Data preparation scripts and step-by-step guides** to set up your own game files for each new port.
- A say in which games I port next.

> Game files are never distributed. You always need your own legally obtained copy of the game.

---

## 👥 Credits

- **Konami**: original developers of Pro Evolution Soccer 2012.
- **Marmalade** (`s3e`) runtime, used by the original game.
- **vitaGL** graphics library by [Rinnegatamante](https://github.com/Rinnegatamante/vitaGL).
- **kubridge** plugin by [Bythos](https://github.com/bythos14/kubridge).
- **ShaRKBR33D** / `libshacccg` by [Rinnegatamante](https://github.com/Rinnegatamante/ShaRKBR33D).
- Special thanks to the PS Vita homebrew and porting community.

---

## 📄 License

No `LICENSE` file is present in this repository. <!-- TODO: add a LICENSE file and state the loader license. -->
