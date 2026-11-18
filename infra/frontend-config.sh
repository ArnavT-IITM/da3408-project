#!/bin/sh
set -eu
# JSON-escape the configurable URL without pulling a JS build tool into the UI.
api_url=$(printf '%s' "${AIRCAST_API_URL:-/api}" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf 'window.AIRCAST_API_URL = "%s";\n' "$api_url" > /usr/share/nginx/html/config.js
