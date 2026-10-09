from fbc.config import Settings


def test_quoted_values_from_make_include_env_are_unquoted(monkeypatch):
    monkeypatch.setenv("FBC_SEC_USER_AGENT", '"my-project me@example.org"')
    monkeypatch.setenv("FBC_SEED", "'42'")
    s = Settings.from_env()
    assert s.sec_user_agent == "my-project me@example.org"
    assert s.seed == 42
