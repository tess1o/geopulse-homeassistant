"""Tests for the GeoPulse config flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.geopulse_ha.const import (
    CONF_FRIENDS,
    CONF_GEOFENCE_INTERVAL,
    CONF_HEARTBEAT_MINUTES,
    CONF_SCOPES,
    CONF_SEND_BATTERY,
    CONF_STATUS_INTERVAL,
    CONF_TRACKERS,
    CONF_USER_ID,
    DOMAIN,
)

from .conftest import (
    API,
    TOKEN,
    URL,
    USER_ID,
    make_entry,
    mock_geopulse,
    setup_entry,
    token_payload,
)

USER_INPUT = {
    CONF_URL: f"{URL}/some/path/",
    CONF_TOKEN: TOKEN,
    CONF_VERIFY_SSL: True,
}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_full_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A read + ingest token goes through the trackers step."""
    mock_geopulse(aioclient_mock)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "trackers"
    assert result["description_placeholders"] == {"account": "Alice"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRACKERS: ["device_tracker.pixel_8"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "GeoPulse (Alice)"
    assert result["result"].unique_id == USER_ID
    assert result["data"][CONF_URL] == URL  # path stripped
    assert result["data"][CONF_USER_ID] == USER_ID
    assert result["data"][CONF_SCOPES] == ["read", "gps:ingest"]
    assert result["options"] == {CONF_TRACKERS: ["device_tracker.pixel_8"]}

    # The token is sent as a bearer token.
    headers = [
        call[3] for call in aioclient_mock.mock_calls if "api-tokens" in str(call[1])
    ]
    assert headers[0]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_read_only_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A read-only token explains that forwarding is disabled."""
    mock_geopulse(aioclient_mock, scopes=["read"])
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "read_only"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {}


@pytest.mark.usefixtures("mock_setup_entry")
async def test_ingest_only_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An ingest-only token uses userId from the token and skips /users/me."""
    mock_geopulse(aioclient_mock, scopes=["gps:ingest"])
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "trackers"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRACKERS: []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "GeoPulse (alice@example.com)"
    assert result["result"].unique_id == USER_ID
    assert not any(
        str(call[1]).endswith("/users/me") for call in aioclient_mock.mock_calls
    )


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("connection", "cannot_connect"),
        ("old_version", "unsupported_version"),
        ("unauthorized", "invalid_auth"),
        ("no_scopes", "insufficient_scope"),
        ("revoked", "invalid_auth"),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup: str,
    error: str,
) -> None:
    """Every validation error is shown on the user step."""
    if setup == "connection":
        aioclient_mock.get(f"{API}/system/version/status", exc=TimeoutError)
    elif setup == "old_version":
        mock_geopulse(aioclient_mock, version="1.9.3")
    elif setup == "unauthorized":
        mock_geopulse(aioclient_mock, token_status=401)
    elif setup == "no_scopes":
        mock_geopulse(aioclient_mock, scopes=["write"])
    elif setup == "revoked":
        aioclient_mock.get(
            f"{API}/api-tokens/current", json=token_payload(status="REVOKED")
        )
        mock_geopulse(aioclient_mock)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


@pytest.mark.usefixtures("mock_setup_entry")
async def test_snapshot_version_is_accepted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Development builds of v2 are accepted."""
    mock_geopulse(aioclient_mock, version="2.0.0-SNAPSHOT")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "trackers"


async def test_already_configured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same account cannot be added twice."""
    make_entry().add_to_hass(hass)
    mock_geopulse(aioclient_mock)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Reauth stores the new token."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "gp_sa_new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "gp_sa_new"


async def test_reauth_wrong_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token of another account is refused."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/api-tokens/current", json=token_payload(userId="someone-else")
    )
    mock_geopulse(aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "gp_sa_other"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_reconfigure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reconfigure updates URL and token."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: f"{URL}/", CONF_TOKEN: "gp_sa_new", CONF_VERIFY_SSL: False},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TOKEN] == "gp_sa_new"
    assert entry.data[CONF_VERIFY_SSL] is False


async def test_options(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Options show every field for a read + ingest token and reload the entry."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {
        CONF_TRACKERS,
        CONF_HEARTBEAT_MINUTES,
        CONF_SEND_BATTERY,
        CONF_STATUS_INTERVAL,
        CONF_GEOFENCE_INTERVAL,
        CONF_FRIENDS,
    }
    new_options = {
        CONF_TRACKERS: ["device_tracker.pixel_8"],
        CONF_HEARTBEAT_MINUTES: 0,
        CONF_SEND_BATTERY: False,
        CONF_STATUS_INTERVAL: 120,
        CONF_GEOFENCE_INTERVAL: 60,
        CONF_FRIENDS: False,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], new_options
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == new_options
    assert entry.runtime_data.forwarder.entity_ids == ["device_tracker.pixel_8"]
    assert entry.runtime_data.friends is None


async def test_options_read_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A read-only token has no forwarding options."""
    mock_geopulse(aioclient_mock, scopes=["read"])
    entry = make_entry(scopes=["read"], options={})
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {CONF_STATUS_INTERVAL, CONF_GEOFENCE_INTERVAL, CONF_FRIENDS}
