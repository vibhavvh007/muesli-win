"""Generate packaging/muesli.ico. Run before PyInstaller."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from muesli_win.ui import icons  # noqa: E402


def main() -> int:
    QApplication.instance() or QApplication([])
    out = Path(__file__).resolve().parent / "muesli.ico"
    pm = icons.app_icon(256).pixmap(256, 256)
    if not pm.save(str(out), "ICO"):
        print("failed to write icon", file=sys.stderr)
        return 1
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
