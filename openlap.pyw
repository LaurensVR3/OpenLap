import os
import sys

# Pin the working directory to this file's folder so all relative imports
# and asset paths work correctly whether launched from a terminal, a .bat,
# or by double-clicking the file in Explorer.
_here = os.path.dirname(os.path.abspath(__file__))
os.chdir(_here)
if _here not in sys.path:
    sys.path.insert(0, _here)

import logging
import logging.handlers
import multiprocessing
from app_shell import App


def _setup_logging() -> None:
    log_dir = os.path.join(os.path.expanduser('~'), '.openlap', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'openlap.log')

    fmt = logging.Formatter('%(asctime)s %(levelname)-8s %(name)s — %(message)s')

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


if __name__ == '__main__':
    _setup_logging()
    multiprocessing.freeze_support()
    app = App()
    app._apply_ttk_style()
    app.mainloop()
