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
  "$VENV/bin/python" -m pip install --quiet parsl || \
    echo "warning: parsl install failed; that engine will report itself skipped" >&2

  # Installing and removing packages across runs can leave a transitive
  # dependency missing while the top-level package still looks installed --
  # parsl importing but typing_extensions absent, for instance. Importing it
  # for real is the only check that catches that, and repairing here costs
  # seconds against a job that would otherwise fail hours in.
  if ! "$VENV/bin/python" -c "import parsl" >/dev/null 2>&1; then
    echo "parsl is installed but does not import; repairing dependencies"
    "$VENV/bin/python" -m pip install --quiet --upgrade --force-reinstall parsl || \
      echo "warning: parsl repair failed" >&2
  fi

  # DagOnStar installs from source as 'dagonstar' and provides the 'dagon'
  # import. Do NOT "pip install dagon": that is an unrelated project which
  # owns the name on PyPI and shadows this import, breaking it with
  # "cannot import name 'ExecutionResult'".
  if "$VENV/bin/python" -c "import dagon, dagon.task" >/dev/null 2>&1 && \
     "$VENV/bin/python" -c "from dagon.task import DagonTask" >/dev/null 2>&1; then
    echo "DagOnStar already present"
  else
    "$VENV/bin/python" -m pip uninstall -y -q dagon >/dev/null 2>&1 || true
    "$VENV/bin/python" -m pip install --quiet \
      "git+https://github.com/DagOnStar/dagonstar.git" || \
      echo "warning: DagOnStar install failed; that engine will report itself skipped" >&2
  fi

  # Nextflow is a JVM tool, not a pip package. It is installed into the
  # virtualenv's bin so that activating the venv puts it on PATH, keeping all
  # three engines in one place. On a cluster that provides it, 'module load
  # nextflow' works just as well and this step is skipped.
  if command -v nextflow >/dev/null 2>&1 && [ ! -x "$VENV/bin/nextflow" ]; then
    echo "nextflow already on PATH: $(command -v nextflow)"
  elif [ -x "$VENV/bin/nextflow" ]; then
    echo "nextflow already installed: $VENV/bin/nextflow"
  elif ! command -v java >/dev/null 2>&1; then
    echo "warning: nextflow needs Java and none was found; that engine will report itself skipped" >&2
  else
    echo "installing nextflow into $VENV/bin"
    ( cd "$VENV/bin" && curl -s https://get.nextflow.io | bash ) >/dev/null 2>&1 || \
      echo "warning: nextflow install failed; that engine will report itself skipped" >&2
  fi
fi

echo "virtualenv ready: $VENV"
# Verify by importing, not by presence: a package whose dependencies are
# missing is still "installed" as far as the filesystem is concerned.
"$VENV/bin/python" - <<'PY'
import importlib
for name, label in [
    ("Crypto", "pycryptodome"), ("zfec", "zfec"), ("lz4", "lz4"),
    ("zstandard", "zstandard"), ("pandas", "pandas"), ("matplotlib", "matplotlib"),
    ("parsl", "parsl (optional)"),
]:
    try:
        importlib.import_module(name)
        print(f"  ok       {label}")
    except ImportError as exc:
        missing = str(exc).split("'")[1] if "'" in str(exc) else exc
        detail = "" if missing == name else f" (needs {missing})"
        print(f"  BROKEN   {label}{detail}" if missing != name else f"  missing  {label}")

# DagOnStar is checked by its API, since an unrelated project shares the
# 'dagon' module name and would otherwise import successfully.
try:
    from dagon.task import DagonTask, TaskType  # noqa: F401
    print("  ok       DagOnStar (optional)")
except ImportError as exc:
    detail = "wrong 'dagon' package installed" if "ExecutionResult" in str(exc) else exc
    print(f"  missing  DagOnStar (optional): {detail}")
PY

if command -v "$VENV/bin/nextflow" >/dev/null 2>&1 || command -v nextflow >/dev/null 2>&1; then
  echo "  ok       nextflow (optional)"
else
  echo "  missing  nextflow (optional)"
fi
