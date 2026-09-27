"""Require a staff session for every container API operation."""

from django.http import JsonResponse


class StaffApiMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/') and not request.path.startswith('/api/auth/'):
            if not request.user.is_authenticated:
                return JsonResponse({'code': 'authentication_required', 'error': 'Faça login para usar a API.'}, status=401)
            if not request.user.is_staff:
                return JsonResponse({'code': 'permission_denied', 'error': 'Acesso restrito a operadores.'}, status=403)
        return self.get_response(request)
