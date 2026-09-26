#!/bin/sh
# STUB for demo reliability: pretends to deploy and never contacts any real
# environment. Kept short on purpose: a gated command leases its working directory
# while it runs, so long commands widen fs_guard's window.
set -e
target="${1:?usage: ./deploy.sh <staging|prod>}"
cd "$(dirname "$0")"
echo "deploying v$(cat VERSION) to ${target}..."
sleep 1
echo "deployed v$(cat VERSION) to ${target} (stub)"
