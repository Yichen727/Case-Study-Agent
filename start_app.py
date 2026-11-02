"""Create the local app environment once, then start the existing Streamlit app."""
import hashlib
from pathlib import Path
import subprocess
import sys
import venv

PROJECT_ROOT = Path(__file__).resolve().parent


def environment_python(environment, platform=None):
    return environment / ("Scripts/python.exe" if (platform or sys.platform)=="win32" else "bin/python")


def prepare_environment(root):
    requirements = root / "requirements.txt"
    if not requirements.is_file() or not (root / "run.py").is_file():
        raise RuntimeError("Extract the complete project ZIP before running start.bat or start.command.")
    environment = root / ".venv"
    python = environment_python(environment)
    if not python.is_file():
        print("Creating the project's private Python environment...", flush=True)
        venv.create(environment, with_pip=True)
    try:
        runtime = subprocess.run(
            [str(python), "-c", "import sys; assert sys.version_info >= (3,10) and sys.maxsize > 2**32; print(sys.version.split()[0])"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("The .venv environment is unusable. Extract a fresh project copy without .venv and use its launcher again.") from None
    fingerprint = hashlib.sha256(requirements.read_bytes() + b"\0" + runtime.encode() + b"\0" + sys.platform.encode()).hexdigest()
    marker = environment / ".requirements.sha256"
    installed = marker.read_text(encoding="ascii").strip() if marker.is_file() else ""
    if installed != fingerprint:
        print("Installing project requirements. The first setup may take a few minutes...", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
            cwd=root, check=True,
        )
        print("Checking application dependencies...", flush=True)
        subprocess.run(
            [str(python), "-c", "import streamlit; from app.search.keyword_search import search_cases; from app.services.task_service import TaskManager"],
            cwd=root, check=True,
        )
        marker.write_text(fingerprint, encoding="ascii")
    else:
        print("Existing environment is ready; dependency installation skipped.", flush=True)
    return python


def main():
    try:
        if sys.version_info < (3, 10) or sys.maxsize <= 2**32:
            raise RuntimeError("Use start.bat or start.command to locate or install compatible 64-bit Python.")
        python = prepare_environment(PROJECT_ROOT)
        if not (PROJECT_ROOT / ".env").is_file():
            print("AI features need an approved API key in .env. Local import and keyword search are still available.", flush=True)
        template = PROJECT_ROOT / "assets" / "templates" / "Case Study Tool Base Template.pptx"
        if not template.is_file():
            print("The company export template is missing. Add it to assets/templates before exporting.", flush=True)
        print("Starting Case Study Agent. Keep this window open; press Ctrl+C to stop.", flush=True)
        subprocess.run([str(python), str(PROJECT_ROOT / "run.py")], cwd=PROJECT_ROOT, check=True)
        return 0
    except KeyboardInterrupt:
        print("\nCase Study Agent stopped.")
        return 0
    except RuntimeError as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError):
        print("Setup or startup failed. Check the messages above, your internet connection and company installation permissions.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
