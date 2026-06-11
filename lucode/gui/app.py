from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from lucode.gui.main_window import MainWindow
from lucode.gui.theme import apply_theme


def run_gui(*, workspace: Path, mode: str = "") -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow(workspace=workspace, mode=mode)
    window.show()

    with loop:
        loop.run_forever()
    return 0
