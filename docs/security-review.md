# Blink Live Events security review

Reviewed on 21 November 2026. Upstream revision
`67af33462fd1e76786e8c93701923ed2870ace02`. Changes in the
`security-proxmox` branch address the findings below. This is a source review
and synthetic test run. Authentication and real camera delivery still require
validation with the owner's Blink account and Proxmox guest.

## Result

The reviewed receiver has no identified deliberate exfiltration, analytics,
remote command execution, video upload, or camera clip download path. Its active
network destinations are Blink authentication and API hosts, Google's Firebase
and MCS services, and the configured HA webhook. The receiver forwards event
metadata to HA. Camera streaming belongs to a separate integration.

This conclusion covers the repository's receiver, vendored FCM implementation,
HA component, examples, Docker files, and workflow configuration at the named
revision. A source review cannot guarantee the absence of every vulnerability,
malicious future update, compromised package, or compromised host. The installed
third-party dependency source and Python/OS runtimes have not received an
exhaustive independent audit.

## Data flow

| Destination or storage | Data involved | Source |
| --- | --- | --- |
| `api.oauth.blink.com` | Hosted browser sign-in, PKCE authorization-code exchange, access and refresh tokens | `receiver/blink_live_events/blink_account.py` |
| `rest-prod.immedia-semi.com` and regional `rest-<tier>.immedia-semi.com` | Bearer token, account/client discovery, FCM token registration, app and OS version, notification receipt identifiers | `blink_account.py` |
| `firebaseinstallations.googleapis.com` | Public Blink Firebase client identifiers, generated installation identity and installation tokens | `fcm_registration.py` |
| `android.clients.google.com` | Android-style check-in identity, registration and Google security tokens | `receiver/vendor/fcm_receiver/http_client.py` |
| `mtalk.google.com`, TCP 5228 | Google connection identity and security token, push deliveries and transport acknowledgements | `fcm_client.py`, `fcm_socket.py` |
| Configured HA webhook | Event ID hash, classification, camera name, category/type and timestamps | `ha_delivery.py` |
| Private receiver state directory | Blink access/refresh tokens, FCM credentials, account identifiers, raw push payloads and delivery history | `state.py`, `capture.py` |

Additional generic Firebase/Web Push registration functions exist in the
vendored library. They target `fcmregistrations.googleapis.com` and
`fcm.googleapis.com`; the standalone Android receiver entry point uses the
registration path described above. No network destination belonging to the
project author was found in the active runtime path. GitHub/PyPI/OS repositories
are contacted during installation and updates, separately from push reception.

The browser sign-in page receives the Blink password and MFA code. The receiver
accepts a hidden callback URL, validates its Blink host, path and OAuth state,
and exchanges the code using PKCE. It saves reusable access and refresh tokens
locally. Protect these tokens as credentials even though the receiver does not
collect the password. Atomic state writes enforce 0600 permissions in a 0700
directory. Raw captures are private files, but they have no encryption at rest.
Guest backups and host administrators can access them.

## Findings and changes

### Medium — HTTP redirects could move sensitive requests

Upstream uses urllib's default redirect handling for Blink, Google and HA
requests. Redirects may forward sensitive headers on applicable requests or
move an HA event away from the configured endpoint. This is a defensive finding;
no evidence of a provider redirect exploiting it was found.

Added `receiver/network.py` with redirects rejected and environment proxy
settings disabled. All receiver HTTP paths use it. HTTPS certificate and hostname
verification remain enabled. Configure the final HA address directly. An internal
CA must be installed in the guest trust store if required.

### Medium — Blink destinations lacked a check before attaching credentials

Regional API URLs interpolate the stored/provider-supplied tier. Crafted tier
text could change URL authority. Upstream trusts the authenticated provider and
local state, which reduces the exposure, but it should validate the final
address before attaching a bearer token.

The authenticated request helper now requires HTTPS, a Blink OAuth or regional
API hostname, and no user information or explicit port in the authority.
Regression tests verify external and misleading hostnames fail before any
network call.

### Medium — captures grow without a retention policy

`messages.ndjson` contains raw event payloads as well as delivery checkpoints.
It grows indefinitely, and delivery loads and scans the history. This can
consume guest disk and increase processing time. It also retains private camera
activity metadata. Ordinary log rotation can lose queue/checkpoint state.

The fork now caps the journal at 8 MiB and compacts it to at most 4 MiB.
Delivered messages are removed with their checkpoints, and initialization state
and pending deliveries are preserved. If pending records exceed the target,
oldest or individually oversized events are dropped with a count-only warning.
Writes and reads use a shared thread lock and replacement is atomic. One process
must own each data directory. Temporary replacement can use about 12 MiB total.
HA delivery
can retry old events after an outage, and delivery is not exactly once across a
crash between a successful POST and its checkpoint. Dashboard automations should
check event freshness and tolerate duplicate event IDs when wired in.

### Low — private runtime filenames were absent from general Git exclusions

Added exclusions for `blink-auth.json`, `fcm-state.json`, `messages.ndjson` and
`data/` to Git and Docker build context exclusions. Permissions and separation
from the source checkout remain the main protection. Git exclusions do not
protect files deliberately force-added or copied elsewhere.

### Supply chain and fork ownership

The direct runtime requirement pins `blinkpy==0.25.9`. It is imported only for
legacy iOS refresh constants when old session state is used. Fresh Android
sessions use the repository's own OAuth code. Its transitive requirements remain
unlocked, so a new installation can select different versions. The optional Web
Push crypto library branches are outside the active Android receive path.

`pip-audit -r receiver/requirements.txt` resolved 20 packages on Python 3.14 and
reported no known vulnerabilities on the review date. This is an advisory
check, with different platform/Python resolutions and newly discovered issues
requiring a fresh check. It does not certify dependency source integrity.

Upstream Docker deployment pulls a moving image tag. The fork's Compose file
and publishing workflows now point at `ghcr.io/jonnewton010/ha-blink-live-events`.
An image must be built/published in the fork before that optional Docker route
can be used. The Proxmox route installs from the reviewed source checkout.
Workflows use mutable external action tags and branches, so enabling image or
release automation warrants reviewing those actions and pinning their commits.
Source updates and workflow runs are separate decisions from running this
receiver on a guest. A fork preserves a revision for inspection, while updates
still need review.

## Proxmox and Docker

Docker supplies Python 3.12, dependency installation, an unprivileged process,
a volume and a restart policy. The Python entry point uses no Docker APIs.
The new `receiver/systemd/` unit runs it directly in a Linux guest, using a
dedicated user, private state, a root-owned environment file, restricted writable
paths, disabled privilege escalation and restart handling. The receiver opens
outbound connections and has no incoming listener.

An unprivileged LXC or a VM is assumed. The unit has not yet been run on the
owner's Proxmox system. The instructions use Ubuntu 24.04 or Debian 13, with
Python 3.12 or newer. Guest resource sizing is an initial estimate.

The HA component is separate because it registers event entities and a local
POST-only webhook, while the receiver manages threads, signal handling, FCM
identity and Blink session state. Running that code inside HA would require
lifecycle and dependency changes. The private webhook secret authorizes event
submission and must be kept private. HA itself enforces `local_only=True`.
The receiver trusts the explicitly configured webhook destination, so configure
only the intended internal HA address.

## Verification

- Existing receiver tests and new credential-destination/redirect regressions
  passed, 38 tests total, using Python 3.14.
- Existing HA integration tests passed, 9 tests, in an isolated Python 3.14
  environment with the Home Assistant test harness.
- Dependency advisory scan reported no known vulnerabilities for the 20
  resolved receiver dependencies.
- Real Blink sign-in, push latency, camera compatibility, Proxmox boot/restart,
  and certificate trust remain unverified. The setup guide includes those
  acceptance checks.

## Registration follow-up

A live setup returned JSON HTTP 404 with provider code 1624 at client update.
No authoritative mapping of that numeric code was located. The fork now calls
`/api/v2/users/info` before homescreen/client discovery, checks reported client
and account verification requirements, and saves discovered session metadata
before notification association. Current reference implementations include this
post-token account lookup. This is a compatibility correction requiring a live
retry to determine whether it resolves this account's 404.

Error responses are read up to 16 KiB and reduced to JSON/non-JSON classification,
a numeric provider code and an optional fixed error category. Arbitrary vendor
text, tokens, URLs and identifiers are excluded from diagnostics. Additional
privacy, initialization-order, verification and failed-association persistence
tests pass.

### Client identifier correction

A live diagnostic returned a positive numeric client ID in `/api/v2/users/info`
and a different, nonnumeric `Client-Id` homescreen header. The old receiver had
saved the header and interpolated it into the notification-registration route.
The receiver now uses and validates the authenticated account response's numeric
client ID, refreshes older saved header values during registration and rejects
missing or invalid account IDs before any update POST. It does not select a
client from a list of other signed-in devices. Tests reproduce conflicting,
opaque and missing homescreen headers and invalid account response client IDs.
Registration and real push delivery still require a live retry after this fix.
