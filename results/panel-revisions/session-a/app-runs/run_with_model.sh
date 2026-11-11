#!/bin/bash
# Stand-in for scripts/run.sh, for the Session A in-app runs only. scripts/sv-main.sh calls
# $SV_RUN_SH instead of run.sh when it is set, with the boot service's own arguments; this passes
# them on to the real run.sh unchanged and adds the detection model under test:
#
#   SV_RUN_SH=<this file> SV_DET_HEF=<path to .hef> scripts/sv-main.sh
#
# Nothing else differs from a boot-service start. Without SV_DET_HEF the app uses its default model.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
if [ -n "${SV_DET_HEF:-}" ]; then
    exec "$REPO/scripts/run.sh" "$@" --det-hef-path "$SV_DET_HEF"
fi
exec "$REPO/scripts/run.sh" "$@"
