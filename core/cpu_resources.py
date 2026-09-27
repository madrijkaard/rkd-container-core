"""CPU capacity visible to containers, with a host fallback while Docker is offline."""

import os
import shutil
import subprocess


def available_cpu_capacity():
    docker = shutil.which('docker')
    if docker:
        try:
            result = subprocess.run(
                [docker, 'info', '--format', '{{.NCPU}}'],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if result.returncode == 0:
                count = int(result.stdout.strip())
                if count > 0:
                    return count, 'docker'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return max(1, os.cpu_count() or 1), 'host'
