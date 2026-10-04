#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
    echo 'Erro: Docker não encontrado. Abra um terminal com Docker disponível.' >&2
    exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then
    echo 'Erro: execute este script em um terminal interativo para confirmar o e-mail e definir a senha.' >&2
    exit 1
fi
if [[ "$(docker inspect --format '{{.State.Running}}' rkd-dockestra-core-local-1 2>/dev/null || true)" != true ]]; then
    echo 'Erro: inicie o backend com bash scripts/refresh-local.sh antes de cadastrar o usuário.' >&2
    exit 1
fi

# Require the current command and its migrated credential table.
if ! docker exec rkd-dockestra-core-local-1 python manage.py shell -c \
    'from django.core.management import get_commands, load_command_class; from core.models import OperatorEmail, SmtpCredential; assert get_commands().get("createsuperuser") == "core"; command = load_command_class("core", "createsuperuser"); assert getattr(command, "stores_smtp_password_in_database", False) and getattr(command, "confirms_smtp_password", False); assert getattr(command, "stores_email_in_database", False); assert getattr(command, "verification_policy", None) == (16, 3, 300); SmtpCredential.objects.exists(); OperatorEmail.objects.exists()' \
    >/dev/null 2>&1; then
    echo 'Erro: atualize o backend com bash scripts/refresh-local.sh para aplicar o cadastro com e-mail e senha SMTP criptografados no SQLite.' >&2
    exit 1
fi

# Django creates RKD or recovers its password using the registered email.
# Git Bash/mintty may need winpty to provide Docker with an interactive TTY.
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        if command -v winpty >/dev/null 2>&1 && [[ "${TERM_PROGRAM:-}" == mintty || -z "${WT_SESSION:-}${TERM_PROGRAM:-}" ]]; then
            exec winpty docker exec -it rkd-dockestra-core-local-1 python manage.py createsuperuser --username RKD
        fi
        ;;
esac
exec docker exec -it rkd-dockestra-core-local-1 python manage.py createsuperuser --username RKD
