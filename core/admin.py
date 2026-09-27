from django.contrib import admin

from .models import Environment, Image, Project, Setup


admin.site.register((Project, Environment, Setup))


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
