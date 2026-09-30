from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F

from .models import Environment, Image, Project, Setup


admin.site.register((Project, Environment))


@admin.register(Setup)
class SetupAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or not obj.instances.exists()
        )

    def save_model(self, request, obj, form, change):
        if not change:
            return super().save_model(request, obj, form, change)
        with transaction.atomic():
            # Serialize with replica reservation, including a creation that
            # begins after the admin form was opened.
            Setup.objects.filter(pk=obj.pk).update(last_instance_number=F('last_instance_number'))
            if obj.instances.exists():
                raise PermissionDenied('Exclua todas as instâncias antes de editar este setup.')
            obj.refresh_from_db(fields=['last_instance_number'])
            return super().save_model(request, obj, form, change)


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    exclude = ('token', 'isPrivate')
    readonly_fields = ('private_status', 'token_status')

    @admin.display(description='Repositório privado')
    def private_status(self, obj):
        return 'Sim' if obj and obj.isPrivate else 'Não'

    @admin.display(description='Token')
    def token_status(self, obj):
        return 'Configurado' if obj and obj.token else 'Não configurado'

# Register your models here.
