"""Memory capacity visible to containers, with a host fallback while Docker is offline."""

import ctypes
import os
import shutil
import subprocess


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ('length', ctypes.c_ulong),
        ('load', ctypes.c_ulong),
        ('total_physical', ctypes.c_ulonglong),
        ('available_physical', ctypes.c_ulonglong),
        ('total_page_file', ctypes.c_ulonglong),
        ('available_page_file', ctypes.c_ulonglong),
        ('total_virtual', ctypes.c_ulonglong),
        ('available_virtual', ctypes.c_ulonglong),
        ('available_extended_virtual', ctypes.c_ulonglong),
    ]


def host_memory_bytes():
    if os.name == 'nt':
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total_physical
        return 0
    try:
        return os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (AttributeError, OSError, ValueError):
        return 0


def available_memory_capacity():
    docker = shutil.which('docker')
    if docker:
        try:
            result = subprocess.run(
                [docker, 'info', '--format', '{{.MemTotal}}'],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if result.returncode == 0:
                count = int(result.stdout.strip())
                if count > 0:
                    return count, 'docker'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return host_memory_bytes(), 'host'
