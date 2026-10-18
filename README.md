# GeoPulse for Home Assistant

Connect Home Assistant to your self-hosted [GeoPulse](https://github.com/tess1o/geopulse) server with one token, in both directions:

- **Home Assistant → GeoPulse:** forward `device_tracker` and `person` entities to GeoPulse. Points are queued on disk and retried, so outages don't lose data, and recorder history can be backfilled.
- **GeoPulse → Home Assistant:** your current stay or trip and its place, today's totals, friends on the map, and events for automations (geofence enter/leave, stay/trip started/ended, tracking lost/resumed).

**Requirements:** GeoPulse 2.0 or newer, Home Assistant 2026.3 or newer, [HACS](https://hacs.xyz/).

## Contents

- [Installation](#installation)
- [Setup](#setup)
- [Options](#options)
- [Entities](#entities)
- [Forwarding locations](#forwarding-locations)
- [Events and automations](#events-and-automations)
- [Actions (services)](#actions-services)
- [Changing the URL or token](#changing-the-url-or-token)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
- [Development](#development)

## Installation

GeoPulse is a custom integration: Home Assistant doesn't ship it, so you install it with HACS before you can add it. Until it's installed and Home Assistant has restarted, the **Add integration** button in [Setup](#setup) fails with *"This integration does not support configuration via the UI"*.

### 1. Install HACS

Skip this step if **HACS** is already in your Home Assistant sidebar.

1. Follow the official guide for your installation type: [Download HACS](https://hacs.xyz/docs/use/download/download/). On Home Assistant OS, install the **Get HACS** add-on; on Docker or Core, run the download script inside the config directory.
2. Restart Home Assistant.
3. Clear your browser cache or reload the page with Ctrl/Cmd+Shift+R.
4. Go to **Settings → Devices & services → Add integration**, search for **HACS** and add it. Accept the acknowledgements, then open the GitHub link shown, enter the device code and authorize HACS.
5. **HACS** now appears in the sidebar.

Details and troubleshooting: [HACS: initial configuration](https://hacs.xyz/docs/use/configuration/basic/).

### 2. Install GeoPulse with HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tess1o&repository=geopulse-homeassistant&category=integration)

1. Click the button above and confirm, or add the repository yourself: in **HACS** open the menu (⋮, top right) → **Custom repositories**, enter `https://github.com/tess1o/geopulse-homeassistant`, choose type **Integration** and click **Add**.
2. Search for **GeoPulse** in HACS, open it and click **Download**. Keep the latest version unless you need a specific one.
3. Restart Home Assistant: **Settings → System → ⋮ → Restart Home Assistant**. A reload is not enough for a new integration.

Then continue with [Setup](#setup).

To update later, HACS shows an update for GeoPulse under **Settings → Updates**; install it and restart Home Assistant.

<details>
<summary>Manual installation without HACS</summary>

Copy `custom_components/geopulse_ha` from this repository into `<config>/custom_components/geopulse_ha` of your Home Assistant installation and restart Home Assistant. You will have to repeat this for every update.
</details>

## Setup

### 1. Create a token in GeoPulse

In GeoPulse, open **Location Sources → Add → Home Assistant → GeoPulse integration → Create a new token**. GeoPulse creates an API token with the scopes `read` and `gps:ingest`, connects a Home Assistant location source to it, and shows the **URL** and the **token**.

Copy both now: the token is shown only once. If you lose it, create a new one.

> Already have an API token (for example from **Profile → Security**)? Choose **Use an existing token** instead, so that GeoPulse connects a Home Assistant location source to it. Without a connected location source GeoPulse refuses forwarded locations.

> **Only want GeoPulse data in Home Assistant?** If your phone already sends locations to GeoPulse through another app, such as OwnTracks, Home Assistant doesn't need to send anything. Create a token under **Profile → Security → API tokens** with **Read only** access instead, and don't add a location source. The integration then skips step 3 and shows only your status, events and friends; see [Token scopes](#token-scopes).

### 2. Add the integration in Home Assistant

[![Add the GeoPulse integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=geopulse_ha)

Click the button above, or go to **Settings → Devices & services → Add integration → GeoPulse**, and fill in:

| Field | Description |
|---|---|
| **GeoPulse URL** | The address of your GeoPulse server as Home Assistant reaches it, for example `http://192.168.1.10:8080` or `https://geopulse.example.com`. Any path is ignored. |
| **API token** | The token from step 1. |
| **Verify SSL certificate** | Turn off only for a self-signed certificate. |

### 3. Choose the trackers to forward

Pick the `device_tracker` and `person` entities whose locations should go to GeoPulse. You can leave this empty and choose later in the [options](#options).

- A mobile app `device_tracker` gives the best data. `person` entities usually lack speed, altitude and battery.
- Several devices of the same person are fine, but two devices that are in different places at the same time make the GeoPulse timeline jump between them.

### One account per person

One config entry is one GeoPulse account, which is one person. Choose only trackers that belong to that person. For each family member, create a token in *their* GeoPulse account and add the integration again with it. Adding the same GeoPulse account twice is refused.

### Token scopes

The token's scopes decide what you get:

| Token scopes | Result |
|---|---|
| `read` + `gps:ingest` (recommended) | Everything |
| `read` only | Read-only: your location, status, events, friends. No forwarding: the trackers step is skipped, the forwarding options and the ingest sensors are left out. |
| `gps:ingest` only | Sender only: forwarding, the server and ingest status entities. No status, events or friends. |
| `all` | Works, but raises a repair issue: a full-access token can also change and delete your data |

The integration reads the scopes again on every start, so changing a token's scopes in GeoPulse takes effect after reloading the integration.

## Options

Open **Settings → Devices & services → GeoPulse → Configure**. Saving reloads the integration. Which options appear depends on the [token scopes](#token-scopes).

| Option | Default | Needs | Description |
|---|---|---|---|
| Trackers to forward | none | `gps:ingest` | `device_tracker` and `person` entities to send to GeoPulse. |
| Heartbeat interval | 15 min | `gps:ingest` | Re-send an unchanged position after this long, see [Forwarding](#forwarding-locations). 0 turns it off. |
| Send battery level | on | `gps:ingest` | Include the battery level with forwarded and backfilled points. |
| Fix time attribute | empty | `gps:ingest` | Tracker attribute with the time of the GPS fix, for example `last_timestamp` for iCloud3, see [Forwarding](#forwarding-locations). Empty uses the time Home Assistant updated the tracker. |
| Status poll interval | 60 s (min 30) | `read` | How often to read the current stay or trip and today's totals. Stay, trip and tracking events follow this poll. |
| Geofence events poll interval | 30 s (min 15) | `read` | How often to check for new geofence enter/leave events. |
| Friends | on | `read` | Friends who share their live location, with their trackers, sensors and events. Checked every minute. Turning it off removes the friend devices. |

## Entities

The integration creates a **GeoPulse &lt;name&gt;** device for the account, where &lt;name&gt; is the account's full name or email. Entity ids below assume a user called Alice.

### Status (needs `read`)

| Entity | Description |
|---|---|
| `device_tracker.geopulse_alice_location` | Where GeoPulse last saw you, from whichever location source sent the latest point (OwnTracks, the Home Assistant app, …); attributes `last_seen`, `received_at`, `location_source`. Add it to your `person` to use GeoPulse for presence and zones, see [Using GeoPulse for presence](#using-geopulse-for-presence) |
| `sensor.geopulse_alice_activity` | `stay`, `trip`, `data_gap` (no recent points), `unknown` or `no_data` (GeoPulse has no timeline yet); attribute `since` |
| `sensor.geopulse_alice_place` | The place of the current stay, empty during a trip; attributes `favorite_id`, `location_source`, `city`, `country`, `latitude`, `longitude` |
| `sensor.geopulse_alice_activity_since` | Start of the current stay, trip or data gap |
| `sensor.geopulse_alice_activity_duration` | Length of the current stay, trip or data gap |
| `binary_sensor.geopulse_alice_on_trip` | On during a trip; attributes `distance_m`, `movement_type` |
| `sensor.geopulse_alice_trip_distance` | Distance of the current trip; unavailable outside a trip |
| `sensor.geopulse_alice_distance_today` | Distance travelled today |
| `sensor.geopulse_alice_trips_today`, `_stays_today`, `_places_today` | Number of trips, stays and distinct places today |
| `sensor.geopulse_alice_moving_time_today` | Time spent on trips today |
| `sensor.geopulse_alice_last_point` | Time of the latest GPS point GeoPulse stored; attributes `received_at`, `source_type`, `accuracy`, `battery` |
| `binary_sensor.geopulse_alice_tracking` | Off during a data gap or while GeoPulse has no GPS data; unknown until GeoPulse has generated a timeline |
| `event.geopulse_alice_geopulse_activity` | GeoPulse events, see [Events and automations](#events-and-automations) |

"Today" is the current day in the timezone set in your GeoPulse profile. Distance, trips, stays and moving time today are daily totals that restart at midnight, so Home Assistant's long-term statistics can show them per day, week or month. Places today counts distinct places and isn't summed across days.

### Using GeoPulse for presence

`device_tracker.geopulse_alice_location` follows your latest point in GeoPulse. That's useful when your phone sends locations to GeoPulse with another app, such as OwnTracks, and Home Assistant has no tracker of its own for you: add the tracker to your person under **Settings → People**, and zones and presence automations work with it.

- It updates with the status poll (every minute by default), so it's up to a minute behind GeoPulse, which is itself as recent as your phone's last report.
- GeoPulse's trackers can't be forwarded back to GeoPulse. They aren't offered in the trackers list, and when a forwarded person takes its location from a GeoPulse tracker, that update is skipped, so only its other trackers are sent.

### Server and ingest

| Entity | Description |
|---|---|
| `binary_sensor.geopulse_alice_server` | On while the GeoPulse server and its database are up (diagnostic, checked every minute); attributes `status`, `database` |
| `update.geopulse_alice_geopulse_server` | Shows the running GeoPulse version and whether a newer release exists, with a link to its release notes. Informational only: it cannot install anything. Checked every 30 minutes. |
| `sensor.geopulse_alice_ingest_status` | Needs `gps:ingest`. Health of the location source connected to the token: `ok`, `rejected`, `auth_failed`, `no_data` or `disabled` (diagnostic); attributes with the time and code of the last saved, rejected and authentication-failed point |
| `sensor.geopulse_alice_last_data_received` | Needs `gps:ingest`. When GeoPulse last received data from this location source |

### Forwarded trackers (needs `gps:ingest`)

Each forwarded tracker gets its own device, named after the tracker and linked to the account device, with a **Last sent** sensor: the time a point of that tracker was last delivered, with attributes `source_entity_id`, `queue_length` (points of this tracker waiting to be sent) and `last_error`. Removing a tracker from the options removes its device.

### Friends (needs `read` and the Friends option)

Each GeoPulse friend who shares their live location with you gets a device named after them, with:

| Entity | Description |
|---|---|
| `device_tracker.<friend>` | Their position on the map; attributes `last_seen`, `activity`, `activity_duration_s`, `last_location` |
| **Activity** sensor | `stay` or `trip` |
| **Activity duration** sensor | Length of the current stay or trip |
| **Place** sensor | Where they are staying; empty while on a trip |
| **Battery** sensor | Only when GeoPulse knows their battery level |

Friends who stop sharing become unavailable; friends you remove in GeoPulse disappear. Their stays and trips also fire events, see [Friends' events](#friends-events).

## Forwarding locations

- A point is sent when a tracker's latitude, longitude, GPS accuracy or altitude changes. States without coordinates (for example `home` from a router-based tracker) are skipped.
- **Heartbeat:** when a tracker reports an unchanged position and nothing was sent for the heartbeat interval, the position is sent again, so GeoPulse doesn't record a data gap while you stay put.
- **Fix time:** each point is timestamped with the time Home Assistant updated the tracker. Some trackers, such as [iCloud3](https://github.com/gcobb321/icloud3), refresh their state while keeping an older GPS fix, so set **Fix time attribute** to the attribute holding the fix time (`last_timestamp` for iCloud3). Unix seconds and ISO 8601 times are accepted; times without a time zone are read as local time. Trackers with that attribute then send points stamped with the fix time, a new fix is sent even when the position is unchanged, and a fix that isn't newer than the last one sent is skipped. The heartbeat therefore has no effect on them. Trackers without the attribute work as before. A `person` doesn't carry the attribute, so forward the tracker itself.
- Speed, altitude, accuracy and battery are sent when known and omitted otherwise. Battery comes from the tracker's `battery_level` attribute or, failing that, a battery sensor on the same device.
- Points go into a persistent queue (up to 10,000 points, stored on disk, so a restart doesn't lose them) and are sent in batches of up to 500. On errors the integration retries after 30 s, 1 min, 5 min, then every 15 min. The **Last sent** sensors show the queue length and the last error.
- Points GeoPulse already has are dropped by GeoPulse, so re-sending is harmless.

To send locations that Home Assistant recorded before you set up the integration, or while forwarding was off, use the [`geopulse_ha.backfill`](#actions-services) action.

## Events and automations

GeoPulse doesn't send events itself. The integration derives them: stays, trips and tracking lost/resumed by comparing successive status polls (filling in anything in between from the GeoPulse timeline), and geofence enter/leave by polling GeoPulse's geofence events. Geofences are set up in GeoPulse. Each event fires both:

- the `event.<…>_geopulse_activity` entity, with `event_type` one of `geofence_enter`, `geofence_leave`, `stay_started`, `stay_ended`, `trip_started`, `trip_ended`, `tracking_lost`, `tracking_resumed`, and the event details as attributes;
- the `geopulse_ha_event` bus event, with `type`, `occurred_at`, `entry_id`, `user_id`, `entity_id` (the event entity), `device_id` (the account device), `stale` and the same details.

Details use snake_case keys, for example:

| Event | Attributes |
|---|---|
| `geofence_enter` / `geofence_leave` | `geofence_id`, `geofence_name`, `subject_user_id`, `subject_name`, `subject_is_self`, `latitude`, `longitude` |
| `stay_started` | `place_name`, `favorite_id`, `location_source`, `city`, `country`, `latitude`, `longitude`, `started_at` |
| `stay_ended` | `place_name`, `favorite_id`, `started_at`, `ended_at`, `duration_seconds`, … |
| `trip_started` / `trip_ended` | `movement_type`, `started_at`, `ended_at`, `duration_seconds`, `distance_meters`, … |
| `tracking_lost` / `tracking_resumed` | `last_point_at`, `lost_at`, `resumed_at`, `duration_seconds` |

Geofence events include other people the geofence watches; `subject_is_self` tells whether it's you.

### Example: an automation on the event entity

Trigger on the entity's state (it changes on every event) and check the event type in a condition. A trigger on `attribute: event_type` would miss the second of two events of the same type in a row.

```yaml
triggers:
  - trigger: state
    entity_id: event.geopulse_alice_geopulse_activity
    not_from: unavailable
conditions:
  - "{{ trigger.to_state.attributes.event_type == 'stay_started' }}"
  - "{{ trigger.to_state.attributes.place_name == 'Gym' }}"
actions:
  - action: media_player.play_media
    target:
      entity_id: media_player.headphones
    data:
      media_content_id: https://example.com/workout.mp3
      media_content_type: music
```

### Example: an automation on the bus event

The bus event also carries friends' events and stale events (see below):

```yaml
triggers:
  - trigger: event
    event_type: geopulse_ha_event
    event_data:
      type: trip_ended
      stale: false
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        {{ trigger.event.data.subject_name | default('You', true) }} finished a trip
        {%- if trigger.event.data.distance_meters %} of
        {{ (trigger.event.data.distance_meters / 1000) | round(1) }} km{% endif %}
```

### Timing

Stay, trip and tracking events can only be detected after GeoPulse's timeline update (every 5 minutes) plus the next status poll, so a stay usually shows up 10–15 minutes after it started. Geofence events arrive on the next geofence poll. For instant "arrived home" automations, Home Assistant's own zones remain the better tool; these events suit notifications, logging and trip summaries.

The first poll after setup only remembers where things stand and fires nothing. Changes to the *Unknown* or *No data* status (for example while GeoPulse regenerates the timeline) fire nothing either.

### Stale events

An event is **stale** when it happened more than 60 minutes ago (a phone uploaded hours of queued points and the timeline caught up), or when Home Assistant couldn't reach GeoPulse for more than 15 minutes and the event happened before that outage ended. Stale events don't trigger the event entity, so a late "arrived home" doesn't run your automations. They still fire the bus event with `stale: true`.

### Friends' events

When a friend's activity changes between two friends polls (every 60 s), `stay_ended`, `trip_started`, `trip_ended` and `stay_started` fire the bus event with `subject_user_id`, `subject_name` and `subject_is_self: false`, and appear in Activity on the friend's device. They don't trigger the event entity, so its automations keep reacting to you only; for a friend, trigger on the bus event or on their Activity or Place sensor. A stay or trip that starts and ends between two polls is missed, and friends' trips have no distance or movement type.

### Activity (logbook)

GeoPulse events appear in **Activity**, and on the account device's and the event entity's pages, as readable lines with the time they actually happened, for example *Alice trip ended, 3.9 km by car (08:44–08:53)* or *Bob entered Office (17:02)*. Late events end with "reported late".

The event entity also writes a row for every event, showing only the time it fired. To keep just the readable lines on the main Activity page, hide it in `configuration.yaml` (device and entity pages ignore this setting):

```yaml
logbook:
  exclude:
    entity_globs:
      - event.*_geopulse_activity
```

## Actions (services)

### `geopulse_ha.backfill`

Sends the recorder history of a `device_tracker` or `person` entity to GeoPulse. GeoPulse drops points it already has, so overlapping ranges are fine. The **Fix time attribute** option applies here too. Needs a token with `gps:ingest` and the recorder.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | The tracker or person whose history to send |
| `start` | yes | Start of the period |
| `end` | no | End of the period; defaults to now. At most 31 days after `start`. |
| `config_entry_id` | no | The GeoPulse account to send to. Needed only when you have several accounts that can send and the entity isn't forwarded by exactly one of them. |

It returns how many points were read (`points`) and what GeoPulse did with them (`accepted`, `saved`, `duplicate`, `filtered`). Try it from **Developer tools → Actions**:

```yaml
action: geopulse_ha.backfill
data:
  entity_id: device_tracker.alice_phone
  start: "2026-09-01 00:00:00"
  end: "2026-09-30 23:59:59"
```

Your recorder only keeps history for `purge_keep_days` (10 days by default), so older periods return no points.

### `geopulse_ha.flush_queue`

Sends queued points now instead of waiting for the next retry. Optional `config_entry_id` limits it to one account; by default it flushes all of them.

## Changing the URL or token

- **New URL, or a new token for the same account:** **Settings → Devices & services → GeoPulse → ⋮ → Reconfigure**.
- **Token expired or revoked:** Home Assistant shows a *Reauthentication required* notice; open it and paste a new token.

In both cases the token must belong to the same GeoPulse account. For a different account, add the integration again.

## Troubleshooting

- **Repairs:** check **Settings → System → Repairs**. The integration raises issues for full-access tokens, tokens expiring within 14 days, tokens refused for lacking `gps:ingest` while sending locations (a read-only token never sends, so it raises nothing), tokens not connected to a location source, a disabled location source, and servers older than GeoPulse 2.0. Each issue says how to fix it.
- **Locations don't arrive:** look at the **Last sent** sensor of the tracker (`queue_length`, `last_error`) and the **Ingest status** sensor of the account. In GeoPulse, check that the Home Assistant location source is enabled.
- **"This integration does not support configuration via the UI":** GeoPulse isn't installed on that Home Assistant yet, or it hasn't restarted since. Follow [Installation](#installation), restart, then add the integration again. The HACS button shows the same message when HACS itself isn't installed.
- **Cannot connect during setup:** the URL must be reachable from Home Assistant, not just from your browser. Inside Docker, `localhost` is the Home Assistant container itself; use the host's IP or the GeoPulse container name.
- **Diagnostics:** download them from the integration's ⋮ menu; tokens, URLs, names and precise coordinates are redacted.
- **Debug logs:** turn on **Enable debug logging** on the integration page, or add to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.geopulse_ha: debug
  ```

## Removal

1. **Settings → Devices & services → GeoPulse → ⋮ → Delete.** This also deletes the queued points that weren't sent yet and the stored event state.
2. In HACS, open GeoPulse and choose **Remove**, then restart Home Assistant.
3. Optionally revoke the token in GeoPulse under **Profile → Security** and delete the Home Assistant location source. Data already in GeoPulse stays there.

## Development

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv venv -p 3.14 .venv
uv pip install -p .venv/bin/python -r requirements_dev.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

To try it in a local Home Assistant, install `homeassistant` into the venv, link the integration into a config directory and start it (`.ha-config/` is git-ignored):

```bash
mkdir -p .ha-config/custom_components
ln -s "$PWD/custom_components/geopulse_ha" .ha-config/custom_components/geopulse_ha
.venv/bin/hass -c .ha-config
```

CI runs ruff, the tests against the minimum (2026.3) and latest supported Home Assistant, hassfest and the HACS validation. A release's tag must match `version` in `custom_components/geopulse_ha/manifest.json`.

## License

[Apache-2.0](LICENSE)
