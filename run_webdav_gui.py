#!/usr/bin/env python3
"""Launch the Zotero WebDAV configuration GUI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from zoteropdf2md.webdav_gui import main

if __name__ == "__main__":
    main()
