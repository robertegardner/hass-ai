from pae.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.pae_read_only is True
    assert settings.ha_url == "http://homeassistant.iot:8123"
    assert settings.log_format == "json"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("HA_URL", "http://192.168.6.10:8123")
    monkeypatch.setenv("HA_TOKEN", "supersecret")
    monkeypatch.setenv("PAE_READ_ONLY", "false")
    settings = Settings(_env_file=None)
    assert settings.ha_url == "http://192.168.6.10:8123"
    assert settings.ha_token.get_secret_value() == "supersecret"
    assert settings.pae_read_only is False


def test_token_never_leaks_in_repr(monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "supersecret")
    settings = Settings(_env_file=None)
    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings.ha_token)


def test_ws_url_derivation():
    assert (
        Settings(_env_file=None, ha_url="http://homeassistant.iot:8123").ha_ws_url
        == "ws://homeassistant.iot:8123/api/websocket"
    )
    assert (
        Settings(_env_file=None, ha_url="https://ha.example.com/").ha_ws_url
        == "wss://ha.example.com/api/websocket"
    )
