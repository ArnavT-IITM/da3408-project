#!/bin/sh
# Opt-in local CI: commits request the same checked pipeline used by the console.
set -eu
mkdir -p .git/hooks
cat > .git/hooks/post-commit <<'HOOK'
#!/bin/sh
curl --fail --silent --show-error -X POST http://127.0.0.1:${AIRCAST_API_PORT:-18000}/pipeline/run \
  || echo 'AirCast CI trigger unavailable. Start the stack and run make pipeline.' >&2
HOOK
chmod +x .git/hooks/post-commit
