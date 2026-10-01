#!/bin/sh
set -eu
exec "${MYSQL_ROUTER_PYTHON:-python3}" "$(dirname "$0")/post-renderer.py"
