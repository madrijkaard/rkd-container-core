"""Exercise real shell scripts in temporary checkouts with a fake Docker CLI.

No actual container or application database can be removed by these tests.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which('bash')
if not BASH and Path('C:/Program Files/Git/bin/bash.exe').exists():
    BASH = 'C:/Program Files/Git/bin/bash.exe'

FAKE_DOCKER = '''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [[ -n "${FAIL_MATCH:-}" && "$*" == *"$FAIL_MATCH"* ]]; then exit 17; fi
if [[ "$*" == 'container ls -aq' ]]; then
    printf 'root-container\\ndata-container\\nunrelated-container\\n'
elif [[ "$1" == inspect ]]; then
    case "${!#}" in
        root-container) printf '%s\\n' "$FIXTURE_ROOT" ;;
        data-container) printf '%s/volumes/sqlite\\n' "$FIXTURE_ROOT" ;;
        unrelated-container) printf '%s/unrelated\\n' "$FIXTURE_ROOT" ;;
    esac
fi
'''


@unittest.skipUnless(BASH, 'Bash is required to exercise deployment scripts.')
class BackendScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='dockestra scripts ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'checkout'
        self.root.mkdir()
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts')
        (self.root / '.env.example').write_text('DJANGO_SECRET_KEY=\nEMAIL_HOST_USER=\n')
        for name in ('docker-compose.yml', 'docker-compose.local.yml'):
            (self.root / name).write_text('services: {}\n')
        self.bin = Path(self.temporary.name) / 'bin'
        self.bin.mkdir()
        docker = self.bin / 'docker'
        docker.write_text(FAKE_DOCKER, encoding='utf-8', newline='\n')
        docker.chmod(0o755)
        self.log = Path(self.temporary.name) / 'docker.log'
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env.get('PATH', ''),
                        DOCKER_LOG=str(self.log), FIXTURE_ROOT=str(self.root))
        self.env.pop('FAIL_MATCH', None)
        self.env_file = self.root / '.env'
        self.original_env = 'DJANGO_SECRET_KEY=old-test-key\nEMAIL_HOST_USER=sender@example.com\n'

    def run_script(self, name, *args, failure=None, cwd=None):
        env = {**self.env, 'FAIL_MATCH': failure or ''}
        result = subprocess.run([BASH, str(self.root / 'scripts' / name), *args],
                                cwd=cwd or self.temporary.name, env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        return result

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def create_database(self, relative='volumes/sqlite/rkd_dockestra_core.local.sqlite3'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'existing-database-must-survive')
        return path

    def test_plain_generation_never_calls_docker_and_preserves_other_settings(self):
        self.env_file.write_text('EMAIL_HOST_USER=sender@example.com\nDJANGO_SECRET_KEY=\n')
        result = self.run_script('configure-secret-key.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.env_file.read_text()
        self.assertRegex(content, r'DJANGO_SECRET_KEY=[0-9a-f]{96}\n')
        self.assertIn('EMAIL_HOST_USER=sender@example.com', content)
        key = re.search(r'DJANGO_SECRET_KEY=(\w+)', content).group(1)
        self.assertNotIn(key, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.root / 'scripts/.env').exists())
        self.assertEqual(self.run_script('configure-secret-key.sh').returncode, 0)
        self.assertEqual(self.env_file.read_text(), content)

    def test_existing_key_and_database_are_preserved_byte_for_byte(self):
        self.env_file.write_text(self.original_env)
        database = self.create_database()
        result = self.run_script('configure-secret-key.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file.read_text(), self.original_env)
        self.assertEqual(database.read_bytes(), b'existing-database-must-survive')
        self.assertEqual(self.calls(), [])

    def test_force_resets_all_checkout_databases_but_not_other_files_or_volumes(self):
        self.env_file.write_text(self.original_env)
        databases = [self.create_database(name) for name in (
            'rkd_dockestra_core.sqlite3', 'container_core.sqlite3',
            'volumes/sqlite/rkd_dockestra_core.local.sqlite3',
            'volumes/sqlite/rkd_dockestra_core.sqlite3',
            'volumes/sqlite/container_core.local.sqlite3',
            'volumes/sqlite/container_core.sqlite3',
            'volumes/sqlite/rkd_dockestra_core.local.sqlite3-wal',
            'volumes/sqlite/rkd_dockestra_core.local.sqlite3-shm',
            'volumes/sqlite/container_core.sqlite3-journal')]
        unrelated = self.create_database('volumes/other/keep.sqlite3')
        result = self.run_script('configure-secret-key.sh', '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(not path.exists() for path in databases))
        self.assertTrue(unrelated.exists())
        self.assertTrue((self.root / 'volumes/sqlite').is_dir())
        self.assertRegex(self.env_file.read_text(), r'DJANGO_SECRET_KEY=[0-9a-f]{96}\n')
        self.assertNotIn('old-test-key', self.env_file.read_text())
        self.assertIn('EMAIL_HOST_USER=sender@example.com', self.env_file.read_text())
        removed = [call for call in self.calls() if call.startswith('container rm')]
        self.assertEqual(removed, ['container rm --force root-container data-container'])
        self.assertFalse(any(' build ' in call or 'compose' in call or 'volume rm' in call for call in self.calls()))

    def test_reset_aborts_before_mutating_data_if_docker_or_container_removal_fails(self):
        self.env_file.write_text(self.original_env)
        database = self.create_database()
        for failure in ('info', 'container rm'):
            with self.subTest(failure=failure):
                result = self.run_script('configure-secret-key.sh', '--force', failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.env_file.read_text(), self.original_env)
                self.assertEqual(database.read_bytes(), b'existing-database-must-survive')
                self.assertFalse((self.root / '.dockestra-operation.lock').exists())

    def test_missing_key_is_generated_without_resetting_existing_database(self):
        database = self.create_database('container_core.sqlite3')
        result = self.run_script('configure-secret-key.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(database.read_bytes(), b'existing-database-must-survive')
        self.assertRegex(self.env_file.read_text(), r'DJANGO_SECRET_KEY=[0-9a-f]{96}\n')
        self.assertEqual(self.calls(), [])

    def test_custom_database_path_and_symlinks_are_not_deleted(self):
        self.env_file.write_text(self.original_env + 'DJANGO_DB_PATH=/external/database.sqlite3\n')
        database = self.create_database()
        result = self.run_script('configure-secret-key.sh', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(database.exists())
        self.assertEqual(self.calls(), [])
        self.env_file.write_text(self.original_env)
        outside = Path(self.temporary.name) / 'external.sqlite3'
        outside.write_bytes(b'external-data')
        link = self.root / 'linked.sqlite3'
        try:
            link.symlink_to(outside)
        except OSError:
            return  # Windows may not grant symlink creation permission.
        result = self.run_script('configure-secret-key.sh', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_bytes(), b'external-data')
        self.assertEqual(self.env_file.read_text(), self.original_env)

    def test_help_and_removed_flags_never_modify_anything(self):
        for flag, status in (('--help', 0), ('-h', 0), ('--local', 1), ('--generate-only', 1)):
            with self.subTest(flag=flag):
                result = self.run_script('configure-secret-key.sh', flag)
                self.assertEqual(result.returncode, status)
                self.assertIn('--force', result.stdout + result.stderr)
                self.assertFalse(self.env_file.exists())
                self.assertEqual(self.calls(), [])

    def test_backend_missing_or_blank_key_stops_before_docker(self):
        for value in (None, '', '   ', '"   "', "'   '"):
            with self.subTest(value=value):
                if value is not None:
                    self.env_file.write_text('DJANGO_SECRET_KEY=' + value + '\n')
                result = self.run_script('refresh-local.sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('./configure-secret-key.sh', result.stderr)
                self.assertEqual(self.calls(), [])
                if value is None:
                    self.assertFalse(self.env_file.exists())
                else:
                    self.assertEqual(self.env_file.read_text(), 'DJANGO_SECRET_KEY=' + value + '\n')

    def test_backend_deploy_orders_removal_build_tests_migrations_and_start(self):
        self.env_file.write_text(self.original_env)
        database = self.create_database()
        result = self.run_script('refresh-local.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        fragments = ['rm --stop --force backend', 'build -t', 'manage.py test --noinput',
                     'makemigrations --check --dry-run', 'manage.py migrate --noinput', 'up -d']
        positions = [next(i for i, call in enumerate(calls) if fragment in call) for fragment in fragments]
        self.assertEqual(positions, sorted(positions))
        tests = next(call for call in calls if 'manage.py test --noinput' in call)
        self.assertIn('--network none', tests)
        self.assertIn('DJANGO_DB_PATH=:memory:', tests)
        self.assertNotIn('--volume', tests)
        self.assertNotIn('--env-file', tests)
        self.assertNotIn('frontend', '\n'.join(calls))
        self.assertEqual(database.read_bytes(), b'existing-database-must-survive')
        self.assertEqual(self.env_file.read_text(), self.original_env)

    def test_failed_build_or_tests_never_migrate_or_start_and_failed_migration_never_starts(self):
        self.env_file.write_text(self.original_env)
        database = self.create_database()
        for failure in ('build -t', 'manage.py test', 'makemigrations', 'manage.py migrate'):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                result = self.run_script('refresh-local.sh', failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(' up -d ' in call for call in self.calls()))
                if failure != 'manage.py migrate':
                    self.assertFalse(any('manage.py migrate ' in call for call in self.calls()))
                self.assertEqual(database.read_bytes(), b'existing-database-must-survive')
                self.assertEqual(self.env_file.read_text(), self.original_env)
                self.assertFalse((self.root / '.dockestra-operation.lock').exists())
