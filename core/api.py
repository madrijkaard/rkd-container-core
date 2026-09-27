"""JSON CRUD endpoints for the Container Core hierarchy."""

import json

from django.db.models import ProtectedError
from django.forms import modelform_factory
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from .cpu_resources import available_cpu_capacity
from .docker_service import ContainerCreationError, cpu_limit, create_container, memory_limit
from .github_branches import GithubBranchError, list_github_branches, parse_github_repository
from .memory_resources import available_memory_capacity
from .models import Environment, Image, Project, Setup
from .token_cipher import TokenDecryptionError, decrypt_token, encrypt_token


RESOURCES = {
    'projects': (Project, ('description',), None),
    'environments': (Environment, ('description',), 'project'),
    'images': (Image, ('description', 'definition', 'repository', 'branch', 'isPrivate'), 'environment'),
    'setups': (Setup, ('cpu', 'memory', 'port', 'volume'), 'image'),
}


def validate_setup_cpu(form):
    if 'cpu' in form.errors:
        return
    maximum, _ = available_cpu_capacity()
    try:
        cpu_limit(form.cleaned_data['cpu'], maximum)
    except ContainerCreationError as error:
        form.add_error('cpu', str(error))


def validate_setup_memory(form):
    if 'memory' in form.errors:
        return
    maximum, _ = available_memory_capacity()
    if maximum < 6 * 1024**2:
        form.add_error('memory', 'Memory capacity is unavailable.')
        return
    try:
        memory_limit(form.cleaned_data['memory'], maximum)
    except ContainerCreationError as error:
        form.add_error('memory', str(error))


def cpu_capacity(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    maximum, source = available_cpu_capacity()
    return JsonResponse({'max_cpu': maximum, 'source': source})


def memory_capacity(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    maximum, source = available_memory_capacity()
    if maximum < 6 * 1024**2:
        return JsonResponse({'error': 'Memory capacity is unavailable.'}, status=503)
    return JsonResponse({'max_memory_bytes': maximum, 'source': source})


def github_branches(request):
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    if request.method == 'GET':
        repository = request.GET.get('repository', '')
        token = None
    else:
        payload, error = parse_payload(request, ('repository', 'token', 'image_id'))
        if error:
            return error
        repository = payload.get('repository', '')
        token = payload.get('token') or None
        image_id = payload.get('image_id')
        if token is not None and (not isinstance(token, str) or image_id is not None):
            return JsonResponse({'error': 'Informe o token ou o ID da imagem.'}, status=400)
        if image_id is not None:
            if not isinstance(image_id, int) or isinstance(image_id, bool):
                return JsonResponse({'error': 'ID da imagem inválido.'}, status=400)
            image = Image.objects.filter(pk=image_id, repository=repository, isPrivate=1).first()
            if image is None or not image.token:
                return JsonResponse({'error': 'Imagem privada ou token não encontrado.'}, status=404)
            try:
                token = decrypt_token(image.token)
            except TokenDecryptionError as error:
                return JsonResponse({'code': 'token_unavailable', 'error': str(error)}, status=503)
    try:
        branches = list_github_branches(repository, token)
    except GithubBranchError as error:
        return JsonResponse({'code': error.code, 'error': str(error)}, status=error.status)
    return JsonResponse({'branches': branches})


def validate_image_repository(form, existing=None):
    if any(field in form.errors for field in ('repository', 'branch', 'isPrivate', 'token')):
        return
    repository = form.cleaned_data['repository']
    branch = form.cleaned_data['branch']
    is_private = form.cleaned_data['isPrivate']
    token = form.cleaned_data['token']
    if repository:
        try:
            parse_github_repository(repository)
        except GithubBranchError as error:
            form.add_error('repository', str(error))
        if not branch:
            form.add_error('branch', 'Selecione uma branch.')
    elif branch:
        form.add_error('branch', 'Informe o repositório antes da branch.')
    if is_private:
        if not repository:
            form.add_error('repository', 'Informe um repositório privado.')
        if not token and not (existing and existing['isPrivate'] and existing['token']
                              and existing['repository'] == repository):
            form.add_error('token', 'Informe um token para o repositório privado.')
    elif token:
        form.add_error('token', 'Marque o repositório como privado para informar um token.')


def serialize(record, resource):
    _, fields, parent_field = RESOURCES[resource]
    data = {
        'id': record.pk,
        'code': record.code,
        'created_date': record.created_date,
        'created_by': record.created_by,
        'last_modified_date': record.last_modified_date,
        'last_modified_by': record.last_modified_by,
    }
    data.update({field: getattr(record, field) for field in fields})
    if parent_field:
        data[f'{parent_field}_id'] = getattr(record, f'{parent_field}_id')
    if resource == 'images':
        data['hasToken'] = bool(record.token)
    return data


def parse_payload(request, allowed_fields):
    if request.content_type != 'application/json':
        return None, JsonResponse({'error': 'Send JSON in the request body.'}, status=415)
    try:
        payload = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, JsonResponse({'error': 'Invalid JSON.'}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({'error': 'The request body must be a JSON object.'}, status=400)
    unexpected = sorted(set(payload) - set(allowed_fields))
    if unexpected:
        return None, JsonResponse({'error': f"Unsupported fields: {', '.join(unexpected)}."}, status=400)
    return payload, None


@ensure_csrf_cookie
def collection(request, resource, parent_id=None):
    model, fields, parent_field = RESOURCES[resource]
    parent = None
    if parent_field:
        parent_model = model._meta.get_field(parent_field).remote_field.model
        parent = parent_model.objects.filter(pk=parent_id).first()
        if parent is None:
            return JsonResponse({'error': 'Parent record not found.'}, status=404)

    if request.method == 'GET':
        records = model.objects.all()
        if parent_field:
            records = records.filter(**{parent_field: parent})
        return JsonResponse([serialize(record, resource) for record in records.order_by('id')], safe=False)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['GET', 'POST'])

    editable = ('code', *fields, *(['token'] if resource == 'images' else []))
    payload, error = parse_payload(request, editable)
    if error:
        return error
    if resource == 'images':
        payload.setdefault('isPrivate', 0)
        payload.setdefault('token', '')
    form = modelform_factory(model, fields=editable)(payload)
    form.is_valid()
    if resource == 'images':
        validate_image_repository(form)
    if resource == 'setups':
        validate_setup_cpu(form)
        validate_setup_memory(form)
    if form.errors:
        return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
    record = form.save(commit=False)
    if resource == 'images':
        record.token = encrypt_token(form.cleaned_data['token']) if record.isPrivate else ''
    record.created_by = request.user.get_username()
    record.last_modified_by = request.user.get_username()
    if parent_field:
        setattr(record, parent_field, parent)
    record.save()
    return JsonResponse(serialize(record, resource), status=201)


@ensure_csrf_cookie
def detail(request, resource, pk):
    model, fields, _ = RESOURCES[resource]
    record = model.objects.filter(pk=pk).first()
    if record is None:
        return JsonResponse({'error': 'Record not found.'}, status=404)

    if request.method == 'GET':
        return JsonResponse(serialize(record, resource))

    if request.method == 'DELETE':
        try:
            record.delete()
        except ProtectedError:
            return JsonResponse({
                'code': 'associated_records',
                'error': 'This record has associated records and cannot be deleted.',
            }, status=409)
        return JsonResponse({'deleted': True})

    if request.method != 'PUT':
        return HttpResponseNotAllowed(['GET', 'PUT', 'DELETE'])

    editable = ('code', *fields, *(['token'] if resource == 'images' else []))
    payload, error = parse_payload(request, editable)
    if error:
        return error
    if resource == 'images':
        existing = {'token': record.token, 'repository': record.repository, 'isPrivate': record.isPrivate}
        payload.setdefault('isPrivate', record.isPrivate)
        payload.setdefault('token', '')
    form = modelform_factory(model, fields=editable)(payload, instance=record)
    form.is_valid()
    if resource == 'images':
        validate_image_repository(form, existing)
    if resource == 'setups':
        validate_setup_cpu(form)
        validate_setup_memory(form)
    if form.errors:
        return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
    record = form.save(commit=False)
    if resource == 'images':
        record.token = (encrypt_token(form.cleaned_data['token']) if form.cleaned_data['token']
                        else existing['token'] if record.isPrivate else '')
    record.last_modified_by = request.user.get_username()
    record.save()
    return JsonResponse(serialize(record, resource))


@ensure_csrf_cookie
def project_setups(request, project_id):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    if not Project.objects.filter(pk=project_id).exists():
        return JsonResponse({'error': 'Project not found.'}, status=404)
    setups = Setup.objects.filter(
        image__environment__project_id=project_id
    ).select_related('image__environment').order_by('id')
    return JsonResponse([
        {
            'id': setup.id,
            'setup_code': setup.code,
            'image_code': setup.image.code,
            'environment_code': setup.image.environment.code,
        }
        for setup in setups
    ], safe=False)


def setup_container(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    setup = Setup.objects.select_related('image').filter(pk=pk).first()
    if setup is None:
        return JsonResponse({'error': 'Setup not found.'}, status=404)
    try:
        result = create_container(setup)
    except ContainerCreationError as error:
        return JsonResponse({'code': error.code, 'error': str(error)}, status=error.status)
    return JsonResponse(result, status=201)
