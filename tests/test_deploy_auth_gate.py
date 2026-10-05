"""Regression test for the deploy_auth/ password gate (WEBAPP_PROJECT_STANDARD.md §5).

Actually runs deploy.sh against a stubbed `curl` (no real FTP) rather than
grepping the script for the right-looking lines -- a future refactor that
silently drops the `<?php require .../_auth_gate.php'; ?>` prefix, or starts
uploading `index.html` instead of `index.php`, fails this test even if the
script still "looks" right.
"""
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CURL_STUB = """#!/usr/bin/env bash
src=""
url=""
quote=""
for ((i=1; i<=$#; i++)); do
  a="${!i}"
  if [ "$a" = "-T" ]; then
    j=$((i+1)); src="${!j}"
  elif [ "$a" = "-Q" ]; then
    j=$((i+1)); quote="${!j}"
  elif [[ "$a" == ftp://* ]]; then
    url="$a"
  fi
done
if [ -n "$quote" ]; then
  mkdir -p "$UPLOAD_LOG"
  echo "$quote" >> "$UPLOAD_LOG/.quote_commands"
  exit 0
fi
name="${url##*/}"
mkdir -p "$UPLOAD_LOG"
cp "$src" "$UPLOAD_LOG/$name"
exit 0
"""

# The repo copy under test has no real app.py/dependencies, so this stands in
# for the build step: it leaves the fixture-provided public/index.html alone
# and just reports success (or failure, for test_build_failure_aborts_deploy),
# writing public/health.json the same way app.py's _write_health() would.
PYTHON_STUB_SUCCESS = """#!/usr/bin/env bash
echo '{"version":"test","status":"ok","last_update":"2024-01-01 00:00","extra":{}}' > public/health.json
exit 0
"""

PYTHON_STUB_FAILURE = """#!/usr/bin/env bash
echo '{"version":"test","status":"down","last_update":null,"extra":{}}' > public/health.json
echo "simulated build failure" >&2
exit 1
"""


def _setup_repo_copy(tmp_path, script_name, site_password="hunter2-test", extra_config=""):
    """Set up a scratch copy of the repo plus a stubbed curl/python3 (bin_dir)
    for running deploy.sh/deploy-test.sh without a real FTP host or build.
    Returns (repo_copy, bin_dir).
    """
    repo_copy = tmp_path / "repo"
    repo_copy.mkdir()
    (repo_copy / "public").mkdir()
    (repo_copy / "public" / "index.html").write_text("<html><body>fake site</body></html>")

    auth_dir_src = REPO_ROOT / "deploy_auth"
    auth_dir_dst = repo_copy / "deploy_auth"
    auth_dir_dst.mkdir()
    for name in ("_auth_gate.php", "login.php", "robots.txt"):
        (auth_dir_dst / name).write_text((auth_dir_src / name).read_text())

    (repo_copy / script_name).write_text((REPO_ROOT / script_name).read_text())

    (repo_copy / "deploy.config").write_text(
        "FTP_HOST=ftp.example.com\n"
        "FTP_USER=testuser\n"
        "FTP_PASS=testpass\n"
        "FTP_REMOTE_PATH=/example.com\n"
        f"SITE_PASSWORD={site_password}\n"
        f"{extra_config}"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(CURL_STUB)
    curl_stub.chmod(curl_stub.stat().st_mode | stat.S_IEXEC)
    python_stub = bin_dir / "python3"
    python_stub.write_text(PYTHON_STUB_SUCCESS)
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IEXEC)

    return repo_copy, bin_dir


def _run_deploy_script(tmp_path, script_name, site_password="hunter2-test"):
    """Run deploy.sh/deploy-test.sh from a scratch copy of the repo, with curl
    and python3 stubbed out (no real FTP host, no real build).
    Returns the directory of "uploaded" files.
    """
    repo_copy, bin_dir = _setup_repo_copy(tmp_path, script_name, site_password)

    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(uploaded)

    result = subprocess.run(
        ["bash", script_name],
        cwd=repo_copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{script_name} failed:\n{result.stdout}\n{result.stderr}"
    return uploaded


def test_deploy_sh_uploads_gated_php_not_html(tmp_path):
    uploaded = _run_deploy_script(tmp_path, "deploy.sh")

    assert (uploaded / "index.php").exists()
    assert not (uploaded / "index.html").exists()

    gated = (uploaded / "index.php").read_text()
    assert gated.startswith("<?php require __DIR__ . '/_auth_gate.php'; ?>\n")
    assert "fake site" in gated


def test_deploy_sh_uploads_all_gate_files(tmp_path):
    uploaded = _run_deploy_script(tmp_path, "deploy.sh")
    for name in ("_auth_gate.php", "auth_secret.php", "login.php", "robots.txt"):
        assert (uploaded / name).exists(), f"{name} was not uploaded"


def test_deploy_sh_uploads_health_json(tmp_path):
    """health.json (WEBAPP_PROJECT_STANDARD.md §6a) must publish plain,
    never through the PHP auth gate -- it's deliberately unauthenticated.
    """
    uploaded = _run_deploy_script(tmp_path, "deploy.sh")
    assert (uploaded / "health.json").exists()
    assert not (uploaded / "health.json").read_text().startswith("<?php")


def test_deploy_sh_refuses_placeholder_password(tmp_path):
    with_placeholder = tmp_path / "placeholder"
    with_placeholder.mkdir()
    # Reuse the harness but override the config afterwards to the placeholder value.
    uploaded = with_placeholder / "uploaded"
    uploaded.mkdir()
    repo_copy = with_placeholder / "repo"
    repo_copy.mkdir()
    (repo_copy / "public").mkdir()
    (repo_copy / "public" / "index.html").write_text("<html></html>")
    auth_dir_dst = repo_copy / "deploy_auth"
    auth_dir_dst.mkdir()
    for name in ("_auth_gate.php", "login.php", "robots.txt"):
        (auth_dir_dst / name).write_text((REPO_ROOT / "deploy_auth" / name).read_text())
    (repo_copy / "deploy.sh").write_text((REPO_ROOT / "deploy.sh").read_text())
    (repo_copy / "deploy.config").write_text(
        "FTP_HOST=ftp.example.com\nFTP_USER=u\nFTP_PASS=p\nFTP_REMOTE_PATH=/x\n"
        "SITE_PASSWORD=change-me\n"
    )

    result = subprocess.run(
        ["bash", "deploy.sh"],
        cwd=repo_copy,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "placeholder" in result.stdout


def test_deploy_test_sh_uploads_index_test_php(tmp_path):
    uploaded = _run_deploy_script(tmp_path, "deploy-test.sh")
    assert (uploaded / "index-test.php").exists()
    assert not (uploaded / "index-test.html").exists()
    gated = (uploaded / "index-test.php").read_text()
    assert gated.startswith("<?php require __DIR__ . '/_auth_gate.php'; ?>\n")


def test_deploy_sh_deletes_stale_unprotected_html(tmp_path):
    """Every pre-gate deploy left a plain index.html on the server. If it's
    never cleaned up, it keeps serving the whole site with no login -- see
    WEBAPP_PROJECT_STANDARD.md §5.
    """
    uploaded = _run_deploy_script(tmp_path, "deploy.sh")
    quote_log = (uploaded / ".quote_commands").read_text()
    assert "DELE" in quote_log
    assert "index.html" in quote_log


def test_deploy_test_sh_deletes_stale_unprotected_html(tmp_path):
    uploaded = _run_deploy_script(tmp_path, "deploy-test.sh")
    quote_log = (uploaded / ".quote_commands").read_text()
    assert "DELE" in quote_log
    assert "index-test.html" in quote_log


def _auth_cookie_secret(repo_copy):
    secret_php = (repo_copy / "deploy_auth" / "auth_secret.php").read_text()
    for line in secret_php.splitlines():
        if "AUTH_COOKIE_SECRET" in line:
            return line.split("'")[3]
    return None


def test_cookie_secret_unchanged_when_password_unchanged(tmp_path):
    uploaded1 = _run_deploy_script(tmp_path, "deploy.sh", site_password="same-password")
    repo_copy = uploaded1.parent / "repo"
    secret1 = _auth_cookie_secret(repo_copy)

    # Re-run the same script in place (same .auth_state), same password.
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
    uploaded2 = tmp_path / "uploaded2"
    uploaded2.mkdir()
    env["UPLOAD_LOG"] = str(uploaded2)
    result = subprocess.run(
        ["bash", "deploy.sh"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    secret2 = _auth_cookie_secret(repo_copy)
    assert secret1 == secret2


def test_cookie_secret_rotates_when_password_changes(tmp_path):
    uploaded1 = _run_deploy_script(tmp_path, "deploy.sh", site_password="old-password")
    repo_copy = uploaded1.parent / "repo"
    secret1 = _auth_cookie_secret(repo_copy)

    # Change SITE_PASSWORD in place and re-deploy with the same .auth_state.
    (repo_copy / "deploy.config").write_text(
        "FTP_HOST=ftp.example.com\n"
        "FTP_USER=testuser\n"
        "FTP_PASS=testpass\n"
        "FTP_REMOTE_PATH=/example.com\n"
        "SITE_PASSWORD=new-password\n"
    )
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
    uploaded2 = tmp_path / "uploaded2"
    uploaded2.mkdir()
    env["UPLOAD_LOG"] = str(uploaded2)
    result = subprocess.run(
        ["bash", "deploy.sh"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    secret2 = _auth_cookie_secret(repo_copy)
    assert secret1 != secret2, "AUTH_COOKIE_SECRET must rotate so old logins are invalidated"


def test_deploy_sh_builds_before_deploying(tmp_path):
    """deploy.sh must invoke the build (python3 app.py) itself, not just
    trust a leftover public/index.html from an earlier, unrelated run.
    """
    repo_copy, bin_dir = _setup_repo_copy(tmp_path, "deploy.sh")
    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(uploaded)
    result = subprocess.run(
        ["bash", "deploy.sh"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Building site" in result.stdout


def test_deploy_sh_aborts_when_build_fails(tmp_path):
    repo_copy, bin_dir = _setup_repo_copy(tmp_path, "deploy.sh")
    python_stub = bin_dir / "python3"
    python_stub.write_text(PYTHON_STUB_FAILURE)
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IEXEC)

    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(uploaded)
    result = subprocess.run(
        ["bash", "deploy.sh"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "build failed" in result.stdout
    uploaded_names = {p.name for p in uploaded.iterdir()}
    assert uploaded_names == {"health.json"}, (
        "a failed build must never publish the site itself, but health.json "
        "(status=down, written by app.py before it exits) must still get "
        "through so a monitoring consumer sees the failure"
    )
    assert '"status":"down"' in (uploaded / "health.json").read_text()


def test_deploy_sh_rejects_invalid_target(tmp_path):
    repo_copy, bin_dir = _setup_repo_copy(tmp_path, "deploy.sh")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    result = subprocess.run(
        ["bash", "deploy.sh", "--target", "bogus"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "ftp, local, or both" in result.stdout


def test_deploy_sh_target_local_writes_files_without_ftp(tmp_path):
    """--target local must work with no FTP credentials at all, and must not
    touch the network (the curl stub records nothing uploaded)."""
    local_deploy_dir = tmp_path / "local_site"
    repo_copy, bin_dir = _setup_repo_copy(
        tmp_path, "deploy.sh",
        extra_config=f"LOCAL_DEPLOY_PATH={local_deploy_dir}\n",
    )
    # Strip FTP_* keys entirely -- local-only deploys must not require them.
    (repo_copy / "deploy.config").write_text(
        f"SITE_PASSWORD=hunter2-test\nLOCAL_DEPLOY_PATH={local_deploy_dir}\n"
    )
    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(uploaded)
    result = subprocess.run(
        ["bash", "deploy.sh", "--target", "local"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Caddy's file_server serves this path and never executes PHP, so the
    # local target must publish the plain page and no PHP at all -- a .php
    # file there is streamed back as source (auth_secret.php's hash included).
    page = (local_deploy_dir / "index.html").read_text()
    assert not page.startswith("<?php")
    for name in ("robots.txt", "health.json"):
        assert (local_deploy_dir / name).exists(), f"{name} was not copied locally"
    assert not list(local_deploy_dir.glob("*.php")), "local target must never publish PHP"
    assert not any(uploaded.iterdir()), "local-only target must never touch FTP"


def test_deploy_sh_target_local_removes_stale_php(tmp_path):
    """A local deploy dir left behind by the old (gated) local target must be
    cleaned up, or Caddy keeps serving auth_secret.php as plain text."""
    local_deploy_dir = tmp_path / "local_site"
    local_deploy_dir.mkdir()
    for name in ("index.php", "_auth_gate.php", "auth_secret.php", "login.php"):
        (local_deploy_dir / name).write_text("<?php // stale")
    repo_copy, bin_dir = _setup_repo_copy(
        tmp_path, "deploy.sh",
        extra_config=f"LOCAL_DEPLOY_PATH={local_deploy_dir}\n",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(tmp_path)
    result = subprocess.run(
        ["bash", "deploy.sh", "--target", "local"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (local_deploy_dir / "index.html").exists()
    assert not list(local_deploy_dir.glob("*.php"))


def test_deploy_sh_target_both_writes_ftp_and_local(tmp_path):
    local_deploy_dir = tmp_path / "local_site"
    repo_copy, bin_dir = _setup_repo_copy(
        tmp_path, "deploy.sh",
        extra_config=f"LOCAL_DEPLOY_PATH={local_deploy_dir}\n",
    )
    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(uploaded)
    result = subprocess.run(
        ["bash", "deploy.sh", "--target", "both"], cwd=repo_copy, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (uploaded / "index.php").exists()
    assert (local_deploy_dir / "index.html").exists()
    assert not list(local_deploy_dir.glob("*.php"))


def test_password_hash_matches_php_hash_algorithm(tmp_path):
    """The bash-side hash('sha256', salt . password) must byte-for-byte match
    what login.php computes, or every real login attempt fails silently.
    """
    import hashlib

    uploaded = _run_deploy_script(tmp_path, "deploy.sh", site_password="hunter2-test")
    secret_php = (uploaded / "auth_secret.php").read_text()

    salt = None
    expected_hash = None
    for line in secret_php.splitlines():
        # define('KEY', 'value'); -> split('\'') gives ["define(", "KEY", ", ", "value", ");"]
        if "SITE_PASSWORD_SALT" in line:
            salt = line.split("'")[3]
        if "SITE_PASSWORD_HASH" in line:
            expected_hash = line.split("'")[3]
    assert salt and expected_hash

    computed = hashlib.sha256((salt + "hunter2-test").encode()).hexdigest()
    assert computed == expected_hash
