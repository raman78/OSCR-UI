# LiveParser always-on-top on Wayland

## Purpose

The `LiveParserWindow` (`OSCRUI/liveparser.py`) must float above a
full-screen Star Trek Online window. On Wayland it currently cannot: the
Qt flags it sets are ignored by the compositor. This note records how the
sibling project **STO_CombatLogAnalyzer** (a Rust STO parser, referred to
here as CLA) solved the identical problem, so the same fix can be ported
to this PySide6 overlay. It began as a design/reference note; the approach has
since been implemented on the `liveparser` branch — see
[Implementation status](#implementation-status).

## Implementation status

Implemented and verified on branch `liveparser` (KDE Plasma Wayland / KWin).

- **Separate process.** Selecting the layer-shell shell integration is
  process-global (it turns *every* window into a layer surface), so the overlay
  runs on its own: `main.py --live-overlay` → `OSCRUI(overlay_mode=True)` builds
  only the live parser. `OSCRUI.toggle_live_overlay` spawns/kills it via
  `QProcess`, gated by `wayland_overlay.layershell_supported()`. Non-Wayland
  keeps the previous in-process path unchanged.
- **ctypes, no binding.** There is no PySide6 binding for `LayerShellQt`, so
  `OSCRUI/wayland_overlay.py` calls `libLayerShellQtInterface.so.6` through
  ctypes. This works only because the system Qt matches PySide6's bundled Qt
  (6.11.1); `layershell_supported()` checks major.minor and the plugin file,
  and falls back to the plain toplevel otherwise. `QT_PLUGIN_PATH` is pointed at
  the system plugin dir and `QT_WAYLAND_SHELL_INTEGRATION=layer-shell` set before
  the QApplication.
- **Invariants.** I1 satisfied (`keyboard-interactivity=none`, layer `overlay`).
  I3 satisfied — the surface is moved by updating anchor margins and resized by
  resizing the widget directly (`live_parser_overlay_resize_*`, since layer
  surfaces support neither `startSystemMove` nor `startSystemResize`). Position
  and size are saved debounced (~2.5 s after the last change) to
  `liveparser__overlay_{left,top,width,height}`. Those keys are owned by the
  overlay process: it writes only that subset (`store_settings_subset`) and the
  main process refreshes them (`reload_settings`) before its own full save, so
  neither clobbers the other.
- **Dragging uses raw relative-pointer motion.** A margin drag driven by
  `event.position()` fails: when we move the surface, Qt's surface-local
  coordinate frame shifts under the pointer (inconsistently — sometimes it
  re-references our move, sometimes not), so the window drifts, jitters, or
  flings to a corner. No `event.position()` formula is stable. The fix is the
  same one CLA's overlay reached: derive motion from `zwp_relative_pointer_v1`
  raw deltas, which our own moves never perturb. Qt does not expose it, so
  `wayland_overlay.create_relative_pointer` binds it via **pywayland** onto the
  `wl_display` that `QNativeInterface::QWaylandApplication` hands us as a raw
  pointer; events are delivered by Qt's normal dispatch. pywayland has no wheels
  and builds from source, so it is an optional extra (`OSCR-UI[wayland]`);
  without it the overlay shows but is not draggable. `overlay_relative_motion`
  accumulates the deltas sub-pixel and applies whole-pixel margin steps while
  `live_parser_overlay_press`/`_release` gate a drag.
- **I2 intentionally not applied.** Qt only exposes *whole-window* click-through
  (`Qt.WindowTransparentForInput` → empty `wl_surface.set_input_region`); a
  partial input region (interactive buttons, click-through elsewhere) is not
  available in the public API. Since this overlay is interactive (buttons +
  drag) and takes no keyboard focus, full click-through would make it unusable,
  so it stays interactive and captures clicks only within its own rectangle.

## The problem

`LiveParserWindow.build_window()` sets, at `OSCRUI/liveparser.py:74`:

```python
# OSCRUI/liveparser.py
self.setWindowFlags(
    self.windowFlags()
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.WindowDoesNotAcceptFocus
    | Qt.WindowType.FramelessWindowHint)
```

On **X11** `WindowStaysOnTopHint` works. On **Wayland** it does not: the
Wayland protocol gives a regular application window (an `xdg_toplevel`) no
control over stacking — the compositor decides z-order, and the
always-on-top request is advisory. KWin, Mutter and wlroots all ignore it
for normal toplevels, so the overlay disappears behind a full-screen game.
This is a protocol-level fact, not a Qt bug; the same limitation hit CLA's
overlay (there via `winit`'s always-on-top hint).

## Root cause and the fix

There is one Wayland mechanism a compositor *does* honor for staying on
top: the **`wlr-layer-shell`** protocol (`zwlr_layer_shell_v1`). It lets a
client create a *layer surface* pinned to one of four layers —
`background`, `bottom`, `top`, `overlay` — outside the normal window
stack. The `overlay` layer renders above full-screen windows. KWin
(Plasma), Mutter (via its own path) and all wlroots compositors implement
it.

The fix is therefore: **on Wayland, make the overlay a layer-shell surface
on the `overlay` layer instead of a normal always-on-top toplevel.** Keep
the existing X11 toplevel path unchanged — `liveparser.py:79` already
branches on `QApplication.platformName() == 'wayland'`, so the switch is
localized.

CLA's implementation lives in
`/home/raman/Shared/RustroverProjects/STO_CombatLogAnalyzer/src/app/layer_overlay.rs`
with an architecture write-up in that repo's `docs/OVERLAY.md`. It renders
the overlay onto a layer surface via `smithay-client-toolkit`. The Qt port
does not need a separate renderer — see [Qt path](#qt-path-layershellqt) —
but the **invariants below are protocol-level and transfer verbatim**.

## Invariants (learned the hard way in CLA)

These are the non-obvious properties that made the overlay actually usable.
Each cost real debugging in CLA; treat them as requirements, not options.

- **I1 — Do not take keyboard focus.** Set the layer surface's
  `keyboard-interactivity = none`. A layer surface that grabs keyboard
  focus deactivates the game. This matters beyond feel: on this setup the
  player runs a separate key-spam helper that only injects while the game
  is the *active* window, and any focus theft silently disables it. The Qt
  intent equivalent is the existing `WindowDoesNotAcceptFocus`, but on
  layer-shell it is the surface's `keyboard-interactivity` that governs it.

- **I2 — Click-through by default via the input region.** A layer surface
  with the default (whole-surface) input region swallows clicks meant for
  the game underneath. Set an **empty input region** so pointer events fall
  through to the game; give the surface a non-empty region only while the
  user is actively repositioning it (or over a small drag handle). In Qt
  the analogue is `QWidget.setMask()` / an input region on the window.

- **I3 — A layer surface cannot be dragged like a toplevel.** There is no
  client-initiated interactive move. Reposition by setting the surface's
  **anchor + margins** (e.g. anchor `top | left`, then adjust the top/left
  margin as the pointer drags). CLA tracks a `(top, left)` margin and
  updates it on pointer motion; the grab point stays put because the
  margins re-reference against the moved surface. `liveparser.py` already
  has a Wayland-specific move handler stub (`live_parser_move_wayland` at
  `liveparser.py:80`) — that is where margin-based movement goes.

- **I4 — Active-window detection via X11 is unreliable here.** With
  **Proton 11** the game runs as a **native Wayland window**, not XWayland.
  Anything that reads `_NET_ACTIVE_WINDOW` / `xprop` to check "is the game
  focused" returns a nameless placeholder and fails. If OSCR-UI ever gates
  behavior on the game being focused, use a Wayland-aware source (KWin
  scripting over D-Bus, or `layer-shell`'s own knowledge that it never
  holds focus) rather than X11. Same buildid on Windows writes a single
  file / behaves differently only because Windows is not Wayland — this is
  a Wine/Proton driver effect, not a version difference.

- **I5 — Two GPU contexts in one process can crash on teardown
  (renderer-specific).** CLA's overlay ran its own `wgpu`/Vulkan instance
  alongside the main window's; destroying the overlay's instance unloaded
  the Vulkan library out from under the main renderer, segfaulting it in
  `wait_for_fence`. The fix was to create the GPU instance once and share
  it across show/hide. Qt/`QRhi` shares one rendering context, so this is
  unlikely to bite a PySide6 port — but if the layer surface ends up with
  its own GL/Vulkan context, watch teardown order.

## Qt path (LayerShellQt)

The Wayland/Qt integration for `wlr-layer-shell` is **LayerShellQt**
(upstream package name `layer-shell-qt`, a KDE library). It ships a
QtWayland *shell integration plugin* plus a C++ API (`LayerShellQt::Window`)
to configure a `QWindow` as a layer surface: `setLayer(LayerOverlay)`,
`setAnchors(...)`, `setMargins(...)`, `setKeyboardInteractivity(...)`,
`setExclusiveZone(-1)`.

Activation has two parts:

| Part | Mechanism |
|---|---|
| Load the plugin | env `QT_WAYLAND_SHELL_INTEGRATION=layer-shell` **before** the `QGuiApplication`/`QApplication` is constructed |
| Configure the window | `LayerShellQt::Window::get(qwindow)` then set layer / anchors / margins / keyboard-interactivity (maps to I1–I3) |

Set the env var early — it must be in place before Qt initializes the
Wayland platform. Doing it in `OSCRUI/__main__` (or wherever `QApplication`
is created), guarded by a Wayland + STO-on-Wayland check, keeps the X11
path (`WindowStaysOnTopHint`) untouched.

Mapping to the invariants:

| Invariant | LayerShellQt call |
|---|---|
| I1 no focus | `setKeyboardInteractivity(KeyboardInteractivityNone)` |
| I2 click-through | window input region / `setMask()` empty; full only while moving |
| I3 move | `setAnchors(AnchorTop \| AnchorLeft)` + `setMargins(...)` on drag |
| on top | `setLayer(LayerOverlay)`, `setExclusiveZone(-1)` |

## Decisions and trade-offs

- **Layer-shell vs a compositor-specific hack.** KWin has script/rule ways
  to force-keep-above, but they are per-compositor and per-user config, not
  something an app can ship. `wlr-layer-shell` is the portable,
  app-controlled mechanism across KWin and wlroots. CLA chose it for that
  reason; the same applies here.

- **Reuse the Qt window vs a separate render surface.** CLA could not reuse
  its `winit` window (eframe/`winit` does not expose layer-shell), so it
  built a second surface and rendered the overlay itself. A PySide6 port
  should *not* copy that — LayerShellQt attaches layer-shell properties to
  the existing `QWindow`, so `LiveParserWindow` keeps its widgets, styling
  and `pyqtgraph` plot as-is. Avoid the separate-renderer route unless
  LayerShellQt proves unavailable.

## Open questions

1. **PySide6 bindings for LayerShellQt.** *Resolved:* no Python binding exists;
   option (b) was taken — a ctypes shim into `libLayerShellQtInterface.so.6`
   plus the `QT_WAYLAND_SHELL_INTEGRATION=layer-shell` plugin, run in a separate
   process (a blend of (b) and (c)). Works because system Qt == bundled Qt.

2. **Compositor coverage.** *Confirmed* on KWin (Plasma) Wayland. Mutter/GNOME
   parity still unverified; `layershell_supported()` degrades gracefully (falls
   back to the plain toplevel) where the plugin or a matching Qt is absent.

3. **Overlay position on forced termination.** Position/size are now saved
   debounced (~2.5 s after each change) and on the overlay's own Close button, so
   they survive SIGTERM too — only a change made in the last <2.5 s before the
   main app terminates the process is lost.

## References

- CLA layer-shell overlay implementation:
  `/home/raman/Shared/RustroverProjects/STO_CombatLogAnalyzer/src/app/layer_overlay.rs`
- CLA overlay architecture note (invariants I1–I3, drop-order, sizing):
  `/home/raman/Shared/RustroverProjects/STO_CombatLogAnalyzer/docs/OVERLAY.md`
- This overlay in OSCR-UI: `OSCRUI/liveparser.py` (`LiveParserWindow`,
  window flags at `liveparser.py:74`, Wayland move stub at
  `liveparser.py:80`).
- `wlr-layer-shell` protocol: `zwlr_layer_shell_v1` (wlroots protocols).
- LayerShellQt: upstream `layer-shell-qt` (KDE).
