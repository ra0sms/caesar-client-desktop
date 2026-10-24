import os
import sys

# One QApplication (offscreen) for the whole test run: widget tests need it,
# and the other tests reuse it through QCoreApplication.instance().
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
