"""Tests for async_remove_config_entry_device (device-delete hook).

The conftest mocks the entire HA + integration package surface, so the real
custom_components.meshcore package can't be imported the normal way. Unlike the
standalone-logic-copy pattern used elsewhere, this hook is small and security
relevant, so we load the *real* function via importlib against the mocked
package and exercise it directly. We reuse the module's own mocked sentinels
(DOMAIN, CONF_REPEATER_SUBSCRIPTIONS, CONF_TRACKED_CLIENTS) as dict keys so the
membership logic operates on the same objects the live code sees.
"""
import importlib.util
import sys
from unittest.mock import MagicMock

import pytest

# Mocks that conftest does not provide but __init__.py imports at module load.
for _name in (
    "homeassistant.exceptions",
    "custom_components.meshcore.coordinator",
    "custom_components.meshcore.meshcore_api",
    "custom_components.meshcore.map_uploader",
    "custom_components.meshcore.mqtt_uploader",
    "custom_components.meshcore.services",
):
    sys.modules.setdefault(_name, MagicMock())


def _load_real_module():
    """Load the real custom_components/meshcore/__init__.py under the mocked package."""
    name = "custom_components.meshcore"
    spec = importlib.util.spec_from_file_location(
        name,
        "custom_components/meshcore/__init__.py",
        submodule_search_locations=["custom_components/meshcore"],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _load_real_module()
remove = MOD.async_remove_config_entry_device
DOMAIN = MOD.DOMAIN
CONF_REPEATER = MOD.CONF_REPEATER_SUBSCRIPTIONS
CONF_CLIENTS = MOD.CONF_TRACKED_CLIENTS

ENTRY_ID = "abc123entryid"
# A repeater configured with a 12-char prefix.
REPEATER_PREFIX = "aabbccddeeff"
CLIENT_PREFIX = "112233445566"
CONTACT_PREFIX = "778899aabbcc"
# Full 64-char public key whose first 12 chars match a configured repeater.
FULL_PUBKEY = REPEATER_PREFIX + "0" * 52


def _make_config_entry(repeaters=None, clients=None):
    entry = MagicMock()
    entry.entry_id = ENTRY_ID
    entry.data = {
        CONF_REPEATER: [{"pubkey_prefix": p} for p in (repeaters or [])],
        CONF_CLIENTS: [{"pubkey_prefix": p} for p in (clients or [])],
    }
    return entry


def _make_device(identifier):
    device = MagicMock()
    device.identifiers = {(DOMAIN, identifier)}
    return device


def _make_hass_no_coordinator():
    """coordinator absent from hass.data (entry mid-reload / startup)."""
    hass = MagicMock()
    hass.data = {}
    return hass


def _make_hass(contacts, last_update_success=True):
    """Coordinator present with data["contacts"] = contacts.

    last_update_success controls the .last_update_success attribute; defaults
    to True (normal operating state after at least one successful poll).
    """
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {"contacts": contacts}
    coordinator.last_update_success = last_update_success
    hass.data = {DOMAIN: {ENTRY_ID: coordinator}}
    return hass


def _make_hass_failed_refresh():
    """Coordinator present but data is None (never had a successful poll)."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = None
    coordinator.last_update_success = False
    hass.data = {DOMAIN: {ENTRY_ID: coordinator}}
    return hass


def _id(node_type, pubkey):
    return f"{ENTRY_ID}_{node_type}_{pubkey}"


# ---------------------------------------------------------------------------
# Hub / repeater / client — unaffected by the coordinator guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hub_device_refused():
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry()
    device = _make_device(ENTRY_ID)  # identifier == entry_id
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_live_repeater_refused():
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry(repeaters=[REPEATER_PREFIX])
    device = _make_device(_id("repeater", REPEATER_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_live_client_refused():
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry(clients=[CLIENT_PREFIX])
    device = _make_device(_id("client", CLIENT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_length_mismatch_repeater_still_refused():
    """Bug #1: identifier carries the full 64-char pubkey; config has 12 chars."""
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry(repeaters=[REPEATER_PREFIX])
    device = _make_device(_id("repeater", FULL_PUBKEY))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_orphan_repeater_allowed():
    """Repeater dropped from config is an orphan and may be removed."""
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry(repeaters=["000000000000"])
    device = _make_device(_id("repeater", REPEATER_PREFIX))
    assert await remove(hass, entry, device) is True


@pytest.mark.asyncio
async def test_foreign_device_allowed():
    """A device whose identifier belongs to another domain falls through to True."""
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry()
    device = MagicMock()
    device.identifiers = {("other_domain", "whatever")}
    assert await remove(hass, entry, device) is True


# ---------------------------------------------------------------------------
# Contact / unknown — coordinator guard in effect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_contact_refused():
    """A contact still present in the coordinator must not be deletable."""
    hass = _make_hass(contacts=[{"pubkey_prefix": CONTACT_PREFIX}])
    entry = _make_config_entry()
    device = _make_device(_id("contact", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_live_contact_full_pubkey_refused():
    """Contact identifier with a full 64-char pubkey, live in coordinator."""
    full = CONTACT_PREFIX + "f" * 52
    hass = _make_hass(contacts=[{"public_key": full}])
    entry = _make_config_entry()
    device = _make_device(_id("contact", full))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_live_unknown_refused():
    hass = _make_hass(contacts=[{"pubkey_prefix": CONTACT_PREFIX}])
    entry = _make_config_entry()
    device = _make_device(_id("unknown", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_orphan_contact_allowed():
    """Contact no longer present in the coordinator may be removed.

    Requires a trustworthy coordinator (last_update_success=True).
    """
    hass = _make_hass(contacts=[{"pubkey_prefix": "ffffffffffff"}])
    entry = _make_config_entry()
    device = _make_device(_id("contact", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is True


# ---------------------------------------------------------------------------
# New: present-coordinator-with-failed-refresh → refuse contact/unknown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_refresh_refuses_contact_removal():
    """Coordinator present but never had a successful poll (data is None).

    live_contact_prefixes would be empty — must NOT be treated as authoritative
    'zero contacts'.  Removal refused.
    """
    hass = _make_hass_failed_refresh()
    entry = _make_config_entry()
    device = _make_device(_id("contact", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_failed_refresh_refuses_unknown_removal():
    """Same guard applies to node_type='unknown'."""
    hass = _make_hass_failed_refresh()
    entry = _make_config_entry()
    device = _make_device(_id("unknown", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_last_update_failure_refuses_contact_removal():
    """Coordinator has stale data from a previous poll but last update failed.

    Even though coordinator.data is truthy, last_update_success=False means
    the snapshot may be out of date — refuse removal.
    """
    # Stale data: contact list does NOT contain CONTACT_PREFIX (looks like an
    # orphan), but coordinator is not trustworthy, so we must refuse.
    hass = _make_hass(contacts=[{"pubkey_prefix": "ffffffffffff"}], last_update_success=False)
    entry = _make_config_entry()
    device = _make_device(_id("contact", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_failed_refresh_allows_repeater_orphan():
    """Repeater / client removal is governed by config data only.

    The coordinator trustworthiness guard does NOT affect repeater or client
    devices; their removal is allowed when absent from config even when the
    coordinator is in a failed state.
    """
    hass = _make_hass_failed_refresh()
    entry = _make_config_entry(repeaters=["000000000000"])
    device = _make_device(_id("repeater", REPEATER_PREFIX))
    assert await remove(hass, entry, device) is True


# ---------------------------------------------------------------------------
# New: present-coordinator-with-successful-empty-data → allow orphan contact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_empty_contacts_allows_orphan_contact():
    """Coordinator trustworthy and reports zero contacts.

    An empty live_contact_prefixes here IS authoritative — the node is gone.
    Removal allowed.
    """
    hass = _make_hass(contacts=[], last_update_success=True)
    entry = _make_config_entry()
    device = _make_device(_id("contact", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is True


@pytest.mark.asyncio
async def test_successful_empty_contacts_allows_orphan_unknown():
    """Same for node_type='unknown'."""
    hass = _make_hass(contacts=[], last_update_success=True)
    entry = _make_config_entry()
    device = _make_device(_id("unknown", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is True


# ---------------------------------------------------------------------------
# New: coordinator-None contract — conservative, refuse contact/unknown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_coordinator_contact_refused():
    """Conservative contract: coordinator absent → refuse contact removal.

    Previously (before this hardening) a missing coordinator meant an empty
    live_contact_prefixes, which allowed removal.  The new contract is: treat
    coordinator-None as an untrustworthy state and refuse.  The user can retry
    once the integration has reloaded and the coordinator is live.
    """
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry()
    device = _make_device(_id("contact", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False


@pytest.mark.asyncio
async def test_missing_coordinator_unknown_refused():
    """Conservative contract also covers node_type='unknown'."""
    hass = _make_hass_no_coordinator()
    entry = _make_config_entry()
    device = _make_device(_id("unknown", CONTACT_PREFIX))
    assert await remove(hass, entry, device) is False
