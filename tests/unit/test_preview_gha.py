"""Tests for scripts/preview/gha.py's GitHub Actions I/O helpers."""

from __future__ import annotations

from scripts.preview import gha


def test_write_output_appends_single_line_key_value(tmp_path, capsys):
    out_file = tmp_path / "output"
    out_file.write_text("")

    gha.write_output("url", "https://example.com", path=str(out_file))

    assert out_file.read_text() == "url=https://example.com\n"


def test_write_output_appends_to_existing_content(tmp_path):
    out_file = tmp_path / "output"
    out_file.write_text("existing=1\n")

    gha.write_output("url", "https://example.com", path=str(out_file))

    assert out_file.read_text() == "existing=1\nurl=https://example.com\n"


def test_write_output_multiline_value_uses_delimiter_block(tmp_path):
    out_file = tmp_path / "output"
    out_file.write_text("")

    gha.write_output("body", "line one\nline two", path=str(out_file))

    content = out_file.read_text()
    lines = content.splitlines()
    assert lines[0].startswith("body<<")
    delim = lines[0].split("<<", 1)[1]
    assert lines[1] == "line one"
    assert lines[2] == "line two"
    assert lines[3] == delim


def test_write_env_appends_single_line_key_value(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("")

    gha.write_env("TUNNEL_ID", "abc-123", path=str(env_file))

    assert env_file.read_text() == "TUNNEL_ID=abc-123\n"


def test_mask_prints_add_mask_command(capsys):
    gha.mask("super-secret-token")

    assert capsys.readouterr().out == "::add-mask::super-secret-token\n"


def test_error_prints_error_command(capsys):
    gha.error("tunnel creation failed")

    assert capsys.readouterr().out == "::error::tunnel creation failed\n"
