# Proxmox receiver setup

Run the receiver inside a dedicated unprivileged LXC container or Linux VM on
Proxmox. These commands assume Ubuntu 24.04 or Debian 13 with systemd, Python
3.12 or newer, and root access inside that guest. A small guest with 1 vCPU,
512 MB RAM and 2 GB disk is a starting allocation. Monitor the event log's disk
usage. Keep the Proxmox host for managing guests.

The receiver needs outbound HTTPS on TCP 443, Google's MCS push transport on
TCP 5228, DNS, and access to HA's internal URL. Use the guest's ordinary bridged
network. The HA webhook requires a source address HA regards as local. No LXC
nesting, privileged container, Docker, or incoming router port forwarding is
required. Keep certificate verification enabled and install your internal CA
in the guest's trust store if your HA URL uses one.

## Install inside the guest

Alternatively, run [`receiver/proxmox/create-lxc.sh`](../receiver/proxmox/create-lxc.sh)
as root on the Proxmox host to create and provision an unprivileged Ubuntu 24.04
container. It uses a free cluster ID, 1 CPU, 512 MB RAM, 2 GB disk, and DHCP on
`vmbr0`. Active storage is selected automatically, preferring `local-lvm` for
the root disk and `local` for templates. Override `CTID`, `BRIDGE`, `ROOT_STORAGE`,
`TEMPLATE_STORAGE`, or `DISK_GB` as environment variables when needed. The script
pins the receiver revision and leaves Blink registration for your interactive
session. On failure it preserves the guest for inspection.

The script also configures the dedicated guest's system journal with a 16 MiB
persistent target, 4 MiB segments and an 8 MiB volatile target. Active journal
files and rotation can add overhead above the configured target. The event
queue has its own 8 MiB cap. Filesystem, OS and package data use additional disk.

Run as root inside the guest. The checkout and virtual environment stay owned
by root so the service account can only change its state directory.

```sh
apt-get update
apt-get install -y python3 python3-venv ca-certificates git
git clone --branch security-proxmox https://github.com/jonNewton010/ha-blink-live-events.git /opt/blink-live-events
cd /opt/blink-live-events
git rev-parse HEAD
python3 -m venv .venv
.venv/bin/python -m pip install -r receiver/requirements.txt
useradd --system --user-group --home-dir /var/lib/blink-live-events --no-create-home --shell /usr/sbin/nologin blink-live-events
install -d -o blink-live-events -g blink-live-events -m 0700 /var/lib/blink-live-events
install -d -m 0700 /etc/blink-live-events
install -m 0600 receiver/systemd/receiver.env.example /etc/blink-live-events/receiver.env
install -m 0644 receiver/systemd/blink-live-events.service /etc/systemd/system/blink-live-events.service
```

Use the commit identified in the security review for installation. Once the
branch is downloaded, `git checkout <reviewed-commit>` selects that exact
revision. Updates are manual and should be reviewed before installing them.

Install `custom_components/blink_live_events` from the same revision in HA and
restart HA. Add **Blink Live Events** under **Settings → Devices & services**.
Its **Configure → View receiver URL** screen provides the private webhook URL.
Edit `/etc/blink-live-events/receiver.env` and replace the example URL with that
address. Use an address reachable from the guest, with working local DNS.
The receiver ignores ambient HTTP proxy settings and rejects HTTP redirects.
Point it at the final webhook URL directly.

## Register the receiver

Run from `/opt/blink-live-events` in an interactive guest console or SSH terminal.
Both registration commands use the same account and state directory as the
service. Registration uses the bundled Firebase client identifiers by default.

```sh
sudo -u blink-live-events .venv/bin/python -m receiver.blink_live_events --data-dir /var/lib/blink-live-events register-fcm
sudo -u blink-live-events .venv/bin/python -m receiver.blink_live_events --data-dir /var/lib/blink-live-events register-blink
```

If `sudo` is unavailable, run `apt-get install -y sudo` first. Open the printed
Blink URL in your browser. Enter your Blink password and verification code on
Blink's hosted page, then paste the resulting callback URL into the terminal's
hidden prompt. Treat the callback and state files as secrets. Tokens are stored
in `/var/lib/blink-live-events`, with directory mode 0700 and file mode 0600.

Start the service after registration succeeds.

```sh
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/blink-live-events.service
systemctl enable --now blink-live-events
journalctl -u blink-live-events -f
```

Generate a motion event at the front door. Confirm the receiver logs receipt
and forwarding, and HA emits `blink_live_events_event` with the correct camera
name. Test again after a guest reboot. A connected FCM socket alone does not
verify Blink registration or delivery. Live view is supplied by your existing
camera integration after an HA automation receives this event.

## Maintenance

The event journal `/var/lib/blink-live-events/messages.ndjson` contains raw push
metadata and delivery checkpoints. Keep it private with the session tokens.
It is capped at 8 MiB and compacted to at most 4 MiB when the next append
would exceed that cap. Compaction removes delivered events and diagnostics while
preserving pending events and initialization state. If pending events alone
exceed the target, the oldest or individually oversized events are discarded
and a warning reports the count. One listener process must own each state directory.
An atomic replacement temporarily needs room for both files, up to about 12 MiB.
Protect guest backups as
they contain usable session tokens. FCM token renewal and re-association with
Blink can require repeating registration. Stop the service before re-registering.

```sh
systemctl stop blink-live-events
# Repeat the relevant registration commands above.
systemctl start blink-live-events
```

For an expired identity that requires replacing state, keep a private backup,
then use a new empty private data directory with the same ownership. Do not
publish diagnostics containing the state files or raw captures.

## Why the upstream Docker image exists

The image supplies Python 3.12, installs `blinkpy==0.25.9`, copies the source,
and sets the receiver entry point. Compose supplies a private persistent volume,
a non-root user, a read-only filesystem, and restart behaviour. These are
packaging and process isolation choices. This setup supplies those functions
through a Python virtual environment, a dedicated Unix account, systemd and
the Proxmox guest boundary.

The separate receiver owns an FCM connection, private identity files, worker
threads and signal handlers. The HA component only accepts local webhook
events and publishes event entities. Embedding the receiver inside HA would
require refactoring those lifecycle responsibilities and dependencies.
