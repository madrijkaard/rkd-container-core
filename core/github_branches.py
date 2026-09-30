"""Read repository metadata and branch names from GitHub."""

import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.core.cache import cache


PAGE_SIZE = 100
MAX_PAGES = 20


class GithubBranchError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.status = status


def parse_github_repository(repository):
    """Accept a normal GitHub repository URL, never an arbitrary upstream host."""
    if not isinstance(repository, str) or not repository or len(repository) > 256:
        raise GithubBranchError('invalid_repository', 'Informe a URL de um repositório GitHub.', 400)
    try:
        parsed = urlsplit(repository.strip())
        parts = parsed.path.strip('/').split('/')
        valid = (
            parsed.scheme == 'https'
            and parsed.hostname == 'github.com'
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and len(parts) == 2
        )
    except ValueError:
        valid = False
    if not valid:
        raise GithubBranchError('invalid_repository', 'Use uma URL https://github.com/owner/repo.', 400)
    owner, repo = parts
    if repo.endswith('.git'):
        repo = repo[:-4]
    if (not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', repo)
            or repo in {'.', '..'}):
        raise GithubBranchError('invalid_repository', 'Use uma URL https://github.com/owner/repo.', 400)
    return owner, repo


def _github_json(url, token):
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Dockestra-Core',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            raise GithubBranchError('repository_not_found', 'Repositório não encontrado ou sem acesso no GitHub.', 404) from None
        if error.code == 401:
            raise GithubBranchError('github_invalid_token', 'Token do GitHub inválido ou expirado.', 401) from None
        if error.code == 429 or (error.code == 403 and error.headers and error.headers.get('X-RateLimit-Remaining') == '0'):
            raise GithubBranchError('github_rate_limited', 'Limite de consultas ao GitHub atingido. Tente novamente mais tarde.', 429) from None
        if error.code == 403:
            raise GithubBranchError('github_access_denied', 'O token não tem acesso a este repositório.', 403) from None
        raise GithubBranchError('github_unavailable', 'Não foi possível consultar o GitHub.', 502) from None
    except (URLError, TimeoutError, OSError):
        raise GithubBranchError('github_unavailable', 'Não foi possível conectar ao GitHub.', 503) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GithubBranchError('github_unavailable', 'Resposta inválida do GitHub.', 502) from None


def get_github_description(repository, token=None):
    owner, repo = parse_github_repository(repository)
    credential_id = hashlib.sha256(token.encode()).hexdigest() if token else 'public'
    cache_key = f'github-description:{owner.lower()}/{repo.lower()}:{credential_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    details = _github_json(f'https://api.github.com/repos/{owner}/{repo}', token)
    if (not isinstance(details, dict) or 'description' not in details
            or not isinstance(details['description'], (str, type(None)))):
        raise GithubBranchError('github_unavailable', 'Resposta inválida do GitHub.', 502)
    description = details['description'] or ''
    cache.set(cache_key, description, 300)
    return description


def list_github_branches(repository, token=None):
    owner, repo = parse_github_repository(repository)
    credential_id = hashlib.sha256(token.encode()).hexdigest() if token else 'public'
    cache_key = f'github-branches:{owner.lower()}/{repo.lower()}:{credential_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    branches = []
    for page in range(1, MAX_PAGES + 1):
        url = f'https://api.github.com/repos/{owner}/{repo}/branches?per_page={PAGE_SIZE}&page={page}'
        items = _github_json(url, token)
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in items):
            raise GithubBranchError('github_unavailable', 'Resposta inválida do GitHub.', 502)
        branches.extend(item['name'] for item in items)
        if len(items) < PAGE_SIZE:
            cache.set(cache_key, branches, 300)
            return branches

    raise GithubBranchError('too_many_branches', 'O repositório possui branches demais para listar.', 422)
