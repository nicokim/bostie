import subprocess
import sys
from importlib.metadata import version


def test_python_m_bostie_prints_the_installed_version() -> None:
    printed = subprocess.run(
        [sys.executable, "-m", "bostie"], capture_output=True, text=True, check=True
    ).stdout

    assert printed.splitlines()[0] == f"bostie {version('bostie')}"
