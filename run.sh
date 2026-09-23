#!/bin/sh
# Launch the nfupgrader service (loopback only). Prints the access URL+token.
# Remember where we were started from (the default depot directory) before
# changing to the install directory.
NFU_START_DIR=${NFU_START_DIR:-$(pwd)}
export NFU_START_DIR
cd "$(dirname "$0")" || exit 2
exec python3 -m nfupgrader.httpd "$@"
