"""
Wayland always-on-top overlay support for the live parser via wlr-layer-shell.

On Wayland the compositor ignores Qt's `WindowStaysOnTopHint`, so a normal
toplevel cannot float above a fullscreen game (see docs/WAYLAND_OVERLAY.md).
The portable fix is the `wlr-layer-shell` protocol, exposed to Qt by the
`LayerShellQt` library. Two obstacles are handled here:

1. pip-installed PySide6 bundles its own Qt but no layer-shell shell-integration
   plugin. The system `layer-shell-qt` package ships one, but Qt only finds it
   when `QT_PLUGIN_PATH` points at the system plugin dir and it is ABI-loadable
   (same major.minor Qt as PySide6's bundled Qt). `prepare_environment()` /
   `layershell_supported()` cover this. Both are process-global, which is why
   the overlay runs in its own process.

2. There is no PySide6 binding for `LayerShellQt::Window`. We call its C++ API
   through ctypes against `libLayerShellQtInterface.so.6` (symbol names verified
   with `nm -D`); the QWindow pointer comes from shiboken6.
"""
import ctypes
import os

from PySide6.QtCore import QMargins, qVersion
from PySide6.QtGui import QWindow
from shiboken6 import Shiboken

# Enum values copied from /usr/include/LayerShellQt/window.h
LAYER_BACKGROUND, LAYER_BOTTOM, LAYER_TOP, LAYER_OVERLAY = 0, 1, 2, 3
ANCHOR_NONE, ANCHOR_TOP, ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT = 0, 1, 2, 4, 8
KEYBOARD_NONE, KEYBOARD_EXCLUSIVE, KEYBOARD_ON_DEMAND = 0, 1, 2

SYSTEM_QT_PLUGIN_PATH = '/usr/lib/qt6/plugins'
_LAYER_SHELL_PLUGIN = os.path.join(
    SYSTEM_QT_PLUGIN_PATH, 'wayland-shell-integration', 'liblayer-shell.so')
_INTERFACE_LIB = 'libLayerShellQtInterface.so.6'
_SYSTEM_QT_CORE = '/usr/lib/libQt6Core.so.6'

# Mangled symbols exported by libLayerShellQtInterface.so.6 (Itanium C++ ABI)
_SYM_GET = '_ZN12LayerShellQt6Window3getEP7QWindow'
_SYM_SET_LAYER = '_ZN12LayerShellQt6Window8setLayerENS0_5LayerE'
_SYM_SET_ANCHORS = '_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE'
_SYM_SET_MARGINS = '_ZN12LayerShellQt6Window10setMarginsERK8QMargins'
_SYM_SET_KEYBOARD = '_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE'
_SYM_SET_EXCLUSIVE_ZONE = '_ZN12LayerShellQt6Window16setExclusiveZoneEi'


def prepare_environment():
    """
    Point Qt at the system layer-shell plugin and select it as the Wayland shell
    integration. MUST be called before the QApplication is constructed, and is
    process-global (every window becomes a layer surface) — only call it in a
    dedicated overlay process.
    """
    existing = [p for p in os.environ.get('QT_PLUGIN_PATH', '').split(os.pathsep) if p]
    if SYSTEM_QT_PLUGIN_PATH not in existing:
        existing.append(SYSTEM_QT_PLUGIN_PATH)
    os.environ['QT_PLUGIN_PATH'] = os.pathsep.join(existing)
    os.environ['QT_WAYLAND_SHELL_INTEGRATION'] = 'layer-shell'


def _system_qt_version() -> str:
    """Return the version of the system Qt the layer-shell plugin links against
    (e.g. '6.11.1'), read from the libQt6Core soname symlink, or '' if unknown."""
    try:
        target = os.path.basename(os.path.realpath(_SYSTEM_QT_CORE))
    except OSError:
        return ''
    # 'libQt6Core.so.6.11.1' -> '6.11.1'
    marker = '.so.'
    idx = target.find(marker)
    return target[idx + len(marker):] if idx != -1 else ''


def layershell_supported() -> bool:
    """
    Whether a layer-shell overlay can actually be created here: a Wayland
    session, the system plugin present, and its Qt ABI-compatible with PySide6's
    bundled Qt (same major.minor). False means callers should fall back to the
    plain always-on-top toplevel.
    """
    if not (os.environ.get('WAYLAND_DISPLAY')
            or os.environ.get('XDG_SESSION_TYPE') == 'wayland'):
        return False
    if not os.path.isfile(_LAYER_SHELL_PLUGIN):
        return False
    system = _system_qt_version().split('.')
    bundled = qVersion().split('.')
    return system[:2] == bundled[:2] and len(system) >= 2


def _cpp_ptr(obj) -> ctypes.c_void_p:
    """Raw C++ pointer of a shiboken-wrapped Qt object."""
    return ctypes.c_void_p(Shiboken.getCppPointer(obj)[0])


_funcs = None


def _library() -> dict:
    """
    Load libLayerShellQtInterface once and return its prepared ctypes functions,
    keyed by symbol. The library is system Qt but the same version as PySide6's
    bundled Qt (see layershell_supported), so it is safe to load in-process.

    ctypes' `lib[symbol]` returns a fresh function object each call and would
    discard the prototypes, so the configured pointers are cached here.
    """
    global _funcs
    if _funcs is None:
        lib = ctypes.CDLL(_INTERFACE_LIB)
        funcs = {symbol: lib[symbol] for symbol in (
            _SYM_GET, _SYM_SET_LAYER, _SYM_SET_ANCHORS, _SYM_SET_KEYBOARD,
            _SYM_SET_EXCLUSIVE_ZONE, _SYM_SET_MARGINS)}
        funcs[_SYM_GET].restype = ctypes.c_void_p
        funcs[_SYM_GET].argtypes = [ctypes.c_void_p]
        # every setter: (Window* this, value); QFlags<Anchor> and enums pass as int
        for symbol in (_SYM_SET_LAYER, _SYM_SET_ANCHORS, _SYM_SET_KEYBOARD,
                       _SYM_SET_EXCLUSIVE_ZONE):
            funcs[symbol].restype = None
            funcs[symbol].argtypes = [ctypes.c_void_p, ctypes.c_int]
        funcs[_SYM_SET_MARGINS].restype = None
        funcs[_SYM_SET_MARGINS].argtypes = [ctypes.c_void_p, ctypes.c_void_p]  # QMargins const&
        _funcs = funcs
    return _funcs


def configure_as_overlay(
        qwindow: QWindow, anchors: int = ANCHOR_TOP | ANCHOR_LEFT,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0)):
    """
    Turn `qwindow` into an `overlay`-layer surface that never takes keyboard
    focus. Must run while the QWindow exists but before it is shown, so the
    shell integration reads this config when it creates the layer surface.

    Parameters:
    - :param qwindow: the window handle (QWidget.windowHandle()) to configure
    - :param anchors: OR-ed ANCHOR_* flags the surface pins to
    - :param margins: (left, top, right, bottom) offset from the anchored edges

    :return: opaque LayerShellQt::Window* pointer, reusable with set_margins()
    """
    lib = _library()
    ls_window = lib[_SYM_GET](_cpp_ptr(qwindow))
    if not ls_window:
        raise RuntimeError('LayerShellQt::Window::get returned null')
    lib[_SYM_SET_LAYER](ls_window, LAYER_OVERLAY)
    lib[_SYM_SET_KEYBOARD](ls_window, KEYBOARD_NONE)  # I1: never steal focus
    lib[_SYM_SET_ANCHORS](ls_window, anchors)
    lib[_SYM_SET_EXCLUSIVE_ZONE](ls_window, -1)  # do not reserve screen space
    set_margins(ls_window, *margins)
    return ls_window


def set_margins(ls_window, left: int, top: int, right: int = 0, bottom: int = 0):
    """
    Update the overlay surface offset from its anchored edges. This is how a
    layer surface is repositioned (I3) — it cannot be dragged like a toplevel.
    `ls_window` is the pointer returned by configure_as_overlay().
    """
    qm = QMargins(left, top, right, bottom)
    _library()[_SYM_SET_MARGINS](ls_window, _cpp_ptr(qm))
