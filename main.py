from argparse import ArgumentParser
from multiprocessing import freeze_support, set_start_method, get_start_method
import os
import sys

from OSCRUI import OSCRUI


class Launcher():

    __version__ = '11.1.0'

    @staticmethod
    def base_path() -> str:
        """initialize the base path"""
        try:
            base_path = sys._MEIPASS
        except Exception:
            if getattr(sys, 'frozen', False):
                # The application is frozen
                base_path = os.path.dirname(sys.executable)
            else:
                base_path = os.path.abspath(os.path.dirname(__file__))
        return base_path

    @staticmethod
    def launch():
        argparser = ArgumentParser(prog='OSCR UI', description='The OSCR parser app.')
        argparser.add_argument(
            '--config_dir', type=str, required=False,
            help='Change configuration directory (must be readable and writable)')
        argparser.add_argument(
            '--live-overlay', action='store_true',
            help='Internal: run only the live parser as a Wayland layer-shell overlay')
        args, _ = argparser.parse_known_args()
        if args.live_overlay:
            # Selecting the layer-shell shell integration is process-global, so it
            # only happens in this dedicated overlay process and must precede the
            # QApplication created inside OSCRUI.
            from OSCRUI.wayland_overlay import layershell_supported, prepare_environment
            if layershell_supported():
                prepare_environment()
        exit_code = OSCRUI(
            args=args, app_dir_path=Launcher.base_path(), version=Launcher.__version__,
            overlay_mode=args.live_overlay).run()
        sys.exit(exit_code)


if __name__ == '__main__':
    freeze_support()
    try:
        set_start_method('spawn')
    except RuntimeError:
        if get_start_method() != 'spawn':
            set_start_method('spawn', force=True)
    Launcher.launch()
