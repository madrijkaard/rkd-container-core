#!/usr/bin/env bash
set -euo pipefail

if (( $# )); then echo 'Uso: ./refresh-local.sh (sem opções)' >&2; exit 1; fi
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
env_file="$project_dir/.env"
source "$project_dir/scripts/lib/common.sh"
validate_env_file
if [[ ! -f "$env_file" ]] || ! env_has_value DJANGO_SECRET_KEY; then
    echo 'Erro: falta DJANGO_SECRET_KEY no .env. Execute primeiro ./configure-secret-key.sh dentro de scripts/.' >&2
    exit 1
fi
require_docker
docker compose version >/dev/null
cd "$project_dir"
compose=(docker compose --env-file "$env_file" -f "$project_dir/docker-compose.local.yml")
"${compose[@]}" config --quiet
acquire_project_lock
trap cleanup_operation EXIT
trap 'echo "Deploy do backend interrompido. Confira o erro acima; a chave não foi alterada e nenhum reset foi executado. Migrations já aplicadas permanecem no banco." >&2' ERR

echo 'Removendo somente o container anterior do backend local (sem remover volumes)...'
"${compose[@]}" rm --stop --force backend
echo 'Construindo a imagem atual do backend...'
docker build -t rkd-core-local-backend:latest "$project_dir"

# Disposable tests: no real .env, persisted database or host Docker socket.
test_env=(--env DJANGO_SECRET_KEY=dockestra-isolated-test-key-not-for-deployment
    --env DJANGO_DEBUG=True --env DJANGO_DB_PATH=:memory: --env DJANGO_ALLOWED_HOSTS=localhost,testserver)
echo 'Executando testes em banco isolado...'
docker run --rm --network none "${test_env[@]}" --entrypoint python rkd-core-local-backend:latest manage.py test --noinput
echo 'Conferindo se as alterações dos models possuem migrations versionadas...'
docker run --rm --network none "${test_env[@]}" --entrypoint python rkd-core-local-backend:latest manage.py makemigrations --check --dry-run
echo 'Aplicando migrations pendentes no banco persistido...'
"${compose[@]}" run --rm --no-deps -T backend python manage.py migrate --noinput
echo 'Iniciando o novo backend local...'
"${compose[@]}" up -d --no-build --force-recreate --wait --wait-timeout 180 backend
echo 'Backend local pronto. DJANGO_SECRET_KEY e dados preservados.'
