#!/bin/sh
# Container entrypoint for Lyrarr.
#
# PUID / PGID (optional): run Lyrarr as that uid/gid instead of root, so files
# it writes (lyrics, covers, the database) are owned by your host user. /config
# is chowned to match on start; /music is never touched — make sure PUID/PGID
# can write to it. Leave both unset to keep running as root (the historical
# behaviour, so existing installs keep their file ownership).
# UMASK (optional): file-creation mask for everything Lyrarr writes, e.g. 002.
set -e

# An empty build-arg would leave LYRARR_VERSION="" and hide the real version.
[ -n "${LYRARR_VERSION}" ] || unset LYRARR_VERSION

if [ -n "${UMASK}" ]; then
    umask "${UMASK}"
fi

if [ -n "${PUID}" ] || [ -n "${PGID}" ]; then
    if [ "$(id -u)" != "0" ]; then
        echo "lyrarr: PUID/PGID set but the container was not started as root; ignoring them" >&2
        exec "$@"
    fi
    PUID="${PUID:-0}"
    PGID="${PGID:-0}"
    case "${PUID}${PGID}" in
        *[!0-9]*) echo "lyrarr: PUID and PGID must be numeric" >&2; exit 1 ;;
    esac

    mkdir -p /config
    # Only touch what isn't already owned correctly (fast on big config dirs).
    find /config \( ! -user "${PUID}" -o ! -group "${PGID}" \) -exec chown "${PUID}:${PGID}" {} +

    export HOME=/config
    echo "lyrarr: running as uid ${PUID}, gid ${PGID}"
    exec setpriv --reuid="${PUID}" --regid="${PGID}" --clear-groups -- "$@"
fi

exec "$@"
