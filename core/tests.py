from django.test import Client, TestCase
from django.test import override_settings
from django.urls import reverse
import json
import os
from unittest.mock import patch
from subprocess import CompletedProcess
from io import BytesIO
from urllib.error import HTTPError

from django.core.cache import cache
from django.contrib.auth import get_user_model

from .docker_service import BUILD_CONTEXT
from .github_branches import parse_github_repository
from .models import Environment, Image, Project, Setup
from .token_cipher import decrypt_token, encrypt_token
from .turnstile import verify_turnstile


def sign_in_staff(client):
    user = get_user_model().objects.create_user(
        username='operator', password='test-password', is_staff=True,
    )
    client.force_login(user)
    return user


class HomeTests(TestCase):
    def test_home_identifies_application(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'name': 'Container Core', 'status': 'ready'})


class ApiAuthTests(TestCase):
    @override_settings(DEBUG=True, TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='')
    def test_api_requires_staff_login_and_login_uses_csrf(self):
        operator = get_user_model().objects.create_user(
            username='staff', password='secret-password', is_staff=True,
        )
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/api/projects/').status_code, 401)
        self.assertEqual(client.post('/api/auth/login/', {
            'username': 'staff', 'password': 'secret-password',
        }, content_type='application/json').status_code, 403)
        response = client.get('/api/auth/session/')
        self.assertEqual(response.json()['authenticated'], False)
        csrf = client.cookies['csrftoken'].value
        response = client.post('/api/auth/login/', {
            'username': 'staff', 'password': 'secret-password',
        }, content_type='application/json', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], operator.username)
        self.assertEqual(client.get('/api/projects/').status_code, 200)
        self.assertEqual(client.post('/api/auth/logout/', {}, content_type='application/json',
                                     HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code, 200)
        self.assertEqual(client.get('/api/projects/').status_code, 401)

    def test_non_staff_cannot_use_api(self):
        user = get_user_model().objects.create_user(username='viewer', password='test')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/api/projects/').status_code, 403)

    @override_settings(TURNSTILE_SITE_KEY='site-key', TURNSTILE_SECRET_KEY='secret-key')
    @patch('core.auth_views.verify_turnstile')
    def test_login_requires_valid_turnstile_when_configured(self, verify):
        get_user_model().objects.create_user(username='STAFF', password='password', is_staff=True)
        session = self.client.get('/api/auth/session/').json()
        self.assertTrue(session['turnstileRequired'])
        self.assertEqual(session['turnstileSiteKey'], 'site-key')
        self.assertNotIn('secret-key', str(session))
        credentials = {'username': 'STAFF', 'password': 'password'}
        verify.return_value = False
        self.assertEqual(self.client.post('/api/auth/login/', credentials, content_type='application/json').status_code, 403)
        verify.assert_called_with(None)
        self.assertEqual(self.client.post('/api/auth/login/', {**credentials, 'turnstileToken': 'bad'},
                                          content_type='application/json').status_code, 403)
        verify.return_value = True
        response = self.client.post('/api/auth/login/', {**credentials, 'turnstileToken': 'valid'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        verify.assert_called_with('valid')

    @override_settings(DEBUG=False, TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='')
    def test_production_login_fails_closed_without_turnstile_keys(self):
        self.assertTrue(self.client.get('/api/auth/session/').json()['turnstileRequired'])
        response = self.client.post('/api/auth/login/', {'username': 'STAFF', 'password': 'password'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 503)

    @override_settings(TURNSTILE_SECRET_KEY='secret-key', TURNSTILE_ALLOWED_HOSTNAMES=['example.com'])
    @patch('core.turnstile.urlopen')
    def test_turnstile_checks_action_and_hostname(self, urlopen_mock):
        def response_for(action, hostname):
            urlopen_mock.return_value.__enter__.return_value = BytesIO(json.dumps({
                'success': True, 'action': action, 'hostname': hostname,
            }).encode())

        response_for('other', 'example.com')
        self.assertFalse(verify_turnstile('token'))
        response_for('login', 'other.example.com')
        self.assertFalse(verify_turnstile('token'))
        response_for('login', 'example.com')
        self.assertTrue(verify_turnstile('token'))


class ContainerHierarchyTests(TestCase):
    def test_records_follow_project_environment_image_setup(self):
        project = Project.objects.create(code='PRJ', description='Project')
        environment = Environment.objects.create(
            code='DEV', project=project, description='Development'
        )
        image = Image.objects.create(
            code='IMG', environment=environment, description='Image',
            definition='FROM python:3.12'
        )
        setup = Setup.objects.create(
            code='STP', image=image, cpu='2', memory='4 GB'
        )

        self.assertIsNotNone(setup.id)
        self.assertIsNotNone(setup.created_date)
        self.assertIsNotNone(setup.last_modified_date)
        self.assertEqual(setup.image.environment.project_id, project.id)
        self.assertEqual(setup.created_by, 'ADMIN')
        self.assertEqual(setup.last_modified_by, 'ADMIN')
        self.assertFalse(Setup._meta.get_field('created_by').null)
        self.assertTrue(Setup._meta.get_field('last_modified_by').null)


class ContainerApiTests(TestCase):
    def setUp(self):
        sign_in_staff(self.client)
        self.project = Project.objects.create(code='PROJECT', description='Project')
        self.environment = Environment.objects.create(
            project=self.project, code='DEV', description='Development'
        )
        self.image = Image.objects.create(
            environment=self.environment, code='ALPINE', description='Alpine',
            definition='FROM alpine:3.20\nCMD ["sleep", "3600"]',
        )
        self.setup = Setup.objects.create(
            image=self.image, code='SERVER', cpu='0.5', memory='512 MB',
            port='8000:8000', volume='backend_data:/data'
        )

    def test_project_setup_list_only_contains_its_setups(self):
        other = Project.objects.create(code='OTHER', description='Other')
        other_environment = Environment.objects.create(
            project=other, code='PROD', description='Production'
        )
        other_image = Image.objects.create(
            environment=other_environment, code='OTHER_IMAGE',
            description='Other image', definition='FROM alpine'
        )
        Setup.objects.create(image=other_image, code='OTHER_SETUP', cpu='1', memory='1 GB')

        response = self.client.get(f'/api/projects/{self.project.pk}/setups/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            'id': self.setup.pk,
            'setup_code': 'SERVER',
            'image_code': 'ALPINE',
            'environment_code': 'DEV',
        }])
        self.assertEqual(self.client.get('/api/projects/99999/setups/').status_code, 404)

    @patch('core.docker_service.shutil.which', return_value=None)
    def test_docker_unavailable_returns_service_unavailable(self, _which):
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'docker_unavailable')

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_stopped_docker_daemon_returns_service_unavailable(self, _which, run):
        run.return_value = CompletedProcess([], 1, stderr='Cannot connect to the Docker daemon')
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'docker_unavailable')
        run.assert_called_once()

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_build_and_run_use_definition_and_resource_limits(self, _which, run):
        run.side_effect = [
            CompletedProcess([], 0, stdout='16 17179869184\n'),
            CompletedProcess([], 0, stdout='built\n'),
            CompletedProcess([], 0, stdout='container-id\n'),
        ]
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['container_id'], 'container-id')
        self.assertTrue(response.json()['container_name'].startswith('container-core-setup-'))
        build_args, build_kwargs = run.call_args_list[1]
        self.assertEqual(build_args[0][1:3], ['build', '--tag'])
        self.assertEqual(build_args[0][-3:], ['--file', '-', str(BUILD_CONTEXT)])
        self.assertEqual(build_kwargs['input'], self.image.definition)
        run_args = run.call_args_list[2].args[0]
        self.assertEqual(run_args[1], 'run')
        self.assertEqual(run_args[run_args.index('--cpus') + 1], '0.5')
        self.assertEqual(run_args[run_args.index('--memory') + 1], '536870912b')
        self.assertEqual(run_args[run_args.index('--publish') + 1], '127.0.0.1:8000:8000')
        self.assertEqual(run_args[run_args.index('--mount') + 1],
                         'type=volume,source=backend_data,target=/data')

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', side_effect=lambda name: name)
    def test_private_checkout_is_build_context_and_token_stays_out_of_docker(self, _which, run):
        self.image.repository = 'https://github.com/owner/private-repo'
        self.image.branch = 'release'
        self.image.isPrivate = 1
        self.image.token = encrypt_token('secret-token')
        self.image.save()
        run.side_effect = [
            CompletedProcess([], 0, stdout='16 17179869184\n'),
            CompletedProcess([], 0, stdout=''),
            CompletedProcess([], 0, stdout='built\n'),
            CompletedProcess([], 0, stdout='container-id\n'),
        ]
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 201, response.content)
        clone_args = run.call_args_list[1].args[0]
        clone_env = run.call_args_list[1].kwargs['env']
        self.assertEqual(clone_args[0], 'git')
        self.assertEqual(clone_args[clone_args.index('--branch') + 1], 'release')
        self.assertNotIn('secret-token', str(clone_args))
        self.assertIn('Authorization: Basic ', clone_env['GIT_CONFIG_VALUE_0'])
        build_args = run.call_args_list[2].args[0]
        self.assertEqual(build_args[0], 'docker')
        self.assertNotEqual(build_args[-1], str(BUILD_CONTEXT))
        self.assertEqual(build_args[-1], clone_args[-1])
        self.assertFalse(os.path.exists(build_args[-1]))
        self.assertNotIn('secret-token', str(build_args))
        self.assertNotIn('env', run.call_args_list[2].kwargs)

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_invalid_stored_mount_prevents_build(self, _which, run):
        self.setup.volume = '../host:/data'
        self.setup.save()
        run.return_value = CompletedProcess([], 0, stdout='16 17179869184\n')
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_configuration')
        run.assert_called_once()

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_invalid_memory_prevents_build(self, _which, run):
        self.setup.memory = 'five'
        self.setup.save()
        run.return_value = CompletedProcess([], 0, stdout='16 17179869184\n')
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_resources')
        run.assert_called_once()

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_cpu_over_docker_limit_prevents_build(self, _which, run):
        self.setup.cpu = '8.5'
        self.setup.save()
        run.return_value = CompletedProcess([], 0, stdout='8 17179869184\n')
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_resources')
        run.assert_called_once()

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_memory_over_docker_limit_prevents_build(self, _which, run):
        self.setup.memory = '5 GB'
        self.setup.save()
        run.return_value = CompletedProcess([], 0, stdout='8 4294967296\n')
        response = self.client.post(f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_resources')
        run.assert_called_once()

    @patch('core.cpu_resources.subprocess.run')
    @patch('core.cpu_resources.shutil.which', return_value='docker')
    def test_cpu_capacity_reports_docker_limit(self, _which, run):
        run.return_value = CompletedProcess([], 0, stdout='12\n')
        response = self.client.get('/api/system/cpu/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'max_cpu': 12, 'source': 'docker'})
        self.assertEqual(run.call_args.args[0][-1], '{{.NCPU}}')

    @patch('core.cpu_resources.os.cpu_count', return_value=16)
    @patch('core.cpu_resources.shutil.which', return_value=None)
    def test_cpu_capacity_uses_host_when_docker_is_offline(self, _which, _count):
        response = self.client.get('/api/system/cpu/')
        self.assertEqual(response.json(), {'max_cpu': 16, 'source': 'host'})

    @patch('core.memory_resources.subprocess.run')
    @patch('core.memory_resources.shutil.which', return_value='docker')
    def test_memory_capacity_reports_docker_limit(self, _which, run):
        run.return_value = CompletedProcess([], 0, stdout='8589934592\n')
        response = self.client.get('/api/system/memory/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'max_memory_bytes': 8589934592, 'source': 'docker'})
        self.assertEqual(run.call_args.args[0][-1], '{{.MemTotal}}')

    @patch('core.memory_resources.host_memory_bytes', return_value=17179869184)
    @patch('core.memory_resources.shutil.which', return_value=None)
    def test_memory_capacity_uses_host_when_docker_is_offline(self, _which, _host):
        response = self.client.get('/api/system/memory/')
        self.assertEqual(response.json(), {'max_memory_bytes': 17179869184, 'source': 'host'})


class CrudApiTests(TestCase):
    def setUp(self):
        self.operator = sign_in_staff(self.client)

    def create(self, url, code, **fields):
        response = self.client.post(
            url,
            {'code': code, **fields},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['created_by'], 'operator')
        self.assertEqual(response.json()['last_modified_by'], 'operator')
        return response.json()

    def test_crud_and_child_lists(self):
        project = self.create('/api/projects/', 'PRJ', description='Project')
        other = self.create('/api/projects/', 'OTHER', description='Other')
        environment_url = f"/api/projects/{project['id']}/environments/"
        environment = self.create(environment_url, 'DEV', description='Development')
        image_url = f"/api/environments/{environment['id']}/images/"
        image = self.create(image_url, 'IMG', description='Image', definition='FROM alpine')
        setup_url = f"/api/images/{image['id']}/setups/"
        setup = self.create(setup_url, 'STP', cpu='2', memory='4 GB',
                            port='8000:8000', volume='backend_data:/data')

        for url, child, parent_field, parent_id in (
            (environment_url, environment, 'project_id', project['id']),
            (image_url, image, 'environment_id', environment['id']),
            (setup_url, setup, 'image_id', image['id']),
        ):
            self.assertEqual(self.client.get(url).json(), [child])
            self.assertEqual(child[parent_field], parent_id)

        self.assertEqual(self.client.get(f"/api/projects/{other['id']}/environments/").json(), [])
        self.assertEqual(self.client.get('/api/projects/').status_code, 200)
        for resource, record in (
            ('projects', project), ('environments', environment),
            ('images', image), ('setups', setup),
        ):
            self.assertEqual(self.client.get(f"/api/{resource}/{record['id']}/").json(), record)

        response = self.client.put(
            f"/api/setups/{setup['id']}/",
            {'code': 'STP-2', 'cpu': '4', 'memory': '8 GB',
             'port': '127.0.0.1:9000:8000', 'volume': 'backend_data:/data'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['created_by'], 'operator')
        self.assertEqual(response.json()['last_modified_by'], 'operator')
        self.assertEqual(response.json()['cpu'], '4')
        self.assertEqual(response.json()['port'], '127.0.0.1:9000:8000')
        self.assertEqual(response.json()['volume'], 'backend_data:/data')

        for resource, record in (
            ('projects', project), ('environments', environment), ('images', image),
        ):
            response = self.client.delete(f"/api/{resource}/{record['id']}/")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['code'], 'associated_records')
            self.assertTrue({
                'projects': Project, 'environments': Environment, 'images': Image,
            }[resource].objects.filter(pk=record['id']).exists())

        for resource, record in (
            ('setups', setup), ('images', image),
            ('environments', environment), ('projects', project),
        ):
            response = self.client.delete(f"/api/{resource}/{record['id']}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'deleted': True})
        self.assertFalse(Environment.objects.exists())
        self.assertFalse(Image.objects.exists())
        self.assertFalse(Setup.objects.exists())
        self.assertTrue(Project.objects.filter(pk=other['id']).exists())

    def test_validation_and_csrf(self):
        response = self.client.post(
            '/api/projects/',
            {'code': '', 'description': 'Project'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('code', response.json()['errors'])
        response = self.client.post(
            '/api/projects/',
            {'code': 'INJECT', 'description': 'Project', 'created_by': 'another user'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/projects/999/environments/').status_code, 404)

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post('/api/projects/', {}, content_type='application/json').status_code, 403)
        client.get('/api/projects/')
        token = client.cookies['csrftoken'].value
        response = client.post(
            '/api/projects/',
            {'code': 'CSRF', 'description': 'Project'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_setup_rejects_invalid_port_and_host_bind_volume(self):
        project = Project.objects.create(code='PRJ', description='Project')
        environment = Environment.objects.create(
            project=project, code='DEV', description='Development'
        )
        image = Image.objects.create(
            environment=environment, code='IMG', description='Image', definition='FROM alpine'
        )
        response = self.client.post(
            f'/api/images/{image.pk}/setups/',
            {'code': 'BAD', 'cpu': '1', 'memory': '512 MB',
             'port': '99999:8000', 'volume': '../secret:/data'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('port', response.json()['errors'])
        self.assertIn('volume', response.json()['errors'])

        response = self.client.post(
            f'/api/images/{image.pk}/setups/',
            {'code': 'BAD_MOUNT', 'cpu': '1', 'memory': '512 MB',
             'volume': 'backend_data:/data,readonly'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('volume', response.json()['errors'])

    @patch('core.api.available_cpu_capacity', return_value=(4, 'docker'))
    def test_setup_cpu_cannot_exceed_current_capacity(self, _capacity):
        project = Project.objects.create(code='PRJ', description='Project')
        environment = Environment.objects.create(
            project=project, code='DEV', description='Development'
        )
        image = Image.objects.create(
            environment=environment, code='IMG', description='Image', definition='FROM alpine'
        )
        response = self.client.post(
            f'/api/images/{image.pk}/setups/',
            {'code': 'TOO_MUCH', 'cpu': '4.5', 'memory': '512 MB'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('cpu', response.json()['errors'])
        self.assertFalse(Setup.objects.filter(code='TOO_MUCH').exists())

        setup = Setup.objects.create(image=image, code='VALID', cpu='1', memory='512 MB')
        response = self.client.put(
            f'/api/setups/{setup.pk}/',
            {'code': 'VALID', 'cpu': '5', 'memory': '512 MB', 'port': '', 'volume': ''},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('cpu', response.json()['errors'])
        setup.refresh_from_db()
        self.assertEqual(setup.cpu, '1')

    @patch('core.api.available_memory_capacity', return_value=(2 * 1024**3, 'docker'))
    def test_setup_memory_cannot_exceed_current_capacity(self, _capacity):
        project = Project.objects.create(code='PRJ', description='Project')
        environment = Environment.objects.create(
            project=project, code='DEV', description='Development'
        )
        image = Image.objects.create(
            environment=environment, code='IMG', description='Image', definition='FROM alpine'
        )
        response = self.client.post(
            f'/api/images/{image.pk}/setups/',
            {'code': 'TOO_MUCH', 'cpu': '1', 'memory': '2.5 GB'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('memory', response.json()['errors'])
        self.assertFalse(Setup.objects.filter(code='TOO_MUCH').exists())

        setup = Setup.objects.create(image=image, code='VALID', cpu='1', memory='1 GB')
        response = self.client.put(
            f'/api/setups/{setup.pk}/',
            {'code': 'VALID', 'cpu': '1', 'memory': '3 GB', 'port': '', 'volume': ''},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('memory', response.json()['errors'])
        setup.refresh_from_db()
        self.assertEqual(setup.memory, '1 GB')


class GithubBranchTests(TestCase):
    def setUp(self):
        cache.clear()
        sign_in_staff(self.client)

    def test_rejects_non_github_urls_without_requesting_them(self):
        for repository in (
            'http://github.com/owner/repo',
            'https://github.com.evil.test/owner/repo',
            'https://github.com/owner/repo/tree/main',
            'https://github.com/owner/repo?token=secret',
        ):
            with self.subTest(repository=repository):
                response = self.client.get('/api/github/branches/', {'repository': repository})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'invalid_repository')
        self.assertEqual(parse_github_repository('https://github.com/owner/repo.git'), ('owner', 'repo'))

    @patch('core.github_branches.urlopen')
    def test_lists_all_pages_and_caches_result(self, urlopen):
        first_page = [{'name': f'feature/{number}'} for number in range(100)]
        second_page = [{'name': 'main'}]
        urlopen.side_effect = [
            BytesIO(json.dumps(first_page).encode()),
            BytesIO(json.dumps(second_page).encode()),
        ]
        url = '/api/github/branches/'
        repository = 'https://github.com/madrijkaard/rkd-survivor-engine'
        response = self.client.get(url, {'repository': repository})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['branches'], [*(item['name'] for item in first_page), 'main'])
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn('page=2', urlopen.call_args.args[0].full_url)
        self.assertEqual(self.client.get(url, {'repository': repository}).json(), response.json())
        self.assertEqual(urlopen.call_count, 2)

    @patch('core.github_branches.urlopen')
    def test_reports_missing_repository(self, urlopen):
        urlopen.side_effect = HTTPError('https://api.github.com/', 404, 'Not Found', None, None)
        response = self.client.get('/api/github/branches/', {
            'repository': 'https://github.com/owner/missing',
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'repository_not_found')

    def test_image_requires_branch_when_repository_is_given(self):
        project = Project.objects.create(code='PRJ', description='Project')
        environment = Environment.objects.create(project=project, code='DEV', description='Dev')
        url = f'/api/environments/{environment.pk}/images/'
        response = self.client.post(url, {
            'code': 'IMG', 'description': 'Image', 'definition': 'FROM alpine',
            'repository': 'https://github.com/owner/repo', 'branch': '',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('branch', response.json()['errors'])
        response = self.client.post(url, {
            'code': 'IMG', 'description': 'Image', 'definition': 'FROM alpine',
            'repository': 'https://github.com/owner/repo', 'branch': 'main',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['repository'], 'https://github.com/owner/repo')
        self.assertEqual(response.json()['branch'], 'main')

    @patch('core.github_branches.urlopen')
    def test_private_token_is_encrypted_not_serialized_and_can_list_branches(self, urlopen):
        project = Project.objects.create(code='PRJ', description='Project')
        environment = Environment.objects.create(project=project, code='DEV', description='Dev')
        url = f'/api/environments/{environment.pk}/images/'
        payload = {
            'code': 'IMG', 'description': 'Image', 'definition': 'FROM alpine',
            'repository': 'https://github.com/owner/private-repo', 'branch': 'main',
            'isPrivate': 1, 'token': 'secret-token',
        }
        created = self.client.post(url, payload, content_type='application/json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertNotIn('token', created.json())
        self.assertEqual(created.json()['hasToken'], True)
        image = Image.objects.get(pk=created.json()['id'])
        self.assertNotIn('secret-token', image.token)
        self.assertEqual(decrypt_token(image.token), 'secret-token')
        urlopen.return_value = BytesIO(b'[{"name":"main"}]')
        response = self.client.post('/api/github/branches/', {
            'repository': image.repository, 'image_id': image.pk,
        }, content_type='application/json')
        self.assertEqual(response.json(), {'branches': ['main']})
        self.assertEqual(urlopen.call_args.args[0].get_header('Authorization'), 'Bearer secret-token')

        payload['token'] = ''
        updated = self.client.put(f'/api/images/{image.pk}/', payload, content_type='application/json')
        self.assertEqual(updated.status_code, 200, updated.content)
        image.refresh_from_db()
        self.assertEqual(decrypt_token(image.token), 'secret-token')
        payload['isPrivate'] = 0
        self.assertEqual(self.client.put(f'/api/images/{image.pk}/', payload,
                                         content_type='application/json').status_code, 200)
        image.refresh_from_db()
        self.assertEqual(image.token, '')
