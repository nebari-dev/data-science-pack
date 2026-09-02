"""Tests for scripts/preview/comment.py's PR-comment body builders.

These strings were manually verified against GitHub's real markdown
renderer (`gh api /markdown`) earlier -- this module just moves the exact
same content into a testable function instead of a workflow `message:`
block, so these tests pin the content, not re-verify GitHub's rendering.
"""

from __future__ import annotations

from scripts.preview import comment

URL = "https://pr-205-data-science-pack.openteams.app"
KC_URL = "https://keycloak-pr-205-data-science-pack.openteams.app"
DEPLOYED_AT = "2026-09-01 12:21 UTC"
DEPLOYED_AT_ISO = "2026-09-01T12:21:18Z"
EXPIRES_AT = "2026-09-01 12:41 UTC"
EXPIRES_AT_ISO = "2026-09-01T12:41:18Z"


def test_render_ready_contains_the_status_row_and_links():
    body = comment.render_ready(URL, KC_URL, DEPLOYED_AT, DEPLOYED_AT_ISO, EXPIRES_AT, EXPIRES_AT_ISO, is_fork=False)

    assert "| Project | Deployment | Actions | Updated |" in body
    assert f"🟢 [Ready]({URL})" in body
    assert f"[Preview]({URL})" in body
    assert f"[Keycloak]({KC_URL})" in body
    assert f'<relative-time datetime="{DEPLOYED_AT_ISO}">{DEPLOYED_AT}</relative-time>' in body
    assert f'<relative-time datetime="{EXPIRES_AT_ISO}">{EXPIRES_AT}</relative-time>' in body
    assert "extend-preview" in body
    assert "deploy-preview" in body


def test_render_ready_omits_fork_warning_when_not_a_fork():
    body = comment.render_ready(URL, KC_URL, DEPLOYED_AT, DEPLOYED_AT_ISO, EXPIRES_AT, EXPIRES_AT_ISO, is_fork=False)

    assert "fork" not in body.lower()


def test_render_ready_includes_fork_warning_when_a_fork():
    body = comment.render_ready(URL, KC_URL, DEPLOYED_AT, DEPLOYED_AT_ISO, EXPIRES_AT, EXPIRES_AT_ISO, is_fork=True)

    assert "This PR is from a fork" in body
    assert "not from a trusted maintainer branch" in body


def test_render_ready_has_no_em_or_en_dashes():
    body = comment.render_ready(URL, KC_URL, DEPLOYED_AT, DEPLOYED_AT_ISO, EXPIRES_AT, EXPIRES_AT_ISO, is_fork=True)

    assert "—" not in body
    assert "–" not in body


def test_render_expired_shows_expired_status_and_no_live_links():
    body = comment.render_expired(EXPIRES_AT, EXPIRES_AT_ISO)

    assert "has expired" in body
    assert "⚫ Expired" in body
    assert URL not in body
    assert f'<relative-time datetime="{EXPIRES_AT_ISO}">{EXPIRES_AT}</relative-time>' in body


def test_render_stopped_mentions_the_label():
    body = comment.render_stopped()

    assert "stopped" in body
    assert "deploy-preview" in body


def test_main_render_ready_writes_body_output(monkeypatch, tmp_path):
    out_file = tmp_path / "output"
    out_file.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    rc = comment.main([
        "comment", "render-ready",
        "--url", URL, "--keycloak-url", KC_URL,
        "--deployed-at", DEPLOYED_AT, "--deployed-at-iso", DEPLOYED_AT_ISO,
        "--expires-at", EXPIRES_AT, "--expires-at-iso", EXPIRES_AT_ISO,
    ])

    assert rc == 0
    content = out_file.read_text()
    assert content.startswith("body<<")
    assert "🟢 [Ready]" in content


def test_main_render_ready_with_fork_flag_includes_warning(monkeypatch, tmp_path):
    out_file = tmp_path / "output"
    out_file.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    comment.main([
        "comment", "render-ready", "--fork",
        "--url", URL, "--keycloak-url", KC_URL,
        "--deployed-at", DEPLOYED_AT, "--deployed-at-iso", DEPLOYED_AT_ISO,
        "--expires-at", EXPIRES_AT, "--expires-at-iso", EXPIRES_AT_ISO,
    ])

    assert "This PR is from a fork" in out_file.read_text()


def test_main_render_expired_writes_body_output(monkeypatch, tmp_path):
    out_file = tmp_path / "output"
    out_file.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    rc = comment.main(["comment", "render-expired", "--expires-at", EXPIRES_AT, "--expires-at-iso", EXPIRES_AT_ISO])

    assert rc == 0
    assert "has expired" in out_file.read_text()


def test_main_render_stopped_writes_body_output(monkeypatch, tmp_path):
    out_file = tmp_path / "output"
    out_file.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    rc = comment.main(["comment", "render-stopped"])

    assert rc == 0
    assert "stopped" in out_file.read_text()
