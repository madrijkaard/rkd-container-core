"""Build an image from a stored Dockerfile and run a limited container."""

import base64
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError

from .models import port_binding, volume_mount
from .github_branches import GithubBranchError, parse_github_repository
from .token_cipher import TokenDecryptionError, decrypt_token


BUILD_CONTEXT = Path(__file__).resolve().parent.parent


class ContainerCreationError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.status = status


def cpu_limit(value, maximum=None):
    try:
        amount = Decimal(value.strip().replace(',', '.'))
    except (AttributeError, InvalidOperation):
        amount = Decimal(0)
    if not amount.is_finite() or amount <= 0:
        raise ContainerCreationError('invalid_resources', 'CPU must be a positive number.', 400)
    if maximum is not None and amount > maximum:
        raise ContainerCreationError(
            'invalid_resources', f'CPU cannot exceed the current limit of {maximum}.', 400
        )
    return format(amount.normalize(), 'f')


def memory_limit(value, maximum=None):
    match = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|K|M|G)\s*', value, re.I)
    if not match:
        raise ContainerCreationError('invalid_resources', 'Memory must include B, KB, MB, or GB.', 400)
    amount = Decimal(match.group(1).replace(',', '.'))
    units = {'B': 1, 'K': 1024, 'KB': 1024, 'M': 1024**2, 'MB': 1024**2,
             'G': 1024**3, 'GB': 1024**3}
    byte_count = int(amount * units[match.group(2).upper()])
    if byte_count < 6 * 1024**2:
        raise ContainerCreationError('invalid_resources', 'Memory must be at least 6 MB.', 400)
    if maximum is not None and byte_count > maximum:
        raise ContainerCreationError('invalid_resources', 'Memory exceeds the current machine limit.', 400)
    return f'{byte_count}b'


def clone_repository(image, destination):
    """Check out the selected branch without putting its token in argv or Git config."""
    try:
        owner, repo = parse_github_repository(image.repository)
    except GithubBranchError as error:
        raise ContainerCreationError('invalid_repository', str(error), 400) from None
    git = shutil.which('git')
    if git is None:
        raise ContainerCreationError('git_unavailable', 'Git is unavailable.', 503)
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GCM_INTERACTIVE'] = 'Never'
    for key in ('GIT_TRACE', 'GIT_TRACE_CURL', 'GIT_TRACE_PACKET', 'GIT_CURL_VERBOSE'):
        env.pop(key, None)
    if image.isPrivate:
        if not image.token:
            raise ContainerCreationError('invalid_configuration', 'Private repository token is missing.', 400)
        try:
            token = decrypt_token(image.token)
        except TokenDecryptionError:
            raise ContainerCreationError('invalid_configuration', 'Private repository token is unavailable.', 503) from None
        credentials = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
        env['GIT_CONFIG_COUNT'] = '1'
        env['GIT_CONFIG_KEY_0'] = 'http.https://github.com/.extraHeader'
        env['GIT_CONFIG_VALUE_0'] = f'Authorization: Basic {credentials}'
    url = f'https://github.com/{owner}/{repo}.git'
    try:
        cloned = subprocess.run(
            [git, '-c', 'credential.helper=', 'clone', '--depth', '1', '--single-branch',
             '--branch', image.branch, '--', url, str(destination)],
            env=env, capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ContainerCreationError('repository_clone_failed', 'Repository checkout failed.', 502) from None
    if cloned.returncode != 0:
        raise ContainerCreationError('repository_clone_failed', 'Repository checkout failed. Check the branch and token.', 502)
    shutil.rmtree(destination / '.git', ignore_errors=True)


def create_container(setup):
    """Return the new container identity; never pass stored text to a shell."""
    docker = shutil.which('docker')
    if docker is None:
        raise ContainerCreationError('docker_unavailable', 'Docker is unavailable.', 503)

    try:
        daemon = subprocess.run(
            [docker, 'info', '--format', '{{.NCPU}} {{.MemTotal}}'],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ContainerCreationError('docker_unavailable', 'Docker is unavailable.', 503) from None
    if daemon.returncode != 0:
        raise ContainerCreationError('docker_unavailable', 'Docker is unavailable.', 503)
    try:
        cpu_maximum, memory_maximum = map(int, daemon.stdout.split())
        if cpu_maximum < 1 or memory_maximum < 6 * 1024**2:
            raise ValueError
    except ValueError:
        raise ContainerCreationError('docker_unavailable', 'Docker resource capacity is unavailable.', 503) from None

    cpus = cpu_limit(setup.cpu, cpu_maximum)
    memory = memory_limit(setup.memory, memory_maximum)
    try:
        published_port = port_binding(setup.port)
        mounted_volume = volume_mount(setup.volume)
    except ValidationError as error:
        raise ContainerCreationError('invalid_configuration', error.messages[0], 400) from None
    if not setup.image.definition.strip():
        raise ContainerCreationError('invalid_definition', 'Image definition is empty.', 400)

    name = f'container-core-setup-{setup.pk}-{uuid4().hex[:12]}'
    workspace = tempfile.TemporaryDirectory(prefix='container-core-source-') if setup.image.repository else nullcontext(None)
    with workspace as temporary_root:
        context = BUILD_CONTEXT
        if temporary_root:
            context = Path(temporary_root) / 'source'
            clone_repository(setup.image, context)
        try:
            built = subprocess.run(
                [docker, 'build', '--tag', name, '--file', '-', str(context)],
                input=setup.image.definition, capture_output=True, text=True,
                timeout=600, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ContainerCreationError('docker_build_failed', 'Docker image build failed.', 502) from None
        if built.returncode != 0:
            raise ContainerCreationError('docker_build_failed', 'Docker image build failed.', 502)

        try:
            run_args = [docker, 'run', '--detach', '--name', name, '--cpus', cpus,
                        '--memory', memory]
            if published_port:
                run_args.extend(['--publish', published_port])
            if mounted_volume:
                run_args.extend(['--mount', mounted_volume])
            run_args.append(name)
            started = subprocess.run(
                run_args,
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ContainerCreationError('container_start_failed', 'Container start failed.', 502) from None
        if started.returncode != 0 or not started.stdout.strip():
            raise ContainerCreationError('container_start_failed', 'Container start failed.', 502)
    return {'container_id': started.stdout.strip(), 'container_name': name}
