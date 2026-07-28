from __future__ import annotations

from importlib import resources


def resource_filename(package_or_requirement: str, resource_name: str) -> str:
    package = resources.files(package_or_requirement)
    return str(package.joinpath(resource_name))

