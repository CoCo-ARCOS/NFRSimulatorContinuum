#!/usr/bin/env bash
set -euo pipefail

# Fetch a self-contained Nextflow binary for transfer to an offline cluster.
#
# The usual installer (curl -s https://get.nextflow.io | bash) only writes a
# ~17 KB launcher script, which then downloads ~41 MB of dependencies into
# $NXF_HOME on first run. That second step needs outbound network access from
# the compute node, which a cluster often does not grant. The "-dist" release
# asset bundles everything into one executable, so it runs with no download at
# all.
#
#   ./fetch_nextflow.sh                       # newest release, into ./bin
#   ./fetch_nextflow.sh 26.04.6 /tmp/out      # pinned version and directory
#
# Then copy the single file to the cluster and point the experiments at it:
#   scp bin/nextflow-dist cluster:~/NFRSimulatorContinuum/bin/
#   NEXTFLOW=bin/nextflow-dist sbatch slurm_realengines.sh
#
# Java is still required on the cluster ("module load java" on most sites);
# the bundle carries Nextflow's dependencies, not a JVM.

ROOT=$(cd "$(dirname "$0")" && pwd)
VERSION=${1:-}
DEST=${2:-"$ROOT/bin"}

if [ -z "$VERSION" ]; then
  echo "resolving the latest Nextflow release"
  VERSION=$(curl -sSL https://api.github.com/repos/nextflow-io/nextflow/releases/latest \
    | grep -m1 '"tag_name"' | sed 's/.*"v\{0,1\}\([0-9][^"]*\)".*/\1/')
  if [ -z "$VERSION" ]; then
    echo "could not resolve the latest version; pass one explicitly" >&2
    exit 1
  fi
fi

mkdir -p "$DEST"
OUT="$DEST/nextflow-dist"
URL="https://github.com/nextflow-io/nextflow/releases/download/v${VERSION}/nextflow-${VERSION}-dist"

echo "downloading Nextflow ${VERSION}"
if ! curl -fsSL -o "$OUT" "$URL"; then
  # Releases before ~22.x published the bundle as "-all" instead of "-dist".
  echo "  '-dist' asset not found, trying the older '-all' name"
  curl -fsSL -o "$OUT" \
    "https://github.com/nextflow-io/nextflow/releases/download/v${VERSION}/nextflow-${VERSION}-all"
fi
chmod +x "$OUT"

# A self-contained bundle is tens of megabytes; the plain launcher is ~17 KB.
SIZE=$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT")
if [ "$SIZE" -lt 1000000 ]; then
  echo "error: downloaded file is only ${SIZE} bytes -- that is the launcher," >&2
  echo "       not the self-contained bundle. Check the version number." >&2
  exit 1
fi

echo "wrote $OUT ($((SIZE / 1024 / 1024)) MB)"

# Verify it runs without fetching anything, which is the property that matters.
TMP_HOME=$(mktemp -d)
if NXF_HOME="$TMP_HOME" NXF_OFFLINE=true "$OUT" -v >/dev/null 2>&1; then
  echo "verified: runs offline with an empty NXF_HOME"
else
  echo "warning: the bundle did not run offline here; check that java is present" >&2
fi
rm -rf "$TMP_HOME"

cat <<EOF

Copy to the cluster and use it with:
  scp $OUT <cluster>:<path>/bin/
  NEXTFLOW=bin/nextflow-dist ./run_realengines_all.sh proxy_dd realengines-output
  NEXTFLOW=bin/nextflow-dist sbatch slurm_realengines.sh
EOF
