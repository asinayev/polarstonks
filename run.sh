#!/bin/bash
# Wrapper to run polarstonks
# Usage: ./run.sh --run am

# Get dir of script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$DIR"

# Activate venv if not active
if [[ -z "$VIRTUAL_ENV" && ! -f /.dockerenv ]]; then
    if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
        source "$REPO_ROOT/.venv/bin/activate"
    else
        echo "Virtual environment not found at $REPO_ROOT/.venv"
        exit 1
    fi
fi

# Run main
python "$REPO_ROOT/src/main.py" "$@"
