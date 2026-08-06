#!/usr/bin/env bash
set -euo pipefail

# Create (or reuse) the project virtualenv and install the dependencies.
#
# Distributions that follow PEP 668 refuse a plain "pip install" into the system
# interpreter, and cluster nodes rarely let us install system-wide anyway, so
# every entry point runs out of this virtualenv.
#
#   ./setup_venv.sh                 # create .venv and install requirements
#   VENV=/scratch/nfr-venv ./setup_venv.sh
#   ENGINE_EXTRAS=1 ./setup_venv.sh # also install Parsl and DagOnStar
#
# Source venv_activate.sh to use it from another script.

ROOT=$(cd "$(dirname "$0")" && pwd)
VENV=${VENV:-"$ROOT/.venv"}
PYTHON=${PYTHON:-python3}

if [ ! -d "$VENV" ]; then
  echo "creating virtualenv at $VENV"
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"

if [ "${ENGINE_EXTRAS:-0}" = "1" ]; then
  # Optional: the Python-native engines. Nextflow is a JVM tool installed
  # separately (or via 'module load nextflow') and is not a pip dependency.
  "$VENV/bin/python" -m pip install --quiet parsl || \
    echo "warning: parsl install failed; that engine will report itself skipped" >&2
  "$VENV/bin/python" -m pip install --quiet dagon || \
    echo "warning: dagon install failed; DagOnStar falls back to inline stages" >&2
fi

echo "virtualenv ready: $VENV"
"$VENV/bin/python" - <<'PY'
import importlib
for name, label in [
    ("Crypto", "pycryptodome"), ("zfec", "zfec"), ("lz4", "lz4"),
    ("zstandard", "zstandard"), ("pandas", "pandas"), ("matplotlib", "matplotlib"),
    ("parsl", "parsl (optional)"), ("dagon", "dagon (optional)"),
]:
    try:
        importlib.import_module(name)
        print(f"  ok       {label}")
    except ImportError:
        print(f"  missing  {label}")
PY
