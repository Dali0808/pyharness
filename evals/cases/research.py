from __future__ import annotations

from evals.cases.model import EvalCase


RESEARCH_CASES = (
    EvalCase(
        case_id="research-api-routes",
        task=(
            "Inspect the API route modules and create report.txt with exactly "
            "three lines in this order: /users, /orders, /health. Each line "
            "contains the route followed by its method, separated by one "
            "space. Exclude internal routes and end with a newline. If a "
            "delegation tool is available, delegate the investigation first."
        ),
        initial_files={
            "api/users.py": "PUBLIC_ROUTES = [('GET', '/users')]\n",
            "api/orders.py": "PUBLIC_ROUTES = [('POST', '/orders')]\n",
            "api/health.py": "PUBLIC_ROUTES = [('GET', '/health')]\n",
            "api/internal.py": "INTERNAL_ROUTES = [('GET', '/debug')]\n",
        },
        expected_files={"report.txt": "/users GET\n/orders POST\n/health GET\n"},
        expected_tool_names=frozenset({"read_file", "write_file"}),
    ),
    EvalCase(
        case_id="research-configuration",
        task=(
            "Inspect the three service configuration files. Create report.txt "
            "with one line per service in alphabetical order, formatted "
            "'service:port', with a trailing newline. If a delegation tool "
            "is available, delegate the investigation first."
        ),
        initial_files={
            "services/web.conf": "name=web\nport=8080\n",
            "services/api.conf": "name=api\nport=9000\n",
            "services/worker.conf": "name=worker\nport=7000\n",
        },
        expected_files={"report.txt": "api:9000\nweb:8080\nworker:7000\n"},
        expected_tool_names=frozenset({"read_file", "write_file"}),
    ),
    EvalCase(
        case_id="research-permissions",
        task=(
            "Inspect role definitions and route permissions. Create report.txt "
            "listing only routes available to the editor role, sorted by path, "
            "one path per line with a trailing newline. If a delegation tool "
            "is available, delegate the investigation first."
        ),
        initial_files={
            "auth/roles.py": "EDITOR = {'read', 'write'}\n",
            "routes/articles.py": "ROUTES = {'/articles': 'read', '/articles/new': 'write'}\n",
            "routes/admin.py": "ROUTES = {'/admin': 'admin'}\n",
            "routes/profile.py": "ROUTES = {'/profile': 'read'}\n",
        },
        expected_files={
            "report.txt": "/articles\n/articles/new\n/profile\n",
        },
        expected_tool_names=frozenset({"read_file", "write_file"}),
    ),
)
