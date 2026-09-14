# Registration Token Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the device registration token from the `PRIVATE_REGISTRATION_TOKEN` environment variable into `config.toml`.

**Architecture:** Keep `DATABASE_URL` as the only required environment variable and keep `VIRGO_CONFIG_FILE` as the optional TOML path override. Load `private_registration_token`, `business_api_token`, and `device_online_window_seconds` from the TOML file in `Settings.from_env()`, then pass the resulting `Settings` object through the existing application wiring unchanged.

**Tech Stack:** Python 3, dataclasses, `tomllib`, pytest, FastAPI application factory, PowerShell startup examples.

---

### Task 1: Update configuration unit tests

**Files:**
- Modify: `tests/test_config.py`

- [ ] **Step 1: Replace environment-token tests with TOML-token tests**

Replace the contents of `tests/test_config.py` with:

```python
from pathlib import Path

import pytest

from app.config import Settings


VALID_CONFIG = (
    'private_registration_token = "registration-secret"\n'
    'business_api_token = "business-secret"\n'
    'device_online_window_seconds = 300\n'
)


def test_settings_reads_environment_and_config_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "chosen-config.toml")
    read_paths = []

    def read_text(path, encoding):
        read_paths.append((path, encoding))
        return VALID_CONFIG

    monkeypatch.setattr(Path, "read_text", read_text)

    settings = Settings.from_env()

    assert settings.database_url == "postgresql://db/example"
    assert settings.private_registration_token == "registration-secret"
    assert settings.business_api_token == "business-secret"
    assert settings.device_online_window_seconds == 300
    assert read_paths == [(Path("chosen-config.toml"), "utf-8")]


@pytest.mark.parametrize("invalid_value", ["", " \t "])
def test_settings_rejects_empty_or_whitespace_database_url(monkeypatch, invalid_value):
    monkeypatch.setenv("DATABASE_URL", invalid_value)
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: VALID_CONFIG)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings.from_env()


def test_settings_rejects_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: VALID_CONFIG)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings.from_env()


@pytest.mark.parametrize(
    "contents,match",
    [
        (
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = 300\n',
            "private_registration_token",
        ),
        (
            'private_registration_token = "   "\n'
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = 300\n',
            "private_registration_token",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'device_online_window_seconds = 300\n',
            "business_api_token",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'business_api_token = "   "\n'
            'device_online_window_seconds = 300\n',
            "business_api_token",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = 0\n',
            "device_online_window_seconds",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = true\n',
            "device_online_window_seconds",
        ),
    ],
)
def test_settings_rejects_invalid_config(monkeypatch, contents, match):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "invalid-config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: contents)

    with pytest.raises(RuntimeError, match=match):
        Settings.from_env()


def test_settings_rejects_missing_config_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "missing.toml")

    def missing_file(self, encoding):
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "read_text", missing_file)

    with pytest.raises(RuntimeError, match="VIRGO_CONFIG_FILE"):
        Settings.from_env()
```

- [ ] **Step 2: Run configuration tests and verify they fail before implementation**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
```

Expected: tests fail because `Settings.from_env()` still requires `PRIVATE_REGISTRATION_TOKEN` from the environment and does not read `private_registration_token` from TOML.

---

### Task 2: Update `Settings.from_env()`

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Change configuration loading implementation**

In `app/config.py`, update `Settings.from_env()` to:

```python
    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DATABASE_URL")
        if database_url is None or not database_url.strip():
            raise RuntimeError("DATABASE_URL is required")

        config_path = Path(os.getenv("VIRGO_CONFIG_FILE", "config.toml"))
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise RuntimeError(
                f"VIRGO_CONFIG_FILE could not be loaded: {config_path}"
            ) from error

        private_registration_token = config.get("private_registration_token")
        if (
            not isinstance(private_registration_token, str)
            or not private_registration_token.strip()
        ):
            raise RuntimeError(
                "private_registration_token is required in VIRGO_CONFIG_FILE"
            )

        business_api_token = config.get("business_api_token")
        if not isinstance(business_api_token, str) or not business_api_token.strip():
            raise RuntimeError("business_api_token is required in VIRGO_CONFIG_FILE")

        online_window = config.get("device_online_window_seconds", 300)
        if (
            isinstance(online_window, bool)
            or not isinstance(online_window, int)
            or online_window <= 0
        ):
            raise RuntimeError(
                "device_online_window_seconds must be a positive integer"
            )

        return cls(
            database_url=database_url,
            private_registration_token=private_registration_token,
            business_api_token=business_api_token,
            device_online_window_seconds=online_window,
        )
```

- [ ] **Step 2: Run configuration tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
```

Expected: all tests in `tests/test_config.py` pass.

---

### Task 3: Update local config templates and examples

**Files:**
- Modify: `config.example.toml`
- Modify: `config.toml`
- Modify: `.env.example`

- [ ] **Step 1: Update TOML templates**

Set `config.example.toml` to:

```toml
private_registration_token = "replace-with-a-long-random-registration-secret"
business_api_token = "replace-with-a-long-random-business-secret"
device_online_window_seconds = 300
```

Set `config.toml` to:

```toml
private_registration_token = "local-development-registration-token-change-me"
business_api_token = "local-development-business-token-change-me"
device_online_window_seconds = 300
```

- [ ] **Step 2: Update environment example**

Set `.env.example` to:

```env
DATABASE_URL=postgresql://admin:admin@127.0.0.1:5433/virgo_pg
VIRGO_CONFIG_FILE=config.toml
```

- [ ] **Step 3: Verify old environment key is gone from active config examples**

Run:

```powershell
rg "PRIVATE_REGISTRATION_TOKEN" config.example.toml config.toml .env.example
```

Expected: no matches.

---

### Task 4: Update integration tests that use `Settings.from_env()`

**Files:**
- Modify: `tests/integration/test_device_registration_api.py`

- [ ] **Step 1: Replace `PRIVATE_REGISTRATION_TOKEN` setup with TOML content**

In `tests/integration/test_device_registration_api.py`, replace each setup block that currently sets `PRIVATE_REGISTRATION_TOKEN` before calling `Settings.from_env()` with a `Path.read_text` monkeypatch that returns:

```python
(
    'private_registration_token = "registration-secret"\n'
    'business_api_token = "business-secret"\n'
    'device_online_window_seconds = 300\n'
)
```

Keep `DATABASE_URL` set to the test database DSN. Keep the request header value `Authorization: Bearer registration-secret`.

- [ ] **Step 2: Run the integration file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_registration_api.py -q
```

Expected: all tests in the file pass when PostgreSQL is available.

---

### Task 5: Update Apifox and project documentation

**Files:**
- Modify: `docs/apifox-seven-api-testing-guide.md`
- Modify: `docs/superpowers/specs/2026-06-21-device-registration-design.md`
- Modify: `docs/superpowers/specs/2026-06-22-message-create-design.md`

- [ ] **Step 1: Update Apifox startup section**

In `docs/apifox-seven-api-testing-guide.md`, remove:

```powershell
$env:PRIVATE_REGISTRATION_TOKEN='replace-with-your-registration-token'
```

Update the text so `registration_token` says it must equal `config.toml` field `private_registration_token`.

- [ ] **Step 2: Update historical design notes that describe current config**

In `docs/superpowers/specs/2026-06-21-device-registration-design.md`, change the configuration description from environment variable `PRIVATE_REGISTRATION_TOKEN` to `config.toml` field `private_registration_token`.

In `docs/superpowers/specs/2026-06-22-message-create-design.md`, change the note "`DATABASE_URL` 与 `PRIVATE_REGISTRATION_TOKEN` 继续由环境变量提供" to say only `DATABASE_URL` remains in environment variables and tokens live in TOML.

- [ ] **Step 3: Verify active docs no longer instruct setting the old environment variable**

Run:

```powershell
rg "setenv\\(\"PRIVATE_REGISTRATION_TOKEN\"|\\$env:PRIVATE_REGISTRATION_TOKEN|PRIVATE_REGISTRATION_TOKEN=.*|os.getenv\\(\"PRIVATE_REGISTRATION_TOKEN\"" app tests docs config.example.toml .env.example
```

Expected: no matches in active code, tests, current user-facing docs, or templates. Old plan documents may still mention historical implementation details if included in a wider search.

---

### Task 6: Final verification

**Files:**
- Check: all modified files

- [ ] **Step 1: Run targeted config tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
```

Expected: pass.

- [ ] **Step 2: Run API tests likely affected by settings**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_api.py tests\test_message_api.py tests\integration\test_device_registration_api.py -q
```

Expected: pass.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: pass.

- [ ] **Step 4: Inspect remaining references**

Run:

```powershell
rg "PRIVATE_REGISTRATION_TOKEN" app tests docs\\apifox-seven-api-testing-guide.md config.example.toml .env.example
```

Expected: no active-code, test, template, or Apifox-guide references remain.

---

## Self-review

- Spec coverage: The plan updates config loading, examples, Apifox documentation, current design docs, unit tests, integration tests, and verification.
- Placeholder scan: No `TBD`, `TODO`, or undefined follow-up steps are intentionally left.
- Type consistency: The TOML field name is consistently `private_registration_token`; the existing dataclass field remains `private_registration_token`.
