#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo 'Reconstruindo e recriando o backend local...'
bash "$script_dir/configure-secret-key.sh" --local
echo 'Backend local pronto.'
