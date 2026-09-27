#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
    echo 'Uso: bash configure-secret-key.sh [--generate-only]'
    echo 'Sem opções: salva/reutiliza a chave no .env e inicia/recria o backend.'
    echo '--generate-only: prepara o .env sem executar Docker.'
}

mode=apply
if (( $# > 1 )); then
    usage >&2
    exit 1
fi
case "${1:-}" in
    '') ;;
    --generate-only) mode=generate ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"
env_file="$script_dir/.env"
temporary_file=''
trap 'if [[ -n "$temporary_file" ]]; then rm -f -- "$temporary_file"; fi' EXIT

if [[ -L "$env_file" || ( -e "$env_file" && ! -f "$env_file" ) ]]; then
    echo 'Erro: .env deve ser um arquivo comum no diretório do backend.' >&2
    exit 1
fi
if [[ ! -f "$env_file" ]]; then
    cp -- "$script_dir/.env.example" "$env_file"
fi
chmod 600 "$env_file"

# Read dotenv values without executing the file or printing credentials.
has_value() {
    awk -v name="$1" '
        { sub(/\r$/, "") }
        $0 ~ "^[[:space:]]*" name "[[:space:]]*=" {
            value = $0
            sub("^[[:space:]]*" name "[[:space:]]*=[[:space:]]*", "", value)
            sub(/[[:space:]]+$/, "", value)
            present = value != "" && value != "\"\"" && value != "\047\047" && value !~ /^#/
        }
        END { exit !present }
    ' "$env_file"
}

key_count="$(awk '/^[[:space:]]*DJANGO_SECRET_KEY[[:space:]]*=/ { count++ } END { print count+0 }' "$env_file")"
if (( key_count > 1 )); then
    echo 'Erro: mantenha apenas uma entrada DJANGO_SECRET_KEY no .env.' >&2
    exit 1
fi

if has_value DJANGO_SECRET_KEY; then
    echo 'DJANGO_SECRET_KEY existente preservada.'
else
    if [[ -e "$script_dir/volumes/sqlite/container_core.sqlite3" ]]; then
        echo 'Erro: já existe um SQLite persistido, mas falta a chave no .env.' >&2
        echo 'Restaure a DJANGO_SECRET_KEY original para manter os tokens salvos legíveis.' >&2
        exit 1
    fi
    if ! command -v openssl >/dev/null 2>&1; then
        echo 'Erro: instale OpenSSL (sudo apt install openssl).' >&2
        exit 1
    fi
    generated_key="$(openssl rand -hex 48)"
    temporary_file="$(mktemp "$script_dir/.env.tmp.XXXXXX")"
    awk '!/^[[:space:]]*DJANGO_SECRET_KEY[[:space:]]*=/' "$env_file" > "$temporary_file"
    printf 'DJANGO_SECRET_KEY=%s\n' "$generated_key" >> "$temporary_file"
    mv -- "$temporary_file" "$env_file"
    temporary_file=''
    unset generated_key
    echo 'Chave aleatória gerada e salva no .env (permissão 600).'
fi

if [[ "$mode" == generate ]]; then
    echo 'Preencha as duas chaves Turnstile no .env e execute o script sem --generate-only para aplicar.'
    exit 0
fi

for variable in TURNSTILE_SITE_KEY TURNSTILE_SECRET_KEY; do
    if ! has_value "$variable"; then
        echo "Erro: preencha $variable no .env e execute novamente. A DJANGO_SECRET_KEY já está salva." >&2
        exit 1
    fi
done
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo 'Erro: instale Docker Engine e o plugin Docker Compose na VPS.' >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo 'Erro: Docker inacessível. Confira o serviço e execute o script com sudo se necessário.' >&2
    exit 1
fi

compose=(docker compose --env-file "$env_file" -f "$script_dir/docker-compose.yml")
"${compose[@]}" config --quiet
echo 'Iniciando/recriando o backend com as variáveis do .env; aguarde a verificação de saúde.'
if ! "${compose[@]}" up -d --build --force-recreate --wait --wait-timeout 180 backend; then
    echo 'Erro ao iniciar o backend. A chave foi preservada; confira docker compose logs backend.' >&2
    exit 1
fi
"${compose[@]}" exec -T backend python -c 'import os; assert os.environ.get("DJANGO_SECRET_KEY"), "DJANGO_SECRET_KEY ausente"; print("DJANGO_SECRET_KEY configurada no container do backend.")'
