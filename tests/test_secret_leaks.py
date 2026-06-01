from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_secret_leaks_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_secret_leaks.py"
    )
    spec = importlib.util.spec_from_file_location("check_secret_leaks", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secret_leaks_accepts_placeholders(tmp_path: Path) -> None:
    module = _load_secret_leaks_module()
    path = tmp_path / "README.md"
    path.write_text(
        "\n".join(
            (
                'api_key = "${API_KEY}"',
                'password = "changeme-placeholder"',
                'token = "__token__"',
            )
        ),
        encoding="utf-8",
    )

    assert module._failures([tmp_path]) == []


def test_secret_leaks_rejects_quoted_secret_assignment(tmp_path: Path) -> None:
    module = _load_secret_leaks_module()
    value = "s3cr3t-" + ("A" * 24)
    path = tmp_path / "settings.py"
    path.write_text(f'api_key = "{value}"\n', encoding="utf-8")

    failures = module._failures([tmp_path])

    assert any("hard-coded secret assignment" in failure for failure in failures)


def test_secret_leaks_rejects_unquoted_env_secret(tmp_path: Path) -> None:
    module = _load_secret_leaks_module()
    value = "secret-" + ("B" * 24)
    path = tmp_path / ".env"
    path.write_text(f"export ACCESS_TOKEN={value}\n", encoding="utf-8")

    failures = module._failures([tmp_path])

    assert any("environment value" in failure for failure in failures)


def test_secret_leaks_rejects_cloud_access_key(tmp_path: Path) -> None:
    module = _load_secret_leaks_module()
    key = "AKIA" + ("C" * 16)
    path = tmp_path / "notes.md"
    path.write_text(f"credential: {key}\n", encoding="utf-8")

    failures = module._failures([tmp_path])

    assert any("cloud access key" in failure for failure in failures)


def test_secret_leaks_rejects_private_key_block(tmp_path: Path) -> None:
    module = _load_secret_leaks_module()
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    path = tmp_path / "key.pem"
    path.write_text(f"{marker}\n", encoding="utf-8")

    failures = module._failures([tmp_path])

    assert any("private key block" in failure for failure in failures)


def test_secret_leaks_rejects_secret_containing_placeholder_substring(
    tmp_path: Path,
) -> None:
    # A real credential that merely *contains* an allow-list word as a
    # substring must not be allow-listed (regression: unanchored allow-list).
    module = _load_secret_leaks_module()
    value = "examplekJHGsecretLIVE" + ("9" * 8)
    path = tmp_path / "settings.py"
    path.write_text(f'api_key = "{value}"\n', encoding="utf-8")

    failures = module._failures([tmp_path])

    assert any("hard-coded secret assignment" in failure for failure in failures)


def test_secret_leaks_rejects_prefixed_env_secret_name(tmp_path: Path) -> None:
    # The secret keyword is embedded after a prefix + underscore; ``\b`` would
    # not fire here, so a prefixed env var must still be detected.
    module = _load_secret_leaks_module()
    value = "r3al-" + ("E" * 24)
    path = tmp_path / ".env"
    path.write_text(f"DATABASE_PASSWORD={value}\n", encoding="utf-8")

    failures = module._failures([tmp_path])

    assert any("environment value" in failure for failure in failures)


def test_secret_leaks_skips_binary_and_cache_files(tmp_path: Path) -> None:
    module = _load_secret_leaks_module()
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    value = "secret-" + ("D" * 24)
    (cache / "cached.py").write_text(f'api_key = "{value}"\n', encoding="utf-8")
    (tmp_path / "image.png").write_bytes(f"\0api_key='{value}'".encode())

    assert module._failures([tmp_path]) == []
