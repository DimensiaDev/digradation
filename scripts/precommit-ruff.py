import subprocess
import sys

files = [f.removeprefix(sys.argv[1]) for f in sys.argv[2:]]

# `--force-exclude` for the same reason `[tool.black]` sets `force-exclude`: ruff
# applies `exclude` only to files found by directory traversal, and this shim
# passes filenames explicitly, so without the flag a generated migration under
# `alembic/versions` gets rewritten by every commit on every platform. Black
# guards this in config; ruff has no equivalent config key, only the flag.
subprocess.run(
    ["poetry", "run", "ruff", "check", "--force-exclude", "--fix", *files],
    cwd=sys.argv[1],
    check=False,
)
