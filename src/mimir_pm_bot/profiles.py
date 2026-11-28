"""Editable session configuration, loaded once at startup."""

import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

Role = Literal["mimir", "reviewer"]


def config_path(name: str) -> Path:
    package = Path(__file__).resolve().parent
    checkout = package.parent.parent
    if (checkout / "pyproject.toml").is_file():
        return checkout / name
    return package / "defaults" / name


def read_prompt(role: Role) -> str:
    path = config_path(f"prompts/{role}.md")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"{path}: expected a nonempty {role} prompt")
    return value + "\n"


def servers(role: Role, internal: str = "", *, path: Path | None = None) -> list[dict[str, Any]]:
    path = path or config_path("config/mcp.toml")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        names = data[role]["servers"]
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise ValueError("servers must list server names")
        if len(names) != len(set(names)):
            raise ValueError("duplicate server")
        result = []
        for name in names:
            if name == "mimir":
                if role != "mimir" or not internal:
                    raise ValueError("mimir is the management-session endpoint")
                url = internal
            else:
                url = data["http"][name]["url"]
                if (
                    not isinstance(url, str)
                    or urlsplit(url).scheme not in {"http", "https"}
                    or not urlsplit(url).hostname
                ):
                    raise ValueError(f"invalid HTTP URL for {name}")
            result.append(dict(type="http", name=name, url=url, headers=[]))
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{path} [{role}]: {exc}") from exc
