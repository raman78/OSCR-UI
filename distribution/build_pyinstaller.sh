#!/bin/sh
set -e
if [ ! -d  distribution ] || [ ! -f assets/oscr_icon_small.png ]
then
  echo "[Error] Start this script from the base folder of the application"
  exit
fi

echo "[Info]  Checking for existing venv \".venv\""
if [ ! -d ".venv" ]
then
  echo "[Info]  No venv found. Creating venv \".venv\"..."
  python3 -m venv .venv
fi

echo "[Info]  Activating venv."
. ".venv/bin/activate"

echo "[Info]  Installing (build) dependencies."
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -e ".[pyinst,wayland]"

# Bundle the layer-shell Qt plugin so the Wayland always-on-top overlay works
# without a matching system layer-shell-qt install. It is built against the
# system Qt, which must match the Qt version PySide6 bundles (both share the
# same SONAME at runtime). libLayerShellQtInterface goes to the bundle root so
# the plugin's SONAME dependency on it resolves; the plugin goes where
# QT_PLUGIN_PATH will look (see OSCRUI/wayland_overlay.py).
LAYER_SHELL_PLUGIN="/usr/lib/qt6/plugins/wayland-shell-integration/liblayer-shell.so"
LAYER_SHELL_IFACE="/usr/lib/libLayerShellQtInterface.so.6"
WAYLAND_ARGS=""
if [ -f "$LAYER_SHELL_PLUGIN" ] && [ -f "$LAYER_SHELL_IFACE" ]
then
  echo "[Info]  Bundling layer-shell plugin for the Wayland overlay."
  WAYLAND_ARGS="--add-binary $LAYER_SHELL_PLUGIN:layershellqt/wayland-shell-integration \
    --add-binary $LAYER_SHELL_IFACE:. --collect-all pywayland"
else
  echo "[Warn]  layer-shell-qt not found; overlay will not stay on top in this build."
fi

echo "[Info]  Creating binary app."
pyinstaller --noconfirm --clean --onedir --name OSCR-UI main.py \
  --add-data assets:assets --add-data locales:locales --windowed \
  --icon assets/oscr_icon_small.png \
  $WAYLAND_ARGS

echo "[Info]  Leaving venv."
deactivate
