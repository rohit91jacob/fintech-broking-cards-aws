import pytest

from fbc.config import DEFAULT_USER_AGENT, Settings


def test_quoted_values_from_make_include_env_are_unquoted(monkeypatch):
    monkeypatch.setenv("FBC_SEC_USER_AGENT", '"my-project me@example.org"')
    monkeypatch.setenv("FBC_SEED", "'42'")
    s = Settings.from_env()
    assert s.sec_user_agent == "my-project me@example.org"
    assert s.seed == 42


def test_source_settings_defaults_and_overrides(monkeypatch):
    for name in ("FBC_USER_AGENT", "FBC_NSE_DOWNLOAD", "FBC_NSE_SESSION_TOLERANCE"):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert s.user_agent == DEFAULT_USER_AGENT
    assert s.nse_download is False  # NSE's terms prohibit automated collection
    assert s.nse_session_tolerance == 0
    monkeypatch.setenv("FBC_USER_AGENT", '"my-fork/2.0 (+https://example.org/my-fork)"')
    monkeypatch.setenv("FBC_NSE_DOWNLOAD", "yes")
    monkeypatch.setenv("FBC_NSE_SESSION_TOLERANCE", "1")
    s = Settings.from_env()
    assert s.user_agent == "my-fork/2.0 (+https://example.org/my-fork)"
    assert s.nse_download is True
    assert s.nse_session_tolerance == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [("FBC_NSE_DOWNLOAD", "maybe"), ("FBC_NSE_SESSION_TOLERANCE", "-1"), ("FBC_TRADING_DAYS", "0")],
)
def test_invalid_source_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()
