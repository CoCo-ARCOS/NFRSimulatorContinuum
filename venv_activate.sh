#!/usr/bin/env bash
# Activate the project virtualenv, creating it on first use.
# Meant to be sourced, not executed:  source venv_activate.sh

_venv_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VENV=${VENV:-"$_venv_root/.venv"}

if [ ! -x "$VENV/bin/python" ]; then
  echo "virtualenv missing at $VENV; creating it"
  VENV="$VENV" "$_venv_root/setup_venv.sh"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Engines are launched as subprocesses; make them inherit this interpreter.
export PATH="$VENV/bin:$PATH"
export VIRTUAL_ENV="$VENV"
unset _venv_root
