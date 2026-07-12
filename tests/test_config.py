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


def test_miner_defaults():
    from pae.config import Settings

    s = Settings(_env_file=None)
    assert s.miner_lookback_days == 60
    assert s.miner_run_hour_utc == 9  # 03:00/04:00 America/Chicago
    assert s.miner_local_tz == "America/Chicago"
    assert s.miner_min_occurrences == 4
    assert s.miner_tod_min_support == 0.5
    assert s.miner_tod_tolerance_minutes == 45.0
    assert s.miner_schedule_std_minutes == 2.0
    assert s.miner_pair_window_minutes == 5.0
    assert s.miner_pair_min_confidence == 0.6
    assert s.miner_pair_min_lift == 3.0
