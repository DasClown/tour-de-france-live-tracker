# Deployment guide

Two ways to deploy the TDF Tracker on a Linux server with systemd.

## Option A: Automated (recommended)

```bash
# After cloning/uploading the repo to /opt/tdf-tracker:
sudo bash deploy/install.sh /opt/tdf-tracker 8000 "$(openssl rand -hex 24)"
```

`install.sh` will:
1. Install dependencies via pip (`aiohttp`, `rich`, `requests`)
2. Create the state directory `/var/lib/tdf-tracker`
3. Write the systemd unit (with token + port)
4. Enable + start the service
5. Print the **token + URL** at the end

## Option B: Manual

```bash
cd /opt/tdf-tracker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

install -d -m 0755 /var/lib/tdf-tracker
cp deploy/tdf-tracker.service /etc/systemd/system/
# Edit /etc/systemd/system/tdf-tracker.service:
#   - Set TDF_TOKEN (use `openssl rand -hex 24`)
#   - Adjust ExecStart Python path if using venv
systemctl daemon-reload
systemctl enable --now tdf-tracker

journalctl -u tdf-tracker -f   # follow logs
```

## Mobile access

Once the service is running:

```
http://<server-ip>:8000/?k=<TOKEN>
```

The token is stored in the browser. The control bar (Restart/Stop/Start)
appears automatically once `/api/control` reports systemd available.

## polkit (for Restart/Stop from the phone)

Restart/Stop from the running service needs a polkit rule. Create
`/etc/polkit-1/rules.d/49-tdf-tracker.rules`:

```javascript
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.user == "root" &&
        action.lookup("unit") == "tdf-tracker.service") {
        return polkit.Result.YES;
    }
});
```

Start/Status work without polkit.

## Firewall

Open port 8000 (or whichever `--port` you chose) in:
- The OS firewall (`ufw allow 8000` if ufw is active)
- The cloud provider's firewall/security group (Hetzner Cloud Firewall,
  AWS Security Groups, etc.)

Verify with an external TCP check before assuming reachability.

## Operating notes

- **Log rotation**: the JSONL trail rotates at 256 MB, keeping 5 archives
  (~1.3 GB max). Adjust `MAX_FILE_SIZE_BYTES` in `tdf/trail.py` if needed.
- **Restart safety**: the service restarts itself via systemctl; the
  process that issues the restart will be terminated (this is expected —
  the new instance comes up cleanly).
- **Pre-stage-3 logging**: when ASO has not yet published data for the
  current stage (HTTP 204), the bootstrap logs an `ERROR`. This is
  expected and resolves once the stage goes live.
