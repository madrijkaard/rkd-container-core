<p align="center"><img src="docs/assets/dockestra-logo.png" alt="Dockestra" width="560"></p>

# rkd-dockestra-core

Python and Django backend for the Dockestra platform. The Django project is `rkd_dockestra_core`, and its application is `core`.

## Database

Na primeira inicialização após a renomeação, o container copia o SQLite persistido com o nome anterior para o novo arquivo antes de aplicar as migrações. O arquivo antigo permanece como cópia de segurança. Na execução direta de `manage.py`, o Django usa o banco antigo caso ele exista e o novo ainda não exista. Tokens GitHub já criptografados continuam legíveis com a mesma `DJANGO_SECRET_KEY`.

SQLite stores data in `rkd_dockestra_core.sqlite3` at the repository root by default. Set `DJANGO_DB_PATH` to use another location; the container uses `/data/rkd_dockestra_core.sqlite3` so a Docker volume can persist it. The `core` migrations create the `project`, `environment`, `image`, `setup`, and `instance` tables. The hierarchy is `Project → Environment → Image → Setup → Instance`; each instance has a protected `setup_id` foreign key. Migration `0003` renames the previous table to `setup` without discarding its records. Their relationships use `project_id`, `environment_id`, and `image_id`. Each table has an automatically generated `id` primary key.

## Requirements

- Python 3.12 or later
- Git CLI for images linked to a GitHub repository
- Docker CLI and a reachable daemon to create containers

## Run locally

`DJANGO_SECRET_KEY` é obrigatória em todos os ambientes, inclusive com `DJANGO_DEBUG=True`. Para executar Django diretamente, configure a variável no terminal antes dos comandos `manage.py`: no Git Bash, `export DJANGO_SECRET_KEY='<sua chave estável>'`; no PowerShell, `$env:DJANGO_SECRET_KEY='<sua chave estável>'`. Use a mesma chave em cada execução para preservar os tokens criptografados. O Django não carrega `.env` automaticamente na execução direta; no Docker, o Compose passa esse arquivo ao container.

Tokens salvos anteriormente com a antiga chave padrão de desenvolvimento precisam ser cadastrados novamente ao configurar uma nova chave. Os demais registros do banco permanecem armazenados.

From Git Bash on Windows:

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe manage.py migrate
./.venv/Scripts/python.exe manage.py createsuperuser
./.venv/Scripts/python.exe manage.py runserver
```

From PowerShell, use `.\.venv\Scripts\python.exe` in place of `./.venv/Scripts/python.exe`. If `.venv` already exists, skip the virtual environment creation step.

The initial JSON response is available at `http://127.0.0.1:8000/`.

## Testar localmente com Docker

Para reconstruir e recriar somente o backend local após alterações no código, execute `bash refresh-local.sh` nesta raiz (também funciona a partir de outro diretório). O script preserva `.env` e `volumes/sqlite/`, aplica as migrations na inicialização e aguarda o backend ficar saudável. Para atualizar backend, frontend e Nginx em um único comando, execute `bash refresh-local.sh` na raiz de `rkd-dockestra-web`.

O container do backend se chama `rkd-dockestra-core-local-1` neste modo e `rkd-dockestra-core-1` no Compose da VPS. Os nomes são fixados com `container_name`: o sufixo `1` faz parte do nome e não aumenta automaticamente. Essa configuração permite uma instância do serviço por ambiente. Os comandos Compose continuam usando o serviço `backend`, e a comunicação interna usa o alias `rkd-backend`.

Use Docker Desktop com **containers Linux** e Docker Compose. Você pode gerar a chave pelo **Git Bash no Windows**, com OpenSSL disponível (`openssl version`). Também pode executar o script em Ubuntu/WSL com a [integração do Docker Desktop](https://docs.docker.com/desktop/features/wsl/) habilitada. Para melhor compatibilidade de permissões e volumes, prefira os clones no sistema de arquivos Linux do WSL. Os comandos seguintes são executados na raiz do backend; depois da geração, os comandos `docker compose` também podem ser executados no PowerShell.

A ordem é: **gerar/preservar a chave no `.env` → criar o backend → criar o usuário → iniciar frontend e Nginx**. Nenhum container do backend precisa existir para gerar a chave:

```bash
bash configure-secret-key.sh --generate-only
docker build -t rkd-core-local-backend:latest .
docker compose -f docker-compose.local.yml up -d --no-build --wait
docker compose -f docker-compose.local.yml exec backend python manage.py createsuperuser --username RKD
```

O primeiro comando só prepara o `.env` e não chama Docker. O segundo constrói a imagem, e o terceiro cria o container já com `DJANGO_SECRET_KEY`, usando `env_file: .env`. No Windows, separar build e subida evita um possível erro de renomeação de arquivo temporário de metadados do Compose/Bake (`Identificador inválido`). Para gerar/preservar a chave e iniciar o backend em uma única execução, use `bash configure-secret-key.sh --local` e depois crie o usuário; no Git Bash, o script também separa build e subida. Se o usuário já existir, pule o comando `createsuperuser`.

Em seguida, na raiz do frontend:

```bash
cd ../rkd-dockestra-web
docker build -t rkd-web-local-frontend:latest .
docker build -t rkd-web-local-nginx:latest -f Dockerfile.proxy.local .
docker compose -f docker-compose.local.yml up -d --no-build
```

Abra **http://localhost:8080/**. O Compose local usa a rede `rkd-local-network`, criada pelo backend, e mantém a porta 8000 interna. Ele desativa Turnstile apenas nesse modo de desenvolvimento e permite os cookies de login em HTTP; não exige um widget, certificado HTTPS ou `.env` do frontend. A chave Django continua obrigatória. Os containers gerenciados pelo sistema serão criados no Docker local.

O SQLite local fica em `volumes/sqlite/rkd_dockestra_core.local.sqlite3` e é preservado ao recriar o backend. Ele é separado do arquivo usado pelo Compose da VPS e do banco de desenvolvimento na raiz; por isso os usuários e registros desses outros bancos não aparecem automaticamente. O script preserva a chave existente e recusa gerar outra se encontrar um SQLite persistido sem a chave original.

A pasta `volumes/` contém apenas dados gerados e é ignorada pelo Git e pelo build Docker. Não é necessário restaurá-la pelo Git antes de iniciar: o Compose cria o diretório montado e o backend cria as tabelas automaticamente. Apagar essa pasta remove o banco, incluindo usuários e cadastros; na próxima inicialização será criado um banco vazio.

Para consultar o backend local:

```bash
docker compose -f docker-compose.local.yml ps
docker compose -f docker-compose.local.yml logs -f backend
```

Use `-f docker-compose.local.yml` em todos os comandos locais. Executar o script sem `--local` ou o Compose sem `-f` seleciona a configuração da VPS.

### Erro de certificado HTTPS durante o build

Se o build apresentar `curl: (60) SSL certificate problem: self-signed certificate in certificate chain`, um proxy ou antivírus pode estar assinando as conexões com uma CA que é confiável no Windows, mas está ausente na imagem Linux. Identifique o emissor da cadeia HTTPS e use apenas sua CA pública já confiável no host. Consulte o procedimento de [certificados CA no Docker](https://docs.docker.com/engine/network/ca-certs/).

No Windows, abra `certmgr.msc`, acesse **Autoridades de Certificação Raiz Confiáveis → Certificados** e exporte a CA identificada como **X.509 codificado em Base64**, sem chave privada. Salve o resultado com extensão `.crt` em `docker/certificates/` deste repositório. Não use o certificado de servidor de `download.docker.com` como CA e não desative a verificação SSL. Se o certificado estiver no armazenamento da máquina em vez do usuário, use `certlm.msc`.

O Dockerfile instala os `.crt` antes dos downloads HTTPS e o pip usa o bundle completo do sistema. Os certificados locais são ignorados pelo Git; a pasta fica vazia em um clone novo, até você adicionar a CA necessária naquele ambiente. Para reconstruir localmente, execute `docker build -t rkd-core-local-backend:latest .` e depois `docker compose -f docker-compose.local.yml up -d --no-build --wait`. O backend preserva o `.env` e o SQLite durante essa operação.

Se a mesma inspeção HTTPS atingir o frontend, copie a mesma CA para `rkd-dockestra-web/docker/certificates/`, conforme o README daquele projeto. Na VPS, só adicione uma CA extra se a rede da VPS também exigir essa confiança.

## Deploy on the Ubuntu VPS with Docker Compose

Este repositório tem seu próprio `docker-compose.yml`. Ele inicia Django e cria a rede `rkd-network`, compartilhada com o Compose do frontend. A imagem do backend inclui Git e o cliente Docker; ela usa o Docker Engine **da VPS** pelo socket `/var/run/docker.sock`, sem iniciar outro daemon. A porta 8000 permanece interna à rede Docker. O Nginx do frontend encaminha `/api/`, `/admin/` e `/static/` para o backend.

### Pré-requisitos na VPS

1. Ubuntu com acesso SSH e um usuário autorizado a executar Docker (`sudo docker ...` nos exemplos abaixo). Instale [Docker Engine, CLI e Buildx pelo repositório oficial](https://docs.docker.com/engine/install/ubuntu/) e o [plugin Docker Compose](https://docs.docker.com/compose/install/linux/) caso ainda não estejam instalados. Não é necessário instalar Python, Node.js ou Nginx diretamente no Ubuntu.
2. Git e OpenSSL instalados na VPS (`sudo apt update && sudo apt install -y git openssl`), acesso para clonar os dois repositórios e saída HTTPS para baixar imagens/dependências e consultar GitHub e Cloudflare. Se algum dos repositórios da aplicação for privado, configure sua autenticação Git para o clone; o token informado na tela de imagens serve para os repositórios de código das imagens, não para clonar estes dois projetos.
3. O domínio `sinan-pro.com` apontando para o IPv4 público da VPS. Se houver registro AAAA, ele também deve apontar para um IPv6 funcional da VPS; caso contrário, remova esse registro. Libere TCP 80 e 443 no firewall da VPS e no painel da Hostinger e deixe essas portas disponíveis para o Nginx do frontend. O Certbot usa a porta 80 para obter o certificado HTTPS. Não é preciso mudar os nameservers do domínio para a Cloudflare apenas para usar Turnstile.
4. Um [widget Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/get-started/widget-management/dashboard/) criado com o hostname **`sinan-pro.com`** (sem `https://` e sem caminho). Guarde a **Site key** e a **Secret key**. O modo Managed pode ser usado. A Secret key só deve ser configurada no backend. O login de produção exige ambas as chaves.

Confirme o Docker antes de iniciar a aplicação:

```bash
sudo systemctl is-active docker
sudo docker version
sudo docker compose version
sudo docker buildx version
```

### Instalação inicial do backend

Os comandos abaixo são executados na VPS, via SSH. Clone os dois projetos no mesmo diretório pai; inicie o backend antes do frontend, pois ele cria `rkd-network`:

```bash
git clone https://github.com/madrijkaard/rkd-dockestra-core.git
git clone https://github.com/madrijkaard/rkd-dockestra-web.git
cd rkd-dockestra-core
bash configure-secret-key.sh --generate-only
nano .env
sudo bash configure-secret-key.sh
sudo docker compose ps
```

O script cria o `.env` a partir de `.env.example` se necessário e gera `DJANGO_SECRET_KEY` automaticamente. Preencha as **duas chaves Turnstile reais** no `.env`, sem os sinais `< >` dos exemplos:

```dotenv
DJANGO_SECRET_KEY=<valor_gerado_pelo_script_nao_substitua>
TURNSTILE_SITE_KEY=<site_key_do_widget>
TURNSTILE_SECRET_KEY=<secret_key_do_widget>
```

Mantenha `DJANGO_SECRET_KEY` estável: ela protege sessões e criptografa os tokens GitHub salvos. O Compose já define `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS=sinan-pro.com`, `TURNSTILE_ALLOWED_HOSTNAMES=sinan-pro.com` e `DJANGO_DB_PATH=/data/rkd_dockestra_core.sqlite3`. Não é necessário criar essas variáveis no ambiente global do Ubuntu: o `.env` é passado ao container. Não coloque chaves no frontend, no Dockerfile, no Git ou em uma imagem Docker.

Se a variável estiver ausente, vazia ou contiver apenas espaços, o backend encerra a inicialização e escreve no terminal/log uma mensagem indicando que falta configurar `DJANGO_SECRET_KEY`. Não existe chave padrão de desenvolvimento. O build usa uma chave aleatória temporária apenas para `collectstatic`; ela não é configurada como variável de ambiente da imagem nem substitui a chave obrigatória em execução.

### Script para configurar DJANGO_SECRET_KEY

`configure-secret-key.sh`, na raiz deste projeto, gera 48 bytes aleatórios com OpenSSL e grava sua representação hexadecimal (96 caracteres) em `.env`, com permissão `600`. A chave não é exibida na saída. Ao executar sem opções, o script usa o `env_file` do Compose para criar/recriar o backend com a variável, aguarda o container ficar saudável e confirma a presença de `DJANGO_SECRET_KEY` sem revelar o valor. A recriação causa uma breve interrupção do backend e preserva o SQLite montado.

```bash
bash configure-secret-key.sh --generate-only  # somente prepara o .env
sudo bash configure-secret-key.sh            # aplica no container
bash configure-secret-key.sh --local         # aplica no container local
```

Rodar o script novamente preserva uma chave já configurada e os demais valores do `.env`. Se houver SQLite em `volumes/sqlite/` e a chave estiver ausente, ele interrompe a geração e pede a restauração da chave original. Não apague ou substitua uma chave usada por tokens existentes. O script exige as duas chaves Turnstile antes de iniciar o backend em produção.

Uma variável exportada em uma sessão `docker exec` só afeta aquela sessão. Para que o processo Django receba a variável e ela continue configurada em containers futuros, o script persiste o valor no `.env` e recria o serviço pelo Compose. Consulte a documentação de [variáveis no container](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/) e de [recriação com Compose](https://docs.docker.com/reference/cli/docker/compose/up/).

### Persistência e primeiro usuário

O SQLite fica em **`rkd-dockestra-core/volumes/sqlite/rkd_dockestra_core.sqlite3` na VPS**, montado como `/data/rkd_dockestra_core.sqlite3` no container. O backend cria a pasta e executa as migrations ao iniciar. O banco local de desenvolvimento e seus usuários não são enviados pelo Git. Para criar o primeiro operador no banco novo:

```bash
sudo docker compose exec backend python manage.py createsuperuser
sudo docker compose logs --tail=100 backend
```

Depois, entre em `../rkd-dockestra-web` e siga a seção de implantação do README do frontend para configurar `ACME_EMAIL`, iniciar Nginx e Certbot e acessar `https://sinan-pro.com/`.

### Manutenção e cuidados

Faça backup de `volumes/sqlite/rkd_dockestra_core.sqlite3` **e** da `DJANGO_SECRET_KEY`, armazenando a chave separadamente. Se trocar a chave sem regravar os tokens, os tokens privados já salvos não poderão ser descriptografados. Ao atualizar este projeto, execute `git pull` e `sudo docker compose up -d --build` neste diretório. Consulte `sudo docker compose ps` e `sudo docker compose logs --tail=100 backend` se o backend não ficar saudável.

O socket Docker dá ao backend controle efetivo sobre o host. Conceda acesso de operador (`staff`) somente a pessoas confiáveis e não exponha a porta 8000 publicamente.

## Backend image definition

The repository's `Dockerfile` deploys Dockestra Core itself. For a container managed by the application, save Dockerfile text in an `image` record's `definition` field, then create a setup linked to that image. Without a GitHub repository, the API builds with the backend repository root as its context. With a repository, it checks out the selected branch into a temporary directory and uses that source tree as the context. The Dockerfile can use `COPY . /app` to include the selected branch. The backend `.dockerignore` applies only when the backend repository is the context; a linked repository can supply its own `.dockerignore`.

The image form reads the repository's GitHub About description through `GET /api/github/description/` for public repositories or `POST /api/github/description/` with a token or saved image ID for private repositories. The response contains only `description`; tokens remain on the backend. An empty About field returns an empty string so the operator can enter a description manually.

For a backend image, use Python 3.12, install `requirements.txt`, include the application source, run migrations at startup, and serve `rkd_dockestra_core.wsgi:application` with Gunicorn. Set `DJANGO_DB_PATH` to a path such as `/data/rkd_dockestra_core.sqlite3`. The setup determines whether to mount a named volume at `/data` and publish a port. The image needs the Docker CLI only if it will itself call the container creation endpoint; the CLI also needs a reachable daemon and appropriate credentials at runtime.

The container API applies the setup's CPU, memory, optional port mapping, and optional named volume mount when starting a container. Replica N uses `host_port + N - 1`, retaining the configured bind IP and container port. For example, `8000:8000` gives replicas 1, 2, and 3 host ports 8000, 8001, and 8002. The mapping is stored in `instance.port`. A deleted replica number and its port offset can be reused by the next instance. An occupied port causes a creation error; ports exceeding 65535 are rejected before building. Replicas share the setup's named volume. It does not pass environment variables or Docker daemon credentials to the container. API access requires a Django staff account. Only trusted operators should receive staff access because they can define Dockerfiles and start containers.

## CRUD API

| Resource | List and create | Retrieve, update, and delete |
| --- | --- | --- |
| Projects | `GET/POST /api/projects/` | `GET/PUT/DELETE /api/projects/<id>/` |
| Environments in a project | `GET/POST /api/projects/<id>/environments/` | `GET/PUT/DELETE /api/environments/<id>/` |
| Images in an environment | `GET/POST /api/environments/<id>/images/` | `GET/PUT/DELETE /api/images/<id>/` |
| Setups using an image | `GET/POST /api/images/<id>/setups/` | `GET/PUT/DELETE /api/setups/<id>/` |
| Instances of a setup | `GET/POST /api/setups/<id>/instances/` | `GET/DELETE /api/instances/<id>/` |

`GET /api/projects/<id>/setups/` lists every setup in a project and includes its setup, image, and environment codes.

Images store optional `repository` and `branch` strings (up to 256 characters each), `isPrivate` (0 or 1), and a `token` text field. A repository URL must use `https://github.com/owner/repo`; a branch is required when a repository is set. Private images require a token with repository Contents read permission. The backend encrypts the token before writing to the database and never includes it in API responses; `hasToken` indicates whether one is saved. On update, an empty token preserves the saved token when the repository is unchanged. Switching to public clears it. Migration `0007` adds these fields to existing images.

`GET /api/github/branches/?repository=<URL>` lists public branches. `POST /api/github/branches/` with JSON `{ "repository": "...", "token": "..." }` lists private branches before saving; with `{ "repository": "...", "image_id": 1 }` it uses the saved token for that image. Credentials are sent in the request body, never in the URL. Results are cached for five minutes per credential and read up to 2,000 branches. Only authenticated staff operators can call these endpoints.

`GET /api/system/cpu/` returns the current CPU limit for containers. It reads the number of logical CPUs exposed by the Docker daemon; when Docker is unavailable, it falls back to the host's logical CPU count and identifies that source in the response.

`GET /api/system/memory/` returns the total memory available to the Docker daemon in bytes. When Docker is unavailable, it falls back to the host's total physical memory. This is the machine's capacity, not its momentary free memory.

## Docker containers

`GET /api/setups/<id>/instances/` lists the setup's successfully created instances. `POST` to the same URL builds the linked image's `definition` (Dockerfile text), starts a detached container, and persists an `instance` row. The response includes `id`, `setup_id`, `number`, `code`, `container_id`, `container_name`, `port`, and audit fields. `POST /api/setups/<id>/containers/` remains a compatible alias and also persists the instance.

Instance codes and Docker container names are exactly `<setup-code>-replica-<number>`, for example `SERVER-replica-1`. The API reserves the lowest available positive number atomically before the Docker build without holding a database transaction during the build. Pending instances also reserve their number. After deletion or a failed creation, that number can be reused. Existing containers keep their assigned names and ports; they are not renumbered automatically. Codes must form valid Docker names; duplicate names across setups return HTTP 409. Setup responses include `hasInstances`; while it is true, `PUT /api/setups/<id>/` returns HTTP 409 (`setup_has_instances`) without changing the setup. The Django Admin also refuses setup changes in this state. This includes instances still being created. Delete all registered instances before editing a setup.

`DELETE /api/instances/<id>/` forcibly stops and removes the recorded container by its Docker ID, then removes the database row. If Docker fails, the row is kept for retry. If the container was already removed externally, the row can still be deleted. Named volumes are preserved. A setup with instances cannot be deleted. Existing containers created before migration `0008` are not automatically registered because their identities were never stored. The Docker CLI must be on the Django process's `PATH`, and the Docker daemon must be running. If Docker is unavailable, the API returns HTTP 503 with `code: "docker_unavailable"`; the web app shows a notification.

The definition is passed to `docker build --file -` through standard input. If a repository is set, Git checks out the selected branch first; the token is supplied to Git through its process environment and is not sent to Docker, placed in the clone URL, or saved in `.git/config`. The temporary checkout is removed after the build. The setup's `cpu` accepts a positive number such as `2` or `0.5`, up to the current CPU limit. Its `memory` accepts a positive quantity of at least 6 MB with a unit, such as `512 MB` or `4 GB`, up to the current machine capacity. The API checks both limits when a setup is saved and again against the Docker daemon when a container starts. Optional `port` uses `host_port:container_port`, such as `8000:8000`, and binds to `127.0.0.1` by default. To choose another IPv4 bind address, use `IP:host_port:container_port`, such as `0.0.0.0:8000:8000`. Optional `volume` uses a named volume and absolute container path, such as `backend_data:/data`; host bind paths are not accepted. These values are applied with Docker's `--cpus`, `--memory`, `--publish`, and `--mount` options. Each request creates a separately named image and container. Use a stable volume name for a replacement container that should access the same SQLite database; do not run two writers against the same database at once.

Create and update requests use JSON. Send `code` and the fields specific to each resource. The backend records the logged-in operator in `created_by` and `last_modified_by`. The timestamp fields are automatic. A record with associated children cannot be deleted; the API returns HTTP 409 with `code: "associated_records"`. Delete its children first.

`GET /api/auth/session/` supplies the CSRF cookie and current operator status. `POST /api/auth/login/` accepts a staff username and password, and `POST /api/auth/logout/` ends the session. Write operations use Django's CSRF protection; clients send the `csrftoken` cookie in the `X-CSRFToken` header. The Angular development server proxies `/api/` requests to `http://127.0.0.1:8000`.

## Login CAPTCHA

The login page uses Cloudflare Turnstile when `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` are set in the backend environment. Create a Turnstile widget for the frontend hostname in the [Cloudflare dashboard](https://dash.cloudflare.com/?to=/:account/turnstile), then set its site key and secret key on the backend process. Set `TURNSTILE_ALLOWED_HOSTNAMES` to a comma-separated list of frontend hostnames (without protocol or port), such as `app.example.com`. The backend validates the single-use challenge token with Cloudflare, checks that its action is `login`, and checks its hostname when that list is set. The secret key must never be placed in the frontend or committed to Git. The backend must be able to reach `https://challenges.cloudflare.com/turnstile/v0/siteverify`; browsers must be able to load the Turnstile script from that host.

With `DJANGO_DEBUG=False`, login requires both keys and fails closed when they are absent. With debug enabled and neither key set, local login remains available without Turnstile. Setting either key in debug mode also requires both keys and a valid challenge.

## Tests

```bash
./.venv/Scripts/python.exe manage.py test
```

For nonlocal deployments, use HTTPS and set a strong, stable `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, and `DJANGO_ALLOWED_HOSTS` appropriately. The secret key also encrypts saved GitHub tokens; changing it makes existing tokens unreadable until operators replace them. Back up the key separately from the database. Session and CSRF cookies are marked secure when debug is disabled.
