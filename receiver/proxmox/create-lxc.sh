#!/usr/bin/env bash
# Run as root in the Proxmox host shell.
set -Eeuo pipefail

RECEIVER_REPOSITORY=https://github.com/jonNewton010/ha-blink-live-events.git
RECEIVER_REVISION=e454280c5f2de05b9cbde7df08bafb6a90e001b8
CTID=${CTID:-}
BRIDGE=${BRIDGE:-vmbr0}
ROOT_STORAGE=${ROOT_STORAGE:-}
TEMPLATE_STORAGE=${TEMPLATE_STORAGE:-}
DISK_GB=${DISK_GB:-2}
created=0

fail() { printf '%s\n' "$*" >&2; exit 1; }
on_error() {
    printf '\nSetup failed on line %s.\n' "$1" >&2
    if ((created)); then
        printf 'Container %s has been preserved for inspection with pct enter %s.\n' "$CTID" "$CTID" >&2
    fi
}
trap 'on_error "$LINENO"' ERR

[[ $EUID == 0 ]] || fail 'Run this script as root in the Proxmox host Shell.'
for command in pct pveam pvesm pvesh awk sort ip; do
    command -v "$command" >/dev/null || fail "Required Proxmox command missing: $command"
done
[[ $(uname -m) == x86_64 ]] || fail 'This script uses the AMD64 Ubuntu template.'
[[ $BRIDGE =~ ^[a-zA-Z0-9_.-]+$ ]] || fail 'Invalid bridge name.'
ip link show dev "$BRIDGE" >/dev/null || fail "Bridge $BRIDGE does not exist. Set BRIDGE to your LAN bridge."
[[ $DISK_GB =~ ^[1-9][0-9]*$ ]] && ((DISK_GB >= 2)) || fail 'DISK_GB must be an integer of at least 2.'

select_storage() {
    local content=$1 preferred=$2 requested=$3 available
    available=$(pvesm status --content "$content" | awk 'NR > 1 && $3 == "active" {print $1}')
    if [[ -n $requested ]]; then
        awk -v name="$requested" '$0 == name {found=1} END {exit !found}' <<< "$available" ||
            fail "Storage $requested must be active and support $content."
        printf '%s\n' "$requested"
    elif awk -v name="$preferred" '$0 == name {found=1} END {exit !found}' <<< "$available"; then
        printf '%s\n' "$preferred"
    else
        [[ -n $available ]] || fail "No active storage supports $content."
        printf '%s\n' "${available%%$'\n'*}"
    fi
}

ROOT_STORAGE=$(select_storage rootdir local-lvm "$ROOT_STORAGE")
TEMPLATE_STORAGE=$(select_storage vztmpl local "$TEMPLATE_STORAGE")
if [[ -z $CTID ]]; then
    CTID=$(pvesh get /cluster/nextid)
fi
[[ $CTID =~ ^[1-9][0-9]{2,8}$ ]] || fail 'CTID must be a valid Proxmox container ID.'
# Check the cluster, including existing QEMU VMs and other nodes.
pvesh get /cluster/nextid --vmid "$CTID" >/dev/null

printf 'Creating LXC %s on %s with 1 CPU, 512 MB RAM, %s GB disk and DHCP on %s.\n' "$CTID" "$ROOT_STORAGE" "$DISK_GB" "$BRIDGE"
pveam update
template=$(pveam available --section system |
    awk '$2 ~ /^ubuntu-24\.04-standard_.*_amd64\.tar\.(zst|gz|xz)$/ {print $2}' |
    sort -V | tail -n 1)
[[ -n $template ]] || fail 'No Ubuntu 24.04 AMD64 system template is available.'
volume="$TEMPLATE_STORAGE:vztmpl/$template"
if ! pveam list "$TEMPLATE_STORAGE" | awk -v volume="$volume" '$1 == volume {found=1} END {exit !found}'; then
    pveam download "$TEMPLATE_STORAGE" "$template"
fi

pct create "$CTID" "$volume" \
    --hostname blink-events \
    --unprivileged 1 \
    --cores 1 --memory 512 --swap 256 \
    --rootfs "$ROOT_STORAGE:$DISK_GB" \
    --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp,type=veth,firewall=1" \
    --onboot 1 \
    --description 'Blink push receiver. Source pinned to a reviewed revision; private state and bounded event journal.'
created=1
pct start "$CTID"

printf 'Waiting for guest DNS and DHCP...\n'
ready=0
for ((attempt=0; attempt<30; attempt++)); do
    if pct exec "$CTID" -- getent ahostsv4 archive.ubuntu.com >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
((ready)) || fail "Guest networking is unavailable. Inspect container $CTID and DHCP on $BRIDGE."

pct exec "$CTID" -- bash -s -- "$RECEIVER_REPOSITORY" "$RECEIVER_REVISION" <<'GUEST'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y python3 python3-venv ca-certificates git sudo nano
git clone "$1" /opt/blink-live-events
cd /opt/blink-live-events
git checkout --detach "$2"
python3 -m venv .venv
.venv/bin/python -m pip install -r receiver/requirements.txt
useradd --system --user-group --home-dir /var/lib/blink-live-events --no-create-home --shell /usr/sbin/nologin blink-live-events
install -d -o blink-live-events -g blink-live-events -m 0700 /var/lib/blink-live-events
install -d -m 0700 /etc/blink-live-events
install -m 0600 receiver/systemd/receiver.env.example /etc/blink-live-events/receiver.env
install -m 0644 receiver/systemd/blink-live-events.service /etc/systemd/system/blink-live-events.service

# This guest is dedicated to the receiver. Bound both persistent and volatile
# system journals and avoid a second copy being forwarded to syslog.
install -d -m 0755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/blink-size-limits.conf <<'JOURNAL'
[Journal]
Storage=persistent
SystemMaxUse=16M
SystemMaxFileSize=4M
RuntimeMaxUse=8M
RuntimeMaxFileSize=2M
ForwardToSyslog=no
JOURNAL
systemctl restart systemd-journald
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/blink-live-events.service
.venv/bin/python -m receiver.blink_live_events --help >/dev/null
GUEST

printf '\nContainer %s is ready. Enter it with\n\npct enter %s\n\n' "$CTID" "$CTID"
cat <<'NEXT'
After installing Blink Live Events in Home Assistant, copy its receiver URL.
Inside the container, edit the configuration and register with Blink

nano /etc/blink-live-events/receiver.env
cd /opt/blink-live-events
sudo -u blink-live-events .venv/bin/python -m receiver.blink_live_events --data-dir /var/lib/blink-live-events register-fcm
sudo -u blink-live-events .venv/bin/python -m receiver.blink_live_events --data-dir /var/lib/blink-live-events register-blink
systemctl enable --now blink-live-events
journalctl -u blink-live-events -f

The event journal is capped at 8 MiB and compacts to at most 4 MiB.
System journals have a 16 MiB persistent target plus active-file overhead.
NEXT
