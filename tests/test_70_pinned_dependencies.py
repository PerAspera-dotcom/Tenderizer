"""The production image installs exactly what requirements.lock says.

On 2026-11-23 a code-only deploy rebuilt the image with unpinned
dependencies, pulled sentry-sdk 3.0.0 (which rejects the
send_default_pii argument in src/api.py) and the app crashed at startup:
docs/INCIDENT_2026-11-23_BACKEND_DEPLOY.md. These tests keep the fix in
place: every runtime dependency pinned exactly, the pins agreeing with the
lock, the Dockerfile installing with the lock as a constraint, and the
base image pinned to a patch release.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock():
    out = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line and "@" not in line, f"lock entries must be exact name==version pins: {line!r}"
        name, version = line.split("==")
        assert _norm(name) not in out, f"duplicate lock entry: {name}"
        out[_norm(name)] = version
    return out


def _runtime_deps():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return project["dependencies"]


def test_every_runtime_dependency_is_pinned_exactly():
    for spec in _runtime_deps():
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?==[A-Za-z0-9_.+\-]+", spec), \
            f"unpinned or loosely pinned runtime dependency: {spec!r} (use name==version, matching requirements.lock)"


def test_pins_agree_with_the_lock():
    lock = _lock()
    for spec in _runtime_deps():
        name = re.match(r"[A-Za-z0-9_.\-]+", spec).group(0)
        version = spec.split("==")[1]
        assert _norm(name) in lock, f"{name} is a dependency but missing from requirements.lock"
        assert lock[_norm(name)] == version, \
            f"{name}: pyproject pins {version}, requirements.lock has {lock[_norm(name)]} — change both together"


def test_lock_excludes_the_app_and_build_tools():
    lock = _lock()
    for name in ("the-scout", "pip", "setuptools", "wheel"):
        assert name not in lock, f"{name} doesn't belong in requirements.lock"


def test_sentry_sdk_stays_below_3():
    """3.0.0 removed send_default_pii, which src/api.py passes. Moving to 3.x
    means changing that call first."""
    major = int(_lock()["sentry-sdk"].split(".")[0])
    assert major < 3


def test_dockerfile_installs_with_the_lock_and_a_pinned_base_image():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM python:\d+\.\d+\.\d+-slim\b", dockerfile, re.M), "base image must be pinned to a patch release"
    assert re.search(r"^COPY [^\n]*requirements\.lock", dockerfile, re.M), "Dockerfile must copy requirements.lock"
    assert re.search(r"^RUN pip install [^\n]*-c requirements\.lock", dockerfile, re.M), \
        "Dockerfile must install with requirements.lock as a constraint"


def test_railway_waits_for_the_healthcheck():
    cfg = tomllib.loads((ROOT / "railway.toml").read_text(encoding="utf-8"))
    assert cfg["deploy"]["healthcheckPath"] == "/api/health-check"
    assert cfg["build"]["builder"] == "DOCKERFILE"
    api = (ROOT / "src" / "api.py").read_text(encoding="utf-8")
    assert '@app.get("/api/health-check")' in api, "the healthcheck path must exist"
