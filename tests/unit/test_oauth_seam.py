"""The OAuth-client seam (mise-nujina, W4 of the estate rebuild).

One engine, told from outside which OAuth client to sign in with
(MISE_EN_SPACE_OAUTH_CLIENT) and where its token lives (MISE_EN_SPACE_DATA_DIR). Three
promises are pinned here:

- Both unset is the pre-seam behaviour: the bundled client, the flavour's
  own data dir, the flavour's own Keychain service, no adoption, no refusal.
- A client named from outside is authoritative: a broken one refuses and
  never falls back to the bundled client (a different Workspace's).
- Moving onto the seam costs no re-consent: an empty store adopts, by copy,
  the pre-seam token minted by the SAME client — and refuses one minted by
  another client, which would act as the other Workspace under this name.
"""

import json
import logging
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

import oauth_config
import token_store
from oauth_config import (
    ClientNotConfigured,
    cli_env_prefix,
    configured_client_id,
    oauth_client_file,
    read_client_id,
    resolve_data_dir,
)

# Captured at import, before conftest points the live binding at a
# hermetic stand-in for each test.
REAL_PRE_SEAM_SERVICES = token_store.PRE_SEAM_KEYCHAIN_SERVICES

ITV_ID = "111111111111-itv.apps.googleusercontent.com"
HOME_ID = "222222222222-home.apps.googleusercontent.com"


def _client_file(path: Path, client_id: str) -> Path:
    path.write_text(json.dumps({"installed": {
        "client_id": client_id,
        "client_secret": "public-by-design",
        "redirect_uris": ["http://localhost"],
    }}))
    return path


def _token(client_id: str, marker: str) -> str:
    return json.dumps({
        "token": f"ya29.{marker}",
        "refresh_token": f"1//{marker}",
        "client_id": client_id,
        "client_secret": "public-by-design",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


@pytest.fixture(autouse=True)
def _normal_mode(monkeypatch):
    """Not guest mode: the seam sits beside MISE_TOKEN_PATH, not under it."""
    monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("MISE_CREDENTIALS", raising=False)


@pytest.fixture
def no_keychain():
    with patch("token_store._has_keychain", return_value=False):
        yield


@pytest.fixture
def pre_seam(tmp_path):
    """Two pre-seam flavour stores: ITV's holds an ITV token, home's a home one."""
    itv = tmp_path / "data" / "mise-batterie-de-savoir"
    home = tmp_path / "data" / "mise-home"
    for d, cid, marker in ((itv, ITV_ID, "itv"), (home, HOME_ID, "home")):
        d.mkdir(parents=True)
        (d / "token.json").write_text(_token(cid, marker))
    with (
        patch("token_store.PRE_SEAM_DATA_DIRS", (itv, home)),
        patch("token_store._LEGACY_TOKEN_PATH", tmp_path / "nowhere" / "token.json"),
    ):
        yield {"itv": itv, "home": home}


# =============================================================================
# Which client
# =============================================================================


class TestClientResolution:
    def test_unset_is_the_bundled_client(self):
        assert oauth_client_file() == oauth_config.BUNDLED_CLIENT_FILE

    def test_env_client_wins(self, tmp_path, monkeypatch):
        f = _client_file(tmp_path / "home.json", HOME_ID)
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(f))
        assert oauth_client_file() == f
        assert configured_client_id() == HOME_ID

    def test_named_but_missing_client_refuses_without_fallback(self, tmp_path, monkeypatch):
        """The bundled client belongs to a different Workspace — never fall through."""
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(tmp_path / "absent.json"))
        with pytest.raises(ClientNotConfigured, match="will not fall back"):
            oauth_client_file()
        with pytest.raises(ClientNotConfigured):
            configured_client_id()

    def test_named_client_without_client_id_refuses(self, tmp_path, monkeypatch):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({"type": "authorized_user"}))
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(f))
        with pytest.raises(ClientNotConfigured, match="client_id"):
            oauth_client_file()

    def test_engine_with_no_client_is_not_configured(self, tmp_path):
        with patch("oauth_config.BUNDLED_CLIENT_FILE", tmp_path / "none.json"):
            with pytest.raises(ClientNotConfigured, match="MISE_EN_SPACE_OAUTH_CLIENT"):
                oauth_client_file()
            assert configured_client_id() is None

    def test_read_client_id_takes_web_clients_too(self, tmp_path):
        f = tmp_path / "web.json"
        f.write_text(json.dumps({"web": {"client_id": ITV_ID}}))
        assert read_client_id(f) == ITV_ID


# =============================================================================
# Which store
# =============================================================================


class TestDataDir:
    def test_unset_is_the_flavour_dir(self):
        assert resolve_data_dir() == oauth_config._DEFAULT_DATA_DIR
        assert oauth_config._DEFAULT_DATA_DIR.parts[-4:] == (
            ".claude", "plugins", "data", "mise-batterie-de-savoir")

    def test_env_dir_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(tmp_path / "kit-data"))
        assert resolve_data_dir() == tmp_path / "kit-data"

    def test_relative_dir_refuses(self, monkeypatch):
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", "relative/dir")
        with pytest.raises(ValueError, match="absolute"):
            resolve_data_dir()

    def test_jdx_mise_data_dir_does_not_engage_the_seam(self, tmp_path, monkeypatch, no_keychain):
        """MISE_DATA_DIR is jdx/mise's own documented override — the unrelated
        version manager's users export it. It must not move mise's token store
        or turn adoption on (the short name did, essayeur 2026-09-27)."""
        monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "jdx-mise-tools"))
        monkeypatch.setenv("MISE_OAUTH_CLIENT", str(tmp_path / "absent.json"))
        assert resolve_data_dir() == oauth_config._DEFAULT_DATA_DIR
        assert oauth_client_file() == oauth_config.BUNDLED_CLIENT_FILE
        assert token_store._seam_in_use() is False

    def test_pre_seam_dirs_name_both_flavours(self):
        names = [d.name for d in oauth_config.PRE_SEAM_DATA_DIRS]
        assert names == ["mise-batterie-de-savoir", "mise-home"]


class TestCliEnvPrefix:
    def test_empty_when_unset(self):
        assert cli_env_prefix() == ""

    def test_carries_both_values_shell_quoted(self, monkeypatch):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", "/k it/client.json")
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", "/data")
        assert cli_env_prefix() == "MISE_EN_SPACE_OAUTH_CLIENT='/k it/client.json' MISE_EN_SPACE_DATA_DIR=/data "


class TestKeychainService:
    def test_unset_is_the_flavour_service(self):
        assert token_store.keychain_service() == token_store.KEYCHAIN_SERVICE

    def test_supplied_client_keys_the_entry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "c.json", HOME_ID)))
        assert token_store.keychain_service() == f"{token_store.KEYCHAIN_SERVICE}:{HOME_ID}"

    def test_pre_seam_services_name_both_flavours(self):
        assert REAL_PRE_SEAM_SERVICES == ("mise-oauth-token", "mise-home-oauth-token")


# =============================================================================
# No re-consent: adoption by client_id
# =============================================================================


class TestAdoption:
    def test_empty_store_adopts_the_matching_client_token(
        self, tmp_path, monkeypatch, no_keychain, pre_seam, caplog
    ):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "home.json", HOME_ID)))
        store = tmp_path / "kit-data"
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(store))
        target = store / "token.json"

        with caplog.at_level(logging.WARNING, logger="token_store"):
            assert token_store.resolve_token_path(target) == target

        adopted = json.loads(target.read_text())
        assert adopted["client_id"] == HOME_ID  # the home token, not ITV's
        assert adopted["refresh_token"] == "1//home"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        # A copy: the pre-seam store is left for older versions still reading it.
        assert json.loads((pre_seam["home"] / "token.json").read_text())["client_id"] == HOME_ID
        assert "Adopted the pre-seam token" in caplog.text

    def test_itv_client_adopts_the_itv_token(self, tmp_path, monkeypatch, no_keychain, pre_seam):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "itv.json", ITV_ID)))
        target = tmp_path / "kit-data" / "token.json"
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(target.parent))
        token_store.resolve_token_path(target)
        assert json.loads(target.read_text())["refresh_token"] == "1//itv"

    def test_no_matching_client_adopts_nothing(self, tmp_path, monkeypatch, no_keychain, pre_seam):
        other = "333333333333-other.apps.googleusercontent.com"
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "o.json", other)))
        target = tmp_path / "kit-data" / "token.json"
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(target.parent))
        assert token_store.resolve_token_path(target) == target
        assert not target.exists()
        assert token_store.has_token(target) is False

    def test_seam_unset_never_adopts(self, tmp_path, monkeypatch, no_keychain, pre_seam):
        """Pre-seam behaviour: an empty store stays empty, whatever sits nearby.

        The bundled client is made to match a pre-seam token on purpose: with
        a non-matching client the refusal would come from client_id matching
        and this test could not see the seam guard at all.
        """
        monkeypatch.setattr(
            "oauth_config.BUNDLED_CLIENT_FILE", _client_file(tmp_path / "bundled.json", ITV_ID))
        target = tmp_path / "elsewhere" / "token.json"
        assert token_store.resolve_token_path(target) == target
        assert not target.exists()
        assert token_store.has_token(target) is False

    def test_data_dir_alone_adopts_for_the_bundled_client(
        self, tmp_path, monkeypatch, no_keychain, pre_seam
    ):
        monkeypatch.setattr(
            "oauth_config.BUNDLED_CLIENT_FILE", _client_file(tmp_path / "bundled.json", ITV_ID))
        target = tmp_path / "kit-data" / "token.json"
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(target.parent))
        token_store.resolve_token_path(target)
        assert json.loads(target.read_text())["client_id"] == ITV_ID

    def test_has_token_counts_an_adoptable_token(self, tmp_path, monkeypatch, no_keychain, pre_seam):
        """Else setup_oauth would send a signed-in user through consent again."""
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "home.json", HOME_ID)))
        target = tmp_path / "kit-data" / "token.json"
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(target.parent))
        assert token_store.has_token(target) is True
        assert not target.exists()  # presence check only; adoption happens on load

    def test_keychain_pre_seam_service_is_adopted_on_macos(self, tmp_path, monkeypatch, pre_seam):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "home.json", HOME_ID)))
        target = tmp_path / "kit-data" / "token.json"
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(target.parent))
        entries = {"mise-home-oauth-token": _token(HOME_ID, "keychain-home")}
        monkeypatch.setattr("token_store.PRE_SEAM_KEYCHAIN_SERVICES", REAL_PRE_SEAM_SERVICES)
        with patch("token_store.get_from_keychain", side_effect=lambda service=None: entries.get(service)):
            token_store.resolve_token_path(target)
        assert json.loads(target.read_text())["refresh_token"] == "1//keychain-home"


class TestForeignTokenRefused:
    def test_token_from_another_client_refuses(self, tmp_path, monkeypatch, no_keychain):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "itv.json", ITV_ID)))
        target = tmp_path / "kit-data" / "token.json"
        target.parent.mkdir()
        target.write_text(_token(HOME_ID, "home"))
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(target.parent))
        with pytest.raises(FileNotFoundError, match="two different Workspace identities") as e:
            token_store.resolve_token_path(target)
        assert "222222222222" in str(e.value) and "111111111111" in str(e.value)
        assert "setup_oauth" in str(e.value)

    def test_matching_token_passes(self, tmp_path, monkeypatch, no_keychain):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(_client_file(tmp_path / "itv.json", ITV_ID)))
        target = tmp_path / "token.json"
        target.write_text(_token(ITV_ID, "itv"))
        assert token_store.resolve_token_path(target) == target

    def test_seam_unset_does_not_check(self, tmp_path, no_keychain):
        """Pre-seam behaviour: whatever token sits in the flavour's store loads."""
        target = tmp_path / "token.json"
        target.write_text(_token(HOME_ID, "home"))
        assert token_store.resolve_token_path(target) == target


# =============================================================================
# setup_oauth and the CLI speak the seam
# =============================================================================


FAKE_URL = "https://accounts.google.com/o/oauth2/auth?state=st&client_id=x"


class TestSetupOauthUsesTheSuppliedClient:
    def test_mints_with_the_supplied_client_and_prints_the_env(self, tmp_path, monkeypatch):
        client = _client_file(tmp_path / "home.json", HOME_ID)
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(client))
        monkeypatch.setenv("MISE_EN_SPACE_DATA_DIR", str(tmp_path / "kit-data"))
        monkeypatch.setattr("tools.setup_oauth.TOKEN_FILE", tmp_path / "kit-data" / "token.json")
        (tmp_path / "kit-data").mkdir()
        from tools.setup_oauth import do_setup_oauth
        with (
            patch("tools.setup_oauth.has_token", return_value=False),
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL) as mint,
            patch("tools.setup_oauth.can_open_browser", return_value=False),
            patch("tools.setup_oauth.subprocess.Popen"),
        ):
            result = do_setup_oauth()
        assert mint.call_args.kwargs["credentials_path"] == str(client)
        assert result["cues"]["oauth_client"] == str(client)
        assert f"MISE_EN_SPACE_OAUTH_CLIENT={client}" in result["message"]
        assert f"MISE_EN_SPACE_DATA_DIR={tmp_path / 'kit-data'}" in result["cues"]["fallback"]
        assert "uv run python -m auth --code" in result["message"]

    def test_broken_supplied_client_refuses_before_minting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(tmp_path / "absent.json"))
        from tools.setup_oauth import do_setup_oauth
        with patch("tools.setup_oauth.get_auth_url") as mint:
            result = do_setup_oauth()
        assert result["error"] is True and result["kind"] == "invalid_input"
        assert "MISE_EN_SPACE_OAUTH_CLIENT" in result["message"]
        assert "Reinstall" not in result["message"]  # the kit named it; reinstalling mise won't help
        mint.assert_not_called()


class TestCliRefusesAnUnconfiguredClient:
    def test_exits_before_any_oauth_call(self, tmp_path, monkeypatch, capsys):
        import auth
        monkeypatch.setenv("MISE_EN_SPACE_OAUTH_CLIENT", str(tmp_path / "absent.json"))
        monkeypatch.setattr("sys.argv", ["auth"])
        with (
            patch("auth.get_auth_url") as mint,
            patch("auth.authenticate") as exchange,
            pytest.raises(SystemExit) as e,
        ):
            auth.main()
        assert e.value.code == 1
        assert "MISE_EN_SPACE_OAUTH_CLIENT" in capsys.readouterr().out
        mint.assert_not_called()
        exchange.assert_not_called()
