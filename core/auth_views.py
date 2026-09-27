"""Session login for trusted operators of the container API."""

import json

from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from .turnstile import verify_turnstile


def turnstile_required():
    return not settings.DEBUG or bool(settings.TURNSTILE_SITE_KEY or settings.TURNSTILE_SECRET_KEY)


@ensure_csrf_cookie
def session(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    user = request.user
    return JsonResponse({
        'authenticated': bool(user.is_authenticated and user.is_staff),
        'username': user.get_username() if user.is_authenticated and user.is_staff else '',
        'turnstileRequired': turnstile_required(),
        'turnstileSiteKey': settings.TURNSTILE_SITE_KEY if settings.TURNSTILE_SECRET_KEY else '',
    })


def sign_in(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        payload = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'error': 'Dados de login inválidos.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Dados de login inválidos.'}, status=400)
    username = payload.get('username')
    password = payload.get('password')
    if not isinstance(username, str) or not isinstance(password, str):
        return JsonResponse({'error': 'Dados de login inválidos.'}, status=400)
    if turnstile_required():
        if not settings.TURNSTILE_SITE_KEY or not settings.TURNSTILE_SECRET_KEY:
            return JsonResponse({'error': 'A proteção do login não está configurada.'}, status=503)
        verified = verify_turnstile(payload.get('turnstileToken'))
        if verified is None:
            return JsonResponse({'error': 'Não foi possível validar a verificação. Tente novamente.'}, status=503)
        if not verified:
            return JsonResponse({'error': 'Verificação de segurança inválida ou expirada. Tente novamente.'}, status=403)
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_staff:
        return JsonResponse({'error': 'Usuário ou senha inválidos, ou acesso não autorizado.'}, status=403)
    login(request, user)
    return JsonResponse({'authenticated': True, 'username': user.get_username()})


def sign_out(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    logout(request)
    return JsonResponse({'authenticated': False})
