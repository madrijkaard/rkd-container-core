#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
    echo 'Uso: ./configure-secret-key.sh [--force] [--help|-h]'
    echo 'Sem opções: gera DJANGO_SECRET_KEY no .env da raiz, ou preserva a chave existente. Não faz deploy.'
    echo '--force: gera outra chave e APAGA todos os bancos SQLite deste checkout (raiz e volumes/sqlite), inclusive legados.'
    echo '         Remove os containers que usam esses bancos antes do reset. Exige Docker disponível.'
    echo '         Encerre também qualquer Django executado diretamente no host antes de usar --force.'
    echo '         O banco vazio será criado pelas migrations no próximo deploy. Usuários precisarão ser cadastrados novamente.'
}

force=false
while (( $# )); do
    case "$1" in
        --force) force=true ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Opção inválida: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
env_file="$project_dir/.env"
source "$project_dir/scripts/lib/common.sh"
validate_env_file
acquire_project_lock
trap cleanup_operation EXIT

if [[ "$force" == false && -f "$env_file" ]] && env_has_value DJANGO_SECRET_KEY; then
    echo 'DJANGO_SECRET_KEY existente preservada. Nenhum deploy foi executado.'
    exit 0
fi

# Only these explicit directories belong to this reset. Never follow symlinks.
for directory in "$project_dir/volumes" "$project_dir/volumes/sqlite"; do
    if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
        echo "Erro: caminho de banco inválido: $directory" >&2
        exit 1
    fi
done
collect_database_files() {
    shopt -s nullglob
    database_files=("$project_dir"/*.sqlite3 "$project_dir"/*.sqlite3-wal "$project_dir"/*.sqlite3-shm "$project_dir"/*.sqlite3-journal
        "$project_dir/volumes/sqlite"/*.sqlite3 "$project_dir/volumes/sqlite"/*.sqlite3-wal
        "$project_dir/volumes/sqlite"/*.sqlite3-shm "$project_dir/volumes/sqlite"/*.sqlite3-journal)
    shopt -u nullglob
    for file in "${database_files[@]}"; do
        if [[ -L "$file" || ! -f "$file" ]]; then
            echo "Erro: arquivo de banco inválido: $file" >&2
            return 1
        fi
    done
}
collect_database_files
if [[ "$force" == true && -f "$env_file" ]] && env_has_value DJANGO_DB_PATH; then
    echo 'Erro: DJANGO_DB_PATH personalizado no .env. Remova essa configuração após tratar o banco externo; --force só apaga bancos na raiz e em volumes/sqlite deste checkout.' >&2
    exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
    echo 'Erro: OpenSSL não encontrado. Use Git Bash com OpenSSL ou instale OpenSSL no Linux.' >&2
    exit 1
fi

# Prepare a replacement before touching the database; never display the key.
generated_key="$(openssl rand -hex 48)"
[[ "$generated_key" =~ ^[0-9a-f]{96}$ ]] || { echo 'Erro ao gerar a chave.' >&2; exit 1; }
temporary_file="$(mktemp "$project_dir/.env.tmp.XXXXXX")"
source_env="$env_file"
if [[ ! -f "$source_env" ]]; then source_env="$project_dir/.env.example"; fi
awk '!/^[[:space:]]*DJANGO_SECRET_KEY[[:space:]]*=/' "$source_env" > "$temporary_file"
printf '\nDJANGO_SECRET_KEY=%s\n' "$generated_key" >> "$temporary_file"
unset generated_key

if [[ "$force" == true ]]; then
    require_docker
    # Find containers using either database directory or an individual database.
    # Only matching bind mounts are removed; named application volumes survive.
    normalize_path() {
        local path="$1"
        case "$(uname -s)" in
            MINGW*|MSYS*|CYGWIN*) path="$(cygpath -au "$path")" ;;
        esac
        # Resolve existing paths physically, including Windows short names.
        if [[ -d "$path" ]]; then
            path="$(cd -- "$path" && pwd -P)"
        elif [[ -f "$path" ]]; then
            path="$(cd -- "$(dirname -- "$path")" && pwd -P)/$(basename -- "$path")"
        fi
        case "$(uname -s)" in
            MINGW*|MSYS*|CYGWIN*) cygpath -am "$path" | tr '[:upper:]' '[:lower:]' ;;
            *) printf '%s\n' "${path%/}" ;;
        esac
    }
    root_path="$(normalize_path "$project_dir")"
    sqlite_path="$(normalize_path "$project_dir/volumes/sqlite")"
    containers=()
    all_ids="$(docker container ls -aq | tr -d '\r')"
    for id in $all_ids; do
        sources="$(docker inspect --format '{{range .Mounts}}{{if eq .Type "bind"}}{{println .Source}}{{end}}{{end}}' "$id")"
        while IFS= read -r source_path; do
            source_path="${source_path%$'\r'}"
            [[ -n "$source_path" ]] || continue
            source_path="$(normalize_path "$source_path")"
            if [[ "$source_path" == "$root_path" || "$source_path" == "$sqlite_path" || "$source_path" == "$root_path/volumes" ]]; then
                containers+=("$id"); break
            fi
            for file in "${database_files[@]}"; do
                if [[ "$source_path" == "$(normalize_path "$file")" ]]; then
                    containers+=("$id"); break 2
                fi
            done
        done <<< "$sources"
    done
    echo 'Reset solicitado: removendo containers que usam os bancos deste checkout...'
    if (( ${#containers[@]} )); then docker container rm --force "${containers[@]}"; fi
    # Recheck after shutdown to include any final WAL/journal files.
    collect_database_files
    if (( ${#database_files[@]} )); then rm -- "${database_files[@]}"; fi
    echo 'Bancos SQLite deste checkout apagados. Os diretórios e volumes foram preservados.'
fi

# Git Bash on Windows may reject replacing an existing file with mv.
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) cp -- "$temporary_file" "$env_file"; rm -- "$temporary_file" ;;
    *) mv -- "$temporary_file" "$env_file" ;;
esac
temporary_file=''
chmod 600 "$env_file"
echo 'DJANGO_SECRET_KEY gerada e salva no .env. Execute ./refresh-local.sh em scripts/ para fazer o deploy local.'
