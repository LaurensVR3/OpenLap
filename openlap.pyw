import os
import sys

# Pin the working directory to this file's folder so all relative imports
# and asset paths work correctly whether launched from a terminal, a .bat,
# or by double-clicking the file in Explorer.
_here = os.path.dirname(os.path.abspath(__file__))
os.chdir(_here)
if _here not in sys.path:
    sys.path.insert(0, _here)

import multiprocessing
from app_shell import App

if __name__ == '__main__':
    multiprocessing.freeze_support()
    app = App()
    app._apply_ttk_style()
    app.mainloop()
