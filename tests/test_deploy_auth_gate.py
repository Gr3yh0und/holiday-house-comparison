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


def _run_deploy_script(tmp_path, script_name, site_password="hunter2-test"):
    """Run deploy.sh/deploy-test.sh from a scratch copy of the repo, with curl
    stubbed to capture uploads locally instead of hitting a real FTP host.
    Returns the directory of "uploaded" files.
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
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(CURL_STUB)
    curl_stub.chmod(curl_stub.stat().st_mode | stat.S_IEXEC)

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
