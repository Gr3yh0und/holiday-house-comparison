"""Regression tests for deploy.sh/deploy-test.sh's --rollback (WEBAPP_PROJECT_STANDARD.md §14B).

Runs the real scripts against a stubbed `curl` -- no real FTP -- deploying
twice with different content, then asserts a rollback re-uploads the *first*
content verbatim. A future refactor that snapshots the wrong bytes (e.g. the
pre-gate public/index.html instead of the uploaded .php) or breaks the
disambiguation/pruning logic fails this test even if the script still reads
correctly.
"""
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CURL_STUB = """#!/usr/bin/env bash
src=""
url=""
for ((i=1; i<=$#; i++)); do
  a="${!i}"
  if [ "$a" = "-T" ]; then
    j=$((i+1)); src="${!j}"
  elif [[ "$a" == ftp://* ]]; then
    url="$a"
  fi
done
name="${url##*/}"
mkdir -p "$UPLOAD_LOG"
cp "$src" "$UPLOAD_LOG/$name"
exit 0
"""


def _make_repo(tmp_path, script_name):
    repo_copy = tmp_path / "repo"
    repo_copy.mkdir()
    (repo_copy / "public").mkdir()

    auth_dir_dst = repo_copy / "deploy_auth"
    auth_dir_dst.mkdir()
    for name in ("_auth_gate.php", "login.php", "robots.txt"):
        (auth_dir_dst / name).write_text((REPO_ROOT / "deploy_auth" / name).read_text())

    (repo_copy / script_name).write_text((REPO_ROOT / script_name).read_text())

    (repo_copy / "deploy.config").write_text(
        "FTP_HOST=ftp.example.com\n"
        "FTP_USER=testuser\n"
        "FTP_PASS=testpass\n"
        "FTP_REMOTE_PATH=/example.com\n"
        "SITE_PASSWORD=hunter2-test\n"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(CURL_STUB)
    curl_stub.chmod(curl_stub.stat().st_mode | stat.S_IEXEC)

    return repo_copy, bin_dir


def _deploy(repo_copy, bin_dir, script_name, content, upload_dir, *extra_args):
    (repo_copy / "public" / "index.html").write_text(content)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(upload_dir)
    upload_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", script_name, *extra_args],
        cwd=repo_copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result


def test_rollback_republishes_the_first_content(tmp_path):
    repo_copy, bin_dir = _make_repo(tmp_path, "deploy.sh")

    r1 = _deploy(repo_copy, bin_dir, "deploy.sh", "<html>Version A</html>", tmp_path / "up1")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    r2 = _deploy(repo_copy, bin_dir, "deploy.sh", "<html>Version B</html>", tmp_path / "up2")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    releases = sorted((repo_copy / "releases" / "prod").iterdir())
    assert len(releases) == 2, "expected two distinct release snapshots, got a collision"

    rollback_upload = tmp_path / "up3"
    rollback_upload.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(rollback_upload)
    result = subprocess.run(
        ["bash", "deploy.sh", "--rollback"],
        cwd=repo_copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    rolled_back = (rollback_upload / "index.php").read_text()
    assert "Version A" in rolled_back
    assert "Version B" not in rolled_back
    # The single most important property: a rollback must never republish a
    # page missing its auth gate. This is a check on the snapshot itself, not
    # just on the content -- snapshotting the pre-gate public/index.html
    # instead of the uploaded, gated bytes would still pass the two asserts
    # above (the wording is still in there) while silently dropping the gate.
    assert rolled_back.startswith("<?php require __DIR__ . '/_auth_gate.php'; ?>\n")
    # Rollback only restores the page, not the auth gate files.
    assert not (rollback_upload / "_auth_gate.php").exists()


def test_rollback_refuses_when_not_enough_releases(tmp_path):
    repo_copy, bin_dir = _make_repo(tmp_path, "deploy.sh")
    r1 = _deploy(repo_copy, bin_dir, "deploy.sh", "<html>Only one</html>", tmp_path / "up1")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    result = subprocess.run(
        ["bash", "deploy.sh", "--rollback"],
        cwd=repo_copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "cannot go back" in result.stdout


def test_prunes_to_five_releases(tmp_path):
    repo_copy, bin_dir = _make_repo(tmp_path, "deploy.sh")
    for i in range(7):
        r = _deploy(repo_copy, bin_dir, "deploy.sh", f"<html>Version {i}</html>", tmp_path / f"up{i}")
        assert r.returncode == 0, r.stdout + r.stderr

    releases = list((repo_copy / "releases" / "prod").iterdir())
    assert len(releases) == 5, f"expected pruning to 5, got {len(releases)}"

    # The two oldest snapshots (Version 0, Version 1) must be gone; the newest
    # (Version 6) must have survived -- confirms it prunes the *oldest*, not
    # an arbitrary two. Substring checks, not exact-match membership: each
    # file also carries the gate's require-line prefix.
    contents = [(d / "index.php").read_text() for d in releases]
    assert not any("Version 0" in c for c in contents)
    assert not any("Version 1" in c for c in contents)
    assert any("Version 6" in c for c in contents)


def test_deploy_test_sh_rollback_is_independent_of_prod(tmp_path):
    repo_copy, bin_dir = _make_repo(tmp_path, "deploy-test.sh")
    r1 = _deploy(repo_copy, bin_dir, "deploy-test.sh", "<html>Test A</html>", tmp_path / "up1")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2 = _deploy(repo_copy, bin_dir, "deploy-test.sh", "<html>Test B</html>", tmp_path / "up2")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    assert not (repo_copy / "releases" / "prod").exists()
    assert (repo_copy / "releases" / "test").exists()

    rollback_upload = tmp_path / "up3"
    rollback_upload.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UPLOAD_LOG"] = str(rollback_upload)
    result = subprocess.run(
        ["bash", "deploy-test.sh", "--rollback"],
        cwd=repo_copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Test A" in (rollback_upload / "index-test.php").read_text()
