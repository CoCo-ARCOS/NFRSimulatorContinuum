#!/bin/bash
set -e

# Go to the proxy_dd directory
cd "$(dirname "$0")"

echo "Rebuilding json-c on $(hostname)..."

# Clean the old build from the laptop
rm -rf json-c-local json-c-src/build
mkdir -p json-c-src/build
cd json-c-src/build

# Load CMake on the cluster (adjust if your cluster uses a specific module version)
module load cmake || echo "Assuming cmake is already available..."

cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_INSTALL_PREFIX=../../json-c-local -DBUILD_SHARED_LIBS=OFF ..
make clean
make install

echo "Successfully built json-c for $(hostname)!"
