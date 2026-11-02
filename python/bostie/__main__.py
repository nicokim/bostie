import platform
import sys

import bostie

sys.stdout.write(
    f"bostie {bostie.__version__}\n"
    f"{platform.python_implementation()} {platform.python_version()}\n"
    f"{platform.platform()}\n"
)
