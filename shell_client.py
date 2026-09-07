"""Runs arbitrary shell scripts locally via Git Bash -- no sandboxing, by
design. See 1-DOCUMENTATION.md section 2 for the full safety rationale.
"""
import os
import subprocess
import tempfile

BASH_CANDIDATES = [
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
]


def _find_bash():
    for path in BASH_CANDIDATES:
        if os.path.isfile(path):
            return path
    # fall back to PATH lookup
    from shutil import which
    found = which("bash.exe") or which("bash")
    if found:
        return found
    raise FileNotFoundError(
        "Couldn't find Git Bash's bash.exe. Checked: " + ", ".join(BASH_CANDIDATES) +
        " and PATH. Install Git for Windows if it's missing."
    )


def run_shell(script_text, timeout=60, cwd=None):
    """Writes script_text to a temp .sh file and runs it with Git Bash.
    Returns dict: {stdout, stderr, returncode, timed_out}."""
    bash_path = _find_bash()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, newline="\n") as f:
        f.write(script_text)
        script_path = f.name

    try:
        try:
            proc = subprocess.run(
                [bash_path, script_path],
                capture_output=True, text=True, timeout=timeout, cwd=cwd,
            )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as e:
            return {
                "stdout": e.stdout or "",
                "stderr": (e.stderr or "") + f"\n[TIMED OUT after {timeout}s]",
                "returncode": None,
                "timed_out": True,
            }
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass
