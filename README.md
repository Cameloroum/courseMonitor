# McGill Seat Monitor

A small Python service that checks McGill's Visual Schedule Builder (VSB) and sends an email when a configured course section becomes available. It only reads public guest data. It does **not** sign in to Minerva, store McGill credentials, or attempt registration.

The project targets Raspberry Pi OS Lite (64-bit) and Python 3.11 or newer. It has no third-party runtime dependencies.

## Typical Raspberry Pi deployment

The production service is installed on the Raspberry Pi at:

```text
Application:   /opt/mcgill-seat-monitor
Configuration: /etc/mcgill-seat-monitor.toml
Email secrets: /etc/mcgill-seat-monitor.env
Saved state:   /var/lib/mcgill-seat-monitor/state.json
Service unit:  /etc/systemd/system/mcgill-seat-monitor.service
Logs:          systemd journal
```

The examples below use a sample course target:

```text
Term:     Fall 2026 (202609)
Course:   GEOG 205
Activity: Lecture
Section:  001
CRN:      12345
Interval: 300 seconds
Email:    you@example.com
```

Connect from your computer with your Pi's SSH hostname or address:

```bash
ssh pi@PI_ADDRESS
```

The following sections are the day-to-day commands for a deployed service. The longer installation guide later in this document is used when installing, rebuilding, or moving it to another Pi.

### How the running monitor works

Every five minutes the service makes a read-only HTTPS request to McGill VSB, finds the configured CRN or activity/section, and converts VSB's response into `FULL`, `AVAILABLE`, `CLOSED`, or `UNKNOWN`. It compares that observation with the last good state in `/var/lib/mcgill-seat-monitor/state.json`.

When a target changes from a non-available state to `AVAILABLE`, the service sends one email. Repeated `AVAILABLE` polls do not send repeated email. If email delivery fails, the available state is not committed, so delivery is retried after the next successful poll. Network and server failures use retries and exponential backoff, while the last good seat state is retained.

### Change the courses being monitored

On the Pi, open the production configuration:

```bash
sudo nano /etc/mcgill-seat-monitor.toml
```

Each `[[courses]]` block is one target. For example:

```toml
[[courses]]
term = "202609"
subject = "GEOG"
number = "205"
activity = "Lec"
section = "001"
crn = "12345"
```

To monitor another section, add another complete block:

```toml
[[courses]]
term = "202701"
subject = "COMP"
number = "250"
activity = "Lec"
section = "002"
crn = "5678"
```

Use the actual McGill term code and CRN; CRNs can be reused in different terms. `crn` is optional, but it is the most exact selector. Without a CRN, both `activity` and `section` should be present so a lecture is not confused with a lab or tutorial.

After saving, validate before restarting:

```bash
/opt/mcgill-seat-monitor/.venv/bin/mcgill-seat-monitor \
  --config /etc/mcgill-seat-monitor.toml \
  --check-config
```

Expected output resembles:

```text
configuration valid: 2 target(s)
```

Apply the change and inspect the first live poll:

```bash
sudo systemctl restart mcgill-seat-monitor.service
sudo journalctl -u mcgill-seat-monitor.service -n 30 --no-pager
```

If a newly added section is already available, its first successful observation intentionally sends an email.

### Start, stop, restart, enable, and disable

```bash
# Start it now
sudo systemctl start mcgill-seat-monitor.service

# Stop it now; it remains enabled for the next boot
sudo systemctl stop mcgill-seat-monitor.service

# Gracefully stop and immediately start it
sudo systemctl restart mcgill-seat-monitor.service

# Start it automatically after future boots
sudo systemctl enable mcgill-seat-monitor.service

# Do not start it automatically after future boots
sudo systemctl disable mcgill-seat-monitor.service

# Stop it now and disable future automatic starts
sudo systemctl disable --now mcgill-seat-monitor.service
```

Check both current and boot-time status:

```bash
systemctl is-active mcgill-seat-monitor.service
systemctl is-enabled mcgill-seat-monitor.service
sudo systemctl status mcgill-seat-monitor.service
```

Healthy output is `active` and `enabled`. Follow logs in real time with:

```bash
sudo journalctl -u mcgill-seat-monitor.service -f
```

Unchanged successful polls are intentionally quiet. The saved state's `Modify` timestamp advances after a successful poll and is a useful health check:

```bash
sudo stat /var/lib/mcgill-seat-monitor/state.json
```

### Repository versus production files

This Git repository contains application code, tests, an example configuration, and the service-unit template. It does not contain the production Gmail app password or `/etc/mcgill-seat-monitor.env`.

Editing repository files on the Mac does not automatically change the running Pi. The deployed code is the root-owned copy in `/opt/mcgill-seat-monitor`; production course settings are in `/etc/mcgill-seat-monitor.toml`, and production secrets are in `/etc/mcgill-seat-monitor.env`.

## What was verified

As of 2026-08-26, guest VSB at [vsb.mcgill.ca](https://vsb.mcgill.ca/) uses lightweight HTTP requests:

1. `POST /api/string-to-filter` resolves a term and course name such as `COMP 250` to VSB's internal course key and validation value.
2. `GET /api/class-data` returns XML with course sections and current availability fields.

The live XML included the activity type, section number (`secNo`), block key, status, open seats (`os`), `isFull`, an unknown-availability flag, a closed flag, and waitlist fields. The monitor follows VSB's own `isFull` signal rather than trying to infer capacity from page text.

These endpoints are semi-public but undocumented. McGill can change them without notice. That is why HTTP access, parsing, state decisions, and notifications live in separate modules and why saved XML fixtures cover the parser without depending on the live site.

### HTTP, polling, and parsing

An **HTTP request** is a small message sent to a web server; the response contains data. **Polling** means repeating a read-only request at a restrained interval. Here the default is once every five minutes, not continuously. **Parsing** turns VSB's XML text into structured Python values such as section `001`, 17 open seats, and state `AVAILABLE`.

The service resolves each unique course once per process and batches the sections in one class-data request per term. Temporary failures are retried, then the main loop uses exponential **backoff**: repeated failures cause increasingly long waits, up to one hour. This reduces load on McGill and avoids making an outage worse.

## Architecture

```text
config.toml
    |
    v
VsbProvider -> XML parser -> Observation -> transition/state store -> SMTP notifier
                                      |
                                      +-> timestamped logs
```

- `config.py` validates the TOML configuration. TOML is readable by Python 3.11 without another package.
- `provider.py` performs bounded HTTP requests, retries transient failures, and batches courses by term.
- `parser.py` maps verified VSB XML fields to a small state model.
- `state.py` atomically persists the last good state in JSON. A temporary file plus rename prevents a power loss from leaving a half-written file.
- `notifier.py` contains the SMTP implementation. A future Discord or Telegram notifier can implement the same small `send` operation without changing VSB parsing.
- `monitor.py` coordinates polling, changes, backoff, logging, and notification retry.
- `cli.py` provides normal, one-shot, config-check, simulation, and notification-test modes.
- `systemd/` contains the service definition used after the manual checks work.

**State** is remembered information from the previous observation. The transition `FULL -> AVAILABLE` is meaningful. Seeing `AVAILABLE` again five minutes later is not. This is **idempotence**: repeating the same successful input does not repeat the external action.

| VSB signal | Monitor state |
|---|---|
| unknown-availability flag | `UNKNOWN` |
| closed flag or non-active status | `CLOSED` |
| `isFull=1` | `FULL` |
| `isFull=0` | `AVAILABLE` |
| malformed/missing data | poll error; retain last good state |

The first observation of an already-available target sends an email. Thereafter, another email is sent only after the target has left `AVAILABLE` and becomes available again. If SMTP delivery fails, the new available state is not committed, so the next successful poll retries the email.

## Why email for version 1

SMTP email works on a headless Pi, is supported by many providers, and needs no bot application or phone-side setup. Python includes the SMTP and TLS libraries. Its drawback is credential setup: providers such as Gmail generally require two-factor authentication plus an app password, and provider-specific SMTP limits apply. A Discord webhook is easier to configure but ties the service to Discord. The notification module is intentionally replaceable.

## Project layout

```text
mcgill-seat-monitor/
├── README.md
├── pyproject.toml
├── config.example.toml
├── src/mcgill_seat_monitor/
├── tests/
│   └── fixtures/
└── systemd/
    └── mcgill-seat-monitor.service
```

`config.toml`, `.env`, virtual environments, bytecode, and local state are ignored by Git. SMTP passwords and tokens must never be added to source files or committed.

## Run it manually first

On Raspberry Pi OS Lite:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
cd ~/mcgill-seat-monitor
python3 -m venv .venv
.venv/bin/python -m pip install .
cp config.example.toml config.toml
nano config.toml
```

If the project is currently only on your Mac, copy it to the Pi before the `cd` command:

```bash
scp -r "/Users/ghali/Documents/01 McGill/mcgill-seat-monitor" pi@PI_ADDRESS:/home/pi/
```

Replace `pi`, `PI_ADDRESS`, and `/home/pi` if your Pi username or home directory differs. SSH access is only a transport here; this project does not configure SSH, Tailscale, a VPN, or firewall rules.

### Configure courses

The term is McGill's numeric term code; for example, the live VSB term code for Fall 2026 is `202609`. Keep numeric-looking values quoted in TOML.

```toml
[[courses]]
term = "202609"
subject = "COMP"
number = "250"
activity = "Lec"
section = "001"

[[courses]]
term = "202609"
subject = "MATH"
number = "141"
activity = "Lec"
section = "002"
crn = "1234" # optional; use the VSB/Banner class key when known
```

`activity` prevents a lecture section `001` from being confused with a lab or tutorial section with the same number. `crn` is the strongest selector and, when present, takes priority over `section`. A successful first observation logs the matched block key so it can be copied into the config.

Validate syntax without contacting McGill:

```bash
.venv/bin/mcgill-seat-monitor --config config.toml --check-config
```

### Configure secrets outside the repository

For a manual run, create an ignored file readable only by your account:

```bash
touch .env
chmod 600 .env
nano .env
```

Example contents:

```bash
MSM_SMTP_USERNAME="your-email@example.com"
MSM_SMTP_PASSWORD="your-smtp-app-password"
MSM_EMAIL_FROM="your-email@example.com"
MSM_EMAIL_TO="destination@example.com"
```

Load these values into the current shell's **environment** and test delivery:

```bash
set -a
. ./.env
set +a
.venv/bin/mcgill-seat-monitor --config config.toml --test-notification
```

Environment variables let the process receive secrets at runtime without placing them in application code or Git. Do not paste passwords directly into command arguments; command arguments can be visible to other local processes.

### Test without waiting for a real seat

The simulator does not contact McGill or send email:

```bash
.venv/bin/mcgill-seat-monitor --simulate FULL,AVAILABLE,AVAILABLE,ERROR,FULL,AVAILABLE
```

Expected notification decisions:

```text
UNSEEN -> FULL; notify=no
FULL -> AVAILABLE; notify=yes
AVAILABLE -> AVAILABLE; notify=no
AVAILABLE -> ERROR; notify=no
AVAILABLE -> FULL; notify=no
FULL -> AVAILABLE; notify=yes
```

Run the offline test suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Finally, make one live read-only poll:

```bash
.venv/bin/mcgill-seat-monitor --config config.toml --once
```

If the target is already available on the first run, this intentionally sends one email. A successful unchanged poll is quiet at `INFO`; use `log_level = "DEBUG"` temporarily if every poll must be visible.

## Install as a systemd service

A long-running background process is often called a **daemon**. `systemd` is Raspberry Pi OS's service manager: it starts daemons after boot, stops them cleanly, restarts failed processes, and sends their output to the system journal.

Complete the manual tests before this section.

### 1. Install the application

```bash
sudo useradd --system --home /var/lib/mcgill-seat-monitor --create-home --shell /usr/sbin/nologin seatmonitor
sudo mkdir -p /opt/mcgill-seat-monitor
sudo cp -a ~/mcgill-seat-monitor/. /opt/mcgill-seat-monitor/
sudo chown -R root:root /opt/mcgill-seat-monitor
sudo python3 -m venv /opt/mcgill-seat-monitor/.venv
sudo /opt/mcgill-seat-monitor/.venv/bin/python -m pip install /opt/mcgill-seat-monitor
```

The dedicated `seatmonitor` account cannot log in and is not root. Normal monitoring needs only outbound HTTPS, outbound SMTP, read access to the application/configuration, and write access to its own state directory.

### 2. Install configuration and secrets

```bash
sudo install -m 0644 /opt/mcgill-seat-monitor/config.example.toml /etc/mcgill-seat-monitor.toml
sudo nano /etc/mcgill-seat-monitor.toml
```

Set this production state path in that file:

```toml
state_file = "/var/lib/mcgill-seat-monitor/state.json"
```

Create the root-readable secrets file:

```bash
sudo install -m 0600 /dev/null /etc/mcgill-seat-monitor.env
sudo nano /etc/mcgill-seat-monitor.env
```

Use the same four `MSM_...` assignments shown above. This file is outside the repository and readable only by root; `systemd` reads it and passes the variables to the service.

### 3. Install, test, and enable the unit

```bash
sudo install -m 0644 /opt/mcgill-seat-monitor/systemd/mcgill-seat-monitor.service /etc/systemd/system/mcgill-seat-monitor.service
sudo systemctl daemon-reload
sudo systemd-run --wait --pipe --property=User=seatmonitor --property=EnvironmentFile=/etc/mcgill-seat-monitor.env /opt/mcgill-seat-monitor/.venv/bin/mcgill-seat-monitor --config /etc/mcgill-seat-monitor.toml --test-notification
sudo systemctl enable --now mcgill-seat-monitor.service
```

`enable` adds the service to normal multi-user boot. `--now` also starts it immediately. The unit waits for `network-online.target`, restarts 30 seconds after an unexpected failure, and does not restart after a normal requested stop. `SIGTERM` triggers graceful shutdown; the polling wait ends and the process logs that it stopped cleanly.

### Service commands

```bash
sudo systemctl start mcgill-seat-monitor.service
sudo systemctl stop mcgill-seat-monitor.service
sudo systemctl restart mcgill-seat-monitor.service
sudo systemctl status mcgill-seat-monitor.service
sudo systemctl enable mcgill-seat-monitor.service
sudo systemctl disable mcgill-seat-monitor.service
sudo systemctl disable --now mcgill-seat-monitor.service
```

After editing `/etc/mcgill-seat-monitor.toml` or the environment file, restart the service:

```bash
sudo systemctl restart mcgill-seat-monitor.service
```

After editing the unit file itself, reload units first:

```bash
sudo systemctl daemon-reload
sudo systemctl restart mcgill-seat-monitor.service
```

## Logs and health over SSH

Python writes timestamped logs to standard output/error. Under `systemd`, those streams go to the journal—there is no separate log file to rotate.

```bash
systemctl is-active mcgill-seat-monitor.service
sudo systemctl status mcgill-seat-monitor.service
sudo journalctl -u mcgill-seat-monitor.service -n 100 --no-pager
sudo journalctl -u mcgill-seat-monitor.service -f
sudo journalctl -u mcgill-seat-monitor.service --since today
```

Meaningful state changes and errors use `INFO`/`ERROR`. Successful unchanged polls use `DEBUG` to avoid noise. An `INFO` health line appears after every 12 successful cycles—about once per hour at the default interval.

Common diagnoses:

- `configuration error`: validate TOML and required fields with `--check-config`.
- `no VSB class matched`: verify the term, activity, section, and optional CRN.
- `multiple VSB classes matched`: add `activity` or `crn` to make the selector exact.
- `VSB request failed`: inspect the network, clock, DNS, and later journal lines. Retries and backoff happen automatically.
- `SMTP delivery failed`: verify provider host/port/security, app-password requirements, and the four environment variables.
- VSB schema errors after a McGill update: run offline tests, capture a new response without personal data, and update only `parser.py` plus fixtures.

## Operational and security boundaries

- Default polling is 300 seconds and cannot be configured below 60 seconds.
- Transient HTTP errors, rate limiting, timeouts, malformed XML, and missing sections are handled as failures; the last good seat state remains intact.
- Availability emails are retried if delivery fails.
- State writes are atomic and survive service restarts.
- SMTP uses certificate-verified TLS for `ssl` and `starttls` modes.
- No browser automation, cookies, Minerva credentials, registration actions, root runtime, database, Docker, dashboard, or inbound network listener is used.
- The VSB endpoints are not a contractual API. If McGill removes guest access or changes the response format, the service should fail visibly rather than guess.

Be conservative even though the code permits a 60-second minimum. Five minutes is the recommended default. A seat alert is advisory: always confirm availability and register through McGill's official process yourself.
