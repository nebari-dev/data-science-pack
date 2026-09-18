"""GitHub Actions step outputs, env vars, log masks and error annotations."""

from __future__ import annotations

import os
import uuid


def _write_kv(path: str, name: str, value: str) -> None:
    if "\n" in value:
        delimiter = f"ghadelim_{uuid.uuid4().hex}"
        block = f"{name}<<{delimiter}\n{value}\n{delimiter}\n"
    else:
        block = f"{name}={value}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)


def write_output(name: str, value: str) -> None:
    _write_kv(os.environ["GITHUB_OUTPUT"], name, value)


def write_env(name: str, value: str) -> None:
    _write_kv(os.environ["GITHUB_ENV"], name, value)


def mask(value: str) -> None:
    print(f"::add-mask::{value}")


def error(message: str) -> None:
    print(f"::error::{message}")
