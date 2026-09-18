#!/bin/sh
# Launch the nfupgrader service (loopback only). Prints the access URL+token.
cd "$(dirname "$0")" || exit 2
exec python3 -m nfupgrader.httpd "$@"
