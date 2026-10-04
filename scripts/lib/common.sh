#!/usr/bin/env bash
# Read dotenv values without executing the file or displaying credentials.
env_has_value() {
    awk -v name="$1" '
        { sub(/\r$/, "") }
        $0 ~ "^[[:space:]]*" name "[[:space:]]*=" {
            value = $0
            sub("^[[:space:]]*" name "[[:space:]]*=[[:space:]]*", "", value)
            sub(/[[:space:]]+$/, "", value)
            if (value ~ /^".*"$/ || value ~ /^\047.*\047$/) value = substr(value, 2, length(value)-2)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            present = value != "" && value !~ /^#/
        }
        END { exit !present }
    ' "$env_file"
}

validate_env_file() {
    if [[ -L "$env_file" || ( -e "$env_file" && ! -f "$env_file" ) ]]; then
        echo 'Erro: .env deve ser um arquivo comum na raiz do backend.' >&2
        return 1
    fi
    if [[ -f "$env_file" ]] && (( $(awk '/^[[:space:]]*DJANGO_SECRET_KEY[[:space:]]*=/ { n++ } END { print n+0 }' "$env_file") > 1 )); then
        echo 'Erro: mantenha apenas uma entrada DJANGO_SECRET_KEY no .env.' >&2
        return 1
    fi
}

acquire_project_lock() {
    operation_lock="$project_dir/.dockestra-operation.lock"
    if ! mkdir -- "$operation_lock" 2>/dev/null; then
        echo 'Erro: outra operação de chave/deploy está em andamento. Se foi interrompida, remova .dockestra-operation.lock somente após encerrá-la.' >&2
        return 1
    fi
}

cleanup_operation() {
    if [[ -n "${temporary_file:-}" ]]; then rm -f -- "$temporary_file"; fi
    if [[ -n "${operation_lock:-}" ]]; then rmdir -- "$operation_lock"; fi
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        echo 'Erro: Docker inacessível. Inicie o Docker e confira as permissões do terminal.' >&2
        return 1
    fi
}
