import ipaddress
import re
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError
from django.db import models


def port_binding(value):
    """Return a Docker publish value, bound to loopback unless an IP is supplied."""
    if not value:
        return None
    parts = value.split(':')
    if len(parts) == 2:
        address = '127.0.0.1'
        host_port, container_port = parts
    elif len(parts) == 3:
        address, host_port, container_port = parts
        try:
            address = str(ipaddress.IPv4Address(address))
        except ipaddress.AddressValueError:
            raise ValidationError('Use um endereço IPv4 válido.', code='invalid_port') from None
    else:
        raise ValidationError('Use host:container ou IP:host:container.', code='invalid_port')

    if not all(part.isascii() and part.isdecimal() and 1 <= int(part) <= 65535
               for part in (host_port, container_port)):
        raise ValidationError('As portas devem estar entre 1 e 65535.', code='invalid_port')
    return f'{address}:{int(host_port)}:{int(container_port)}'


def volume_mount(value):
    """Return a named Docker volume mount without accepting host bind paths."""
    if not value:
        return None
    parts = value.split(':')
    if len(parts) != 2:
        raise ValidationError('Use nome:/caminho/no/container.', code='invalid_volume')
    name, target = parts
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValidationError('Informe um nome de volume válido.', code='invalid_volume')
    path = PurePosixPath(target)
    if (not path.is_absolute() or target == '/' or str(path) != target
            or '..' in path.parts or ',' in target or '\x00' in target):
        raise ValidationError('Informe um caminho absoluto no container.', code='invalid_volume')
    return f'type=volume,source={name},target={target}'


class BaseRecord(models.Model):
    code = models.CharField(
        max_length=100,
        verbose_name="Code",
        help_text="Record identification code",
    )
    created_date = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creation date",
        help_text="Timestamp when the record was created",
    )
    created_by = models.CharField(
        max_length=256,
        default="ADMIN",
        verbose_name="Created by",
        help_text="User who created the record",
    )
    last_modified_date = models.DateTimeField(
        auto_now=True,
        verbose_name="Last modified date",
        help_text="Timestamp of the last record modification",
    )
    last_modified_by = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        default="ADMIN",
        verbose_name="Modified by",
        help_text="User who last modified the record",
    )

    class Meta:
        abstract = True


class Project(BaseRecord):
    description = models.CharField(max_length=256)

    class Meta:
        db_table = "project"


class Environment(BaseRecord):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    description = models.CharField(max_length=256)

    class Meta:
        db_table = "environment"


class Image(BaseRecord):
    environment = models.ForeignKey(Environment, on_delete=models.PROTECT)
    description = models.CharField(max_length=256)
    definition = models.TextField()
    repository = models.CharField(max_length=256, blank=True, default='')
    branch = models.CharField(max_length=256, blank=True, default='')
    isPrivate = models.PositiveSmallIntegerField(choices=((0, 'Não'), (1, 'Sim')), default=0)
    token = models.TextField(blank=True, default='')

    class Meta:
        db_table = "image"


class Setup(BaseRecord):
    image = models.ForeignKey(Image, on_delete=models.PROTECT)
    cpu = models.CharField(max_length=15)
    memory = models.CharField(max_length=15)
    port = models.CharField(max_length=50, blank=True, default='', validators=[port_binding])
    volume = models.CharField(max_length=255, blank=True, default='', validators=[volume_mount])

    class Meta:
        db_table = "setup"
