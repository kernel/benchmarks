"""Checks that run before any task is generated or browser is created."""

from __future__ import annotations

import os

import httpx

KERNEL_API_URL = "https://api.onkernel.com"


def kernel_api_url() -> str:
    return (os.environ.get("KERNEL_BASE_URL") or KERNEL_API_URL).rstrip("/")


def require_env(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")


def check_kernel_project() -> None:
    """Require KERNEL_API_KEY to be scoped to exactly one project.

    Benchmarks create browsers and proxies; keeping them in a dedicated project
    keeps that usage separate from everything else in the organization.
    """
    require_env("KERNEL_API_KEY")
    response = httpx.get(
        f"{kernel_api_url()}/auth/context",
        headers={"Authorization": f"Bearer {os.environ['KERNEL_API_KEY']}"},
        timeout=30,
    )
    response.raise_for_status()
    authorization = response.json().get("authorization") or {}
    credential = (authorization.get("credential_scope") or {}).get("project_id")
    effective = (authorization.get("effective_scope") or {}).get("project_id")
    if not credential or credential != effective:
        raise SystemExit("KERNEL_API_KEY must be scoped to a single Kernel project")
