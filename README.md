# Container Core

Python and Django backend for the Container platform. The Django project is `container_core`, and its application is `core`.

## Database

SQLite stores data in `container_core.sqlite3` at the repository root by default. Set `DJANGO_DB_PATH` to use another location; the container uses `/data/container_core.sqlite3` so a Docker volume can persist it. The `core` migrations create the `project`, `environment`, `image`, and `setup` tables. Migration `0003` renames the previous table to `setup` without discarding its records. Their relationships use `project_id`, `environment_id`, and `image_id`. Each table has an automatically generated `id` primary key.

## Requirements

- Python 3.12 or later
- Git CLI for images linked to a GitHub repository
- Docker CLI and a reachable daemon to create containers

## Run locally

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

## Deploy on the Ubuntu VPS with Docker Compose

This repository has its own `docker-compose.yml`. It starts Django, installs the Docker CLI and Git in the backend image, and gives the backend access to the **host** Docker Engine through `/var/run/docker.sock`. It does not run another Docker daemon. It creates the shared `rkd-network` used by the frontend Compose project. Do not publish port 8000 to the internet; the frontend Nginx container proxies `/api/`, `/admin/`, and `/static/` over HTTPS.

On the VPS, clone both repositories. In this backend checkout, copy `.env.example` to `.env` and set a strong, stable `DJANGO_SECRET_KEY` and the real `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`. The `.env` file is ignored by Git. The Compose file sets `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS=sinan-pro.com`, and `TURNSTILE_ALLOWED_HOSTNAMES=sinan-pro.com`. Keep `.env` private, back it up securely, and do not bake these values into the image.

```bash
cp .env.example .env
# Edit .env with your actual keys before starting.
chmod 600 .env
docker compose up -d --build
docker compose ps
```

The SQLite file is `volumes/sqlite/container_core.sqlite3` on the VPS host, mounted as `/data/container_core.sqlite3` in the backend. The entrypoint runs migrations before Gunicorn starts. Back up both the database and `DJANGO_SECRET_KEY`; changing the key makes stored private-repository tokens unreadable. To create the first operator on a fresh VPS database, run `docker compose exec backend python manage.py createsuperuser`. The local development database and its users are not included in the Git clone.

Start this Compose project before the frontend project because the latter joins the shared Docker network. The Django process runs under an unprivileged UID but is granted access to the host Docker socket. Docker socket access is effectively host administrator access; grant staff accounts only to trusted operators and keep this backend off public ports.

## Backend image definition

This repository has no Dockerfile. Save Dockerfile text in an `image` record's `definition` field, then create a setup linked to that image. Without a GitHub repository, the API builds with the backend repository root as its context. With a repository, it checks out the selected branch into a temporary directory and uses that source tree as the context. The Dockerfile can use `COPY . /app` to include the selected branch. The backend `.dockerignore` applies only when the backend repository is the context; a linked repository can supply its own `.dockerignore`.

For a backend image, use Python 3.12, install `requirements.txt`, include the application source, run migrations at startup, and serve `container_core.wsgi:application` with Gunicorn. Set `DJANGO_DB_PATH` to a path such as `/data/container_core.sqlite3`. The setup determines whether to mount a named volume at `/data` and publish a port. The image needs the Docker CLI only if it will itself call the container creation endpoint; the CLI also needs a reachable daemon and appropriate credentials at runtime.

The container API applies the setup's CPU, memory, optional port mapping, and optional named volume mount when starting a container. It does not pass environment variables or Docker daemon credentials to the container. API access requires a Django staff account. Only trusted operators should receive staff access because they can define Dockerfiles and start containers.

## CRUD API

| Resource | List and create | Retrieve, update, and delete |
| --- | --- | --- |
| Projects | `GET/POST /api/projects/` | `GET/PUT/DELETE /api/projects/<id>/` |
| Environments in a project | `GET/POST /api/projects/<id>/environments/` | `GET/PUT/DELETE /api/environments/<id>/` |
| Images in an environment | `GET/POST /api/environments/<id>/images/` | `GET/PUT/DELETE /api/images/<id>/` |
| Setups using an image | `GET/POST /api/images/<id>/setups/` | `GET/PUT/DELETE /api/setups/<id>/` |

`GET /api/projects/<id>/setups/` lists every setup in a project and includes its setup, image, and environment codes.

Images store optional `repository` and `branch` strings (up to 256 characters each), `isPrivate` (0 or 1), and a `token` text field. A repository URL must use `https://github.com/owner/repo`; a branch is required when a repository is set. Private images require a token with repository Contents read permission. The backend encrypts the token before writing to the database and never includes it in API responses; `hasToken` indicates whether one is saved. On update, an empty token preserves the saved token when the repository is unchanged. Switching to public clears it. Migration `0007` adds these fields to existing images.

`GET /api/github/branches/?repository=<URL>` lists public branches. `POST /api/github/branches/` with JSON `{ "repository": "...", "token": "..." }` lists private branches before saving; with `{ "repository": "...", "image_id": 1 }` it uses the saved token for that image. Credentials are sent in the request body, never in the URL. Results are cached for five minutes per credential and read up to 2,000 branches. Only authenticated staff operators can call these endpoints.

`GET /api/system/cpu/` returns the current CPU limit for containers. It reads the number of logical CPUs exposed by the Docker daemon; when Docker is unavailable, it falls back to the host's logical CPU count and identifies that source in the response.

`GET /api/system/memory/` returns the total memory available to the Docker daemon in bytes. When Docker is unavailable, it falls back to the host's total physical memory. This is the machine's capacity, not its momentary free memory.

## Docker containers

`POST /api/setups/<id>/containers/` builds an image from the linked image's `definition` (Dockerfile text) and starts a detached container. The endpoint returns its container ID and name. The Docker CLI must be on the Django process's `PATH`, and the Docker daemon must be running. If Docker is unavailable, the API returns HTTP 503 with `code: "docker_unavailable"`; the web app shows a notification.

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
