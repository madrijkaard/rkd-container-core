"""
URL configuration for rkd_dockestra_core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path

from core.views import home
from core.auth_views import session, sign_in, sign_out
from core.api import collection, cpu_capacity, detail, github_branches, github_description, instance_detail, memory_capacity, project_setups, setup_container, setup_instances

urlpatterns = [
    path('', home, name='home'),
    path('api/auth/session/', session, name='api-session'),
    path('api/auth/login/', sign_in, name='api-login'),
    path('api/auth/logout/', sign_out, name='api-logout'),
    path('api/system/cpu/', cpu_capacity, name='cpu-capacity'),
    path('api/system/memory/', memory_capacity, name='memory-capacity'),
    path('api/github/branches/', github_branches, name='github-branches'),
    path('api/github/description/', github_description, name='github-description'),
    path('api/projects/', collection, {'resource': 'projects'}, name='projects'),
    path('api/projects/<int:pk>/', detail, {'resource': 'projects'}, name='project-detail'),
    path('api/projects/<int:parent_id>/environments/', collection, {'resource': 'environments'}, name='project-environments'),
    path('api/projects/<int:project_id>/setups/', project_setups, name='project-setups'),
    path('api/environments/<int:pk>/', detail, {'resource': 'environments'}, name='environment-detail'),
    path('api/environments/<int:parent_id>/images/', collection, {'resource': 'images'}, name='environment-images'),
    path('api/images/<int:pk>/', detail, {'resource': 'images'}, name='image-detail'),
    path('api/images/<int:parent_id>/setups/', collection, {'resource': 'setups'}, name='image-setups'),
    path('api/setups/<int:pk>/', detail, {'resource': 'setups'}, name='setup-detail'),
    path('api/setups/<int:pk>/containers/', setup_container, name='setup-container'),
    path('api/setups/<int:pk>/instances/', setup_instances, name='setup-instances'),
    path('api/instances/<int:pk>/', instance_detail, name='instance-detail'),
    path('admin/', admin.site.urls),
]
