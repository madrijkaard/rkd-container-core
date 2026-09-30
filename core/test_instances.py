from subprocess import CompletedProcess
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .docker_service import ContainerCreationError
from .models import Environment, Image, Instance, Project, Setup


class InstanceApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='operator', is_staff=True)
        self.client.force_login(self.user)
        project = Project.objects.create(code='PROJECT', description='Project')
        environment = Environment.objects.create(project=project, code='DEV', description='Dev')
        image = Image.objects.create(environment=environment, code='IMAGE', description='Image', definition='FROM alpine')
        self.setup = Setup.objects.create(image=image, code='SERVER', cpu='1', memory='512 MB')
        self.url = f'/api/setups/{self.setup.pk}/instances/'

    def create(self, mock, url=None):
        mock.side_effect = lambda setup, name, number: {'container_id': f'id-{name}', 'container_name': name}
        response = self.client.post(url or self.url)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    @patch('core.api.create_container')
    def test_instances_are_persisted_and_listed_only_under_their_setup(self, create):
        first = self.create(create)
        second = self.create(create)
        other = Setup.objects.create(image=self.setup.image, code='OTHER', cpu='1', memory='512 MB')
        third = self.create(create, f'/api/setups/{other.pk}/instances/')
        self.assertEqual([first['code'], second['code'], third['code']],
                         ['SERVER-replica-1', 'SERVER-replica-2', 'OTHER-replica-1'])
        self.assertEqual(self.client.get(self.url).json(), [first, second])
        self.assertEqual(first['setup_id'], self.setup.pk)
        self.assertEqual(first['created_by'], self.user.username)
        self.assertEqual(Instance.objects.get(pk=first['id']).container_id, first['container_id'])

    @patch('core.api.remove_container')
    @patch('core.api.create_container')
    def test_deleting_reuses_the_lowest_available_replica_number(self, create, remove):
        first = self.create(create)
        second = self.create(create)
        self.assertEqual(self.client.delete(f"/api/instances/{first['id']}/").status_code, 200)
        remove.assert_called_once_with(first['container_id'])
        replacement = self.create(create)
        self.assertEqual(second['code'], 'SERVER-replica-2')
        self.assertEqual(replacement['code'], 'SERVER-replica-1')
        self.assertEqual(replacement['number'], 1)
        self.assertEqual([row['number'] for row in self.client.get(self.url).json()], [1, 2])

    @patch('core.api.create_container')
    def test_existing_replica_three_does_not_force_the_next_one_to_four(self, create):
        self.setup.last_instance_number = 3
        self.setup.save(update_fields=('last_instance_number',))
        Instance.objects.create(setup=self.setup, code='SERVER-replica-3', number=3,
                                container_id='existing-docker-id')
        replacement = self.create(create)
        self.assertEqual(replacement['number'], 1)
        self.assertEqual(replacement['code'], 'SERVER-replica-1')

    @patch('core.api.create_container')
    def test_failed_creation_does_not_leave_an_instance_row(self, create):
        create.side_effect = ContainerCreationError('docker_build_failed', 'Build failed.', 502)
        self.assertEqual(self.client.post(self.url).status_code, 502)
        self.assertEqual(self.client.get(self.url).json(), [])
        self.assertFalse(Instance.objects.exists())
        self.assertEqual(self.create(create)['number'], 1)

    @patch('core.api.create_container')
    def test_pending_instance_keeps_its_replica_number_reserved(self, create):
        Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1)
        self.assertEqual(self.create(create)['number'], 2)

    @patch('core.api.create_container')
    def test_existing_container_endpoint_also_tracks_an_instance(self, create):
        instance = self.create(create, f'/api/setups/{self.setup.pk}/containers/')
        self.assertEqual(self.client.get(self.url).json(), [instance])

    @patch('core.api.create_container')
    def test_setup_cannot_be_deleted_while_it_has_instances(self, create):
        self.create(create)
        response = self.client.delete(f'/api/setups/{self.setup.pk}/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'associated_records')

    @patch('core.api.remove_container')
    @patch('core.api.create_container')
    def test_setup_cannot_be_edited_until_its_instance_is_deleted(self, create, _remove):
        url = f'/api/setups/{self.setup.pk}/'
        payload = {'code': 'RENAMED', 'cpu': '1', 'memory': '512 MB', 'port': '', 'volume': ''}
        self.assertFalse(self.client.get(url).json()['hasInstances'])
        instance = self.create(create)
        self.assertTrue(self.client.get(url).json()['hasInstances'])

        response = self.client.put(url, payload, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'setup_has_instances')
        self.setup.refresh_from_db()
        self.assertEqual(self.setup.code, 'SERVER')
        self.assertEqual(self.setup.cpu, '1')

        self.assertEqual(self.client.delete(f"/api/instances/{instance['id']}/").status_code, 200)
        self.assertFalse(self.client.get(url).json()['hasInstances'])
        response = self.client.put(url, payload, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['code'], 'RENAMED')

    def test_pending_instance_also_blocks_setup_edits(self):
        Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1)
        response = self.client.put(
            f'/api/setups/{self.setup.pk}/',
            {'code': 'RENAMED', 'cpu': '1', 'memory': '512 MB', 'port': '', 'volume': ''},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'setup_has_instances')
        self.assertTrue(self.client.get(f'/api/setups/{self.setup.pk}/').json()['hasInstances'])

    def test_django_admin_cannot_edit_setup_with_instances(self):
        administrator = get_user_model().objects.create_superuser(
            username='administrator', password='test-password'
        )
        self.client.force_login(administrator)
        Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1,
                                container_id='docker-id')
        response = self.client.post(
            f'/admin/core/setup/{self.setup.pk}/change/',
            {'code': 'RENAMED', 'image': self.setup.image_id,
             'cpu': '1', 'memory': '512 MB', 'port': '', 'volume': '', '_save': 'Save'},
        )
        self.assertEqual(response.status_code, 403)
        self.setup.refresh_from_db()
        self.assertEqual(self.setup.code, 'SERVER')

    def test_django_admin_can_edit_setup_without_instances(self):
        administrator = get_user_model().objects.create_superuser(
            username='administrator', password='test-password'
        )
        self.client.force_login(administrator)
        response = self.client.post(
            f'/admin/core/setup/{self.setup.pk}/change/',
            {'code': 'RENAMED', 'created_by': 'ADMIN', 'last_modified_by': 'ADMIN',
             'image': self.setup.image_id, 'cpu': '1', 'memory': '512 MB',
             'port': '', 'volume': '', '_save': 'Save'},
        )
        self.assertEqual(response.status_code, 302, response.content)
        self.setup.refresh_from_db()
        self.assertEqual(self.setup.code, 'RENAMED')

    @patch('core.api.validate_setup_memory')
    def test_edit_does_not_reset_a_replica_counter_updated_during_validation(self, validate_memory):
        validate_memory.side_effect = lambda _form: Setup.objects.filter(pk=self.setup.pk).update(
            last_instance_number=5
        )
        response = self.client.put(
            f'/api/setups/{self.setup.pk}/',
            {'code': 'RENAMED', 'cpu': '1', 'memory': '512 MB', 'port': '', 'volume': ''},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.setup.refresh_from_db()
        self.assertEqual(self.setup.last_instance_number, 5)

    @patch('core.api.remove_container')
    @patch('core.api.create_container')
    def test_delete_failure_keeps_the_instance_for_retry(self, create, remove):
        instance = self.create(create)
        remove.side_effect = ContainerCreationError('container_delete_failed', 'Failed.', 502)
        response = self.client.delete(f"/api/instances/{instance['id']}/")
        self.assertEqual(response.status_code, 502)
        self.assertTrue(Instance.objects.filter(pk=instance['id']).exists())

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_delete_removes_the_recorded_container_without_removing_volumes(self, which, run):
        instance = Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1, container_id='docker-id')
        run.return_value = CompletedProcess([], 0, stdout='docker-id')
        self.assertEqual(self.client.delete(f'/api/instances/{instance.pk}/').status_code, 200)
        self.assertEqual(run.call_args.args[0], ['docker', 'container', 'rm', '--force', 'docker-id'])
        self.assertFalse(Instance.objects.exists())

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_a_container_removed_externally_can_be_unregistered(self, which, run):
        instance = Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1, container_id='missing-id')
        run.return_value = CompletedProcess([], 1, stderr='Error response from daemon: No such container: missing-id')
        self.assertEqual(self.client.delete(f'/api/instances/{instance.pk}/').status_code, 200)
        self.assertFalse(Instance.objects.exists())

    @patch('core.api.create_container')
    def test_duplicate_setup_codes_report_a_name_conflict_before_docker(self, create):
        self.create(create)
        other = Setup.objects.create(image=self.setup.image, code=self.setup.code, cpu='1', memory='512 MB')
        create.reset_mock()
        response = self.client.post(f'/api/setups/{other.pk}/instances/')
        self.assertEqual(response.status_code, 409)
        create.assert_not_called()
        other.refresh_from_db()
        self.assertEqual(other.last_instance_number, 0)

    def test_pending_creation_is_hidden_and_cannot_be_deleted(self):
        instance = Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1)
        self.assertEqual(self.client.get(self.url).json(), [])
        self.assertEqual(self.client.delete(f'/api/instances/{instance.pk}/').status_code, 409)

    def test_instance_endpoints_require_staff_and_csrf(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url).status_code, 401)
        self.assertEqual(anonymous.delete('/api/instances/1/').status_code, 401)
        viewer = get_user_model().objects.create_user(username='viewer')
        anonymous.force_login(viewer)
        self.assertEqual(anonymous.get(self.url).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.url).status_code, 403)
        self.assertEqual(csrf.delete('/api/instances/1/').status_code, 403)

    def test_missing_records_and_unsupported_methods(self):
        self.assertEqual(self.client.get('/api/setups/999/instances/').status_code, 404)
        self.assertEqual(self.client.post('/api/setups/999/instances/').status_code, 404)
        self.assertEqual(self.client.delete('/api/instances/999/').status_code, 404)
        self.assertEqual(self.client.put(self.url).status_code, 405)
        self.assertEqual(self.client.put('/api/instances/999/').status_code, 405)

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_replicas_increment_the_host_port_and_persist_the_mapping(self, which, run):
        self.setup.port = '0.0.0.0:9000:8000'
        self.setup.save()
        run.side_effect = [
            CompletedProcess([], 0, stdout='4 8589934592'),
            CompletedProcess([], 0, stdout='built'),
            CompletedProcess([], 0, stdout='docker-id-1'),
            CompletedProcess([], 0, stdout='4 8589934592'),
            CompletedProcess([], 0, stdout='built'),
            CompletedProcess([], 0, stdout='docker-id-2'),
        ]
        first = self.client.post(self.url)
        second = self.client.post(self.url)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()['port'], '0.0.0.0:9000:8000')
        self.assertEqual(second.json()['port'], '0.0.0.0:9001:8000')
        for call, port in ((run.call_args_list[2], '0.0.0.0:9000:8000'),
                           (run.call_args_list[5], '0.0.0.0:9001:8000')):
            args = call.args[0]
            self.assertEqual(args[args.index('--publish') + 1], port)
        self.assertEqual(list(self.setup.instances.values_list('port', flat=True)),
                         ['0.0.0.0:9000:8000', '0.0.0.0:9001:8000'])

    @patch('core.api.remove_container')
    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_reused_replica_number_reuses_its_host_port(self, _which, run, _remove):
        self.setup.port = '4173:4173'
        self.setup.save()
        run.side_effect = [
            CompletedProcess([], 0, stdout='4 8589934592'),
            CompletedProcess([], 0, stdout='built'),
            CompletedProcess([], 0, stdout='docker-id-1'),
            CompletedProcess([], 0, stdout='4 8589934592'),
            CompletedProcess([], 0, stdout='built'),
            CompletedProcess([], 0, stdout='docker-id-2'),
        ]
        first = self.client.post(self.url).json()
        self.assertEqual(self.client.delete(f"/api/instances/{first['id']}/").status_code, 200)
        replacement = self.client.post(self.url).json()
        self.assertEqual(replacement['number'], 1)
        self.assertEqual(replacement['port'], '127.0.0.1:4173:4173')
        self.assertIn('--publish', run.call_args_list[-1].args[0])
        self.assertEqual(run.call_args_list[-1].args[0][run.call_args_list[-1].args[0].index('--publish') + 1],
                         '127.0.0.1:4173:4173')

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_exhausted_port_range_rejects_creation_before_build(self, which, run):
        self.setup.port = '65535:8000'
        self.setup.save()
        Instance.objects.create(setup=self.setup, code='SERVER-replica-1', number=1,
                                container_id='docker-id-1')
        run.return_value = CompletedProcess([], 0, stdout='4 8589934592')
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_configuration')
        self.assertIn('65535', response.json()['error'])
        run.assert_called_once()
        self.assertEqual(Instance.objects.count(), 1)

    @patch('core.docker_service.subprocess.run')
    @patch('core.docker_service.shutil.which', return_value='docker')
    def test_occupied_port_is_reported_without_persisting_a_successful_instance(self, which, run):
        self.setup.port = '8000:8000'
        self.setup.save()
        run.side_effect = [
            CompletedProcess([], 0, stdout='4 8589934592'),
            CompletedProcess([], 0, stdout='built'),
            CompletedProcess([], 1, stderr='port is already allocated'),
        ]
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'port_unavailable')
        self.assertIn('127.0.0.1:8000:8000', response.json()['error'])
        self.assertFalse(Instance.objects.exists())
