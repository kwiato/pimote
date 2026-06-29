# Pi PC Remote 🖥️

A remote PC controller built on a **Raspberry Pi Zero 2 W**. The Pi pretends to
be a USB keyboard (HID gadget) plugged into the computer, and a web panel
(Flask) lets you — from your phone, over Tailscale, from anywhere — wake the PC
(Wake-on-LAN), launch apps, and type text/commands.

## How it works

```
Phone ──Tailscale──► Pi Zero 2 W ──USB(HID)──► PC (keyboard)
                          │
                          └──────LAN broadcast──► PC (magic packet / WoL)
```

- **Keyboard:** over USB OTG the Pi exposes a HID device (`/dev/hidg0`). Writing
  8-byte reports to that file "presses keys" on the PC.
- **Wake:** the Pi sends a magic packet over the LAN to the PC's NIC. WoL is
  local (L2 broadcast), but you open the panel remotely over Tailscale.
- **Panel:** a tiny Flask server on the Pi, reachable at `http://<tailscale-ip>:5000`.

> The whole service lives on the Pi Zero, because only it is the PC's physical
> keyboard. A Pi 5 (running the rest of your self-hosted services) has no way to
> drive this PC over USB.

## Hardware

- Raspberry Pi Zero 2 W + a microSD card (8–16 GB is plenty)
- A USB cable **with data lines** (not "charge-only")
- Separate power for the Pi (charger → the **PWR** port)
- A PC with a WoL-capable NIC, connected to the router **by cable**

### ⚠️ Critical: USB ports on the Pi Zero

The Pi Zero 2 W has two micro-USB ports side by side:
- **PWR** (outer) — power only. Plug the charger here.
- **USB** (middle, closer to HDMI) — **data**. Plug the cable to the PC here.

Mixing these up = the PC gets power but doesn't see the keyboard
(error `Cannot send after transport endpoint shutdown`). This was the #1 cause
of trouble while setting this up.

## Installation

### One command

After preparing the system (step 1) and Tailscale (step 2), on the Pi (as a
**normal user**, not root):

```bash
curl -fsSL https://raw.githubusercontent.com/kwiato/pimote/master/get.sh | bash
sudo reboot   # needed only the first time (activates the USB gadget)
```

This downloads the repo and runs the interactive `install.sh` (asks for
MAC/keyboard layout/panel password/port/Windows password). Everything else —
copying files, dependencies, the USB gadget, systemd services, config, and
secrets — is done by the script.

> **The panel is password-protected.** On first run the installer makes you set
> a **panel password**; the web UI is locked until you enter it. It also prints
> an **API token** for automation (e.g. a Glance "Wake PC" tile). See
> [Security](#security).

### Quick path (from a local clone)

The same, if you'd rather read the code first:

```bash
git clone https://github.com/kwiato/pimote && cd pimote
./install.sh        # run as a NORMAL user (not sudo); asks for MAC/layout/password/port
sudo reboot         # needed only the first time (activates the USB gadget)
```

The installer is **idempotent** — re-run it whenever you want to change the
password, MAC, keyboard layout, or port. Configuration lands **outside the repo**
in `~/.config/pc-remote/config.env` (MAC/layout/port) and
`~/.config/pc-remote/login.secret` (password, `chmod 600`). That way the repo can
be cloned and shared without editing any code — everyone supplies their own
details during `install.sh`. Uninstall: `./uninstall.sh` (add `--purge` to drop
the password too).

Sections 1–2 below are **required prerequisites**; 3–6 are the **manual
equivalent** of what the installer does (handy for debugging or if you prefer to
go step by step).

### 1. System

Raspberry Pi OS Lite (64-bit) via Raspberry Pi Imager. In the settings (⚙️) set
right away: hostname, SSH, user, Wi-Fi (your country), locale/timezone.

### 2. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
tailscale ip -4   # note the address, e.g. 100.x.y.z
```

### 3. USB HID gadget (keyboard)

`boot/config.txt.snippet` → append to `/boot/firmware/config.txt`:
```
dtoverlay=dwc2,dr_mode=peripheral
```

Copy the script and service:
```bash
sudo cp usr-local-bin/hid-gadget.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/hid-gadget.sh
sudo cp systemd/hid-gadget.service /etc/systemd/system/
sudo systemctl enable hid-gadget.service
sudo reboot
```

Verify after the reboot:
```bash
ls /sys/kernel/config/usb_gadget/   # -> picontroller
ls /sys/class/udc/                  # -> 3f980000.usb (must not be empty)
ls -l /dev/hidg0                    # -> the device exists
```

Key test (click into a text field on the PC, then on the Pi):
```bash
echo -ne "\0\0\x04\0\0\0\0\0" | sudo tee /dev/hidg0 > /dev/null  # 'a'
echo -ne "\0\0\0\0\0\0\0\0"   | sudo tee /dev/hidg0 > /dev/null  # release
```

### 4. Web panel

```bash
sudo apt update
sudo apt install -y python3-flask python3-cryptography wakeonlan
mkdir -p ~/pc-remote
cp pc-remote/* ~/pc-remote/
```

Set the **panel password** and generate the **API token** (both stored in
`~/.config/pc-remote`, chmod 600):

```bash
mkdir -p ~/.config/pc-remote && chmod 700 ~/.config/pc-remote
PC_REMOTE_CFG_DIR=~/.config/pc-remote PANEL_PW='your-panel-password' \
    python3 ~/pc-remote/crypto_secret.py set-panel
PC_REMOTE_CFG_DIR=~/.config/pc-remote python3 ~/pc-remote/crypto_secret.py gen-token
```

Configuration is kept **outside the code**, in `~/.config/pc-remote/config.env`
(start from `pc-remote/config.env.example`). Set `PC_MAC` to your Ethernet NIC's
MAC (`ipconfig /all` on Windows → "Physical Address", replace dashes with
colons), pick `KEYBOARD_LAYOUT` (`pl`/`us`), and optionally `REMOTE_PORT`:

```bash
mkdir -p ~/.config/pc-remote
cp pc-remote/config.env.example ~/.config/pc-remote/config.env
chmod 600 ~/.config/pc-remote/config.env
nano ~/.config/pc-remote/config.env   # set PC_MAC, KEYBOARD_LAYOUT
```

The `pc-remote.service` in the repo is a **template** with `__USER__`/`__HOME__`
— substitute your values (the installer does this automatically):

```bash
sed -e "s|__USER__|$USER|g" -e "s|__HOME__|$HOME|g" \
    systemd/pc-remote.service | sudo tee /etc/systemd/system/pc-remote.service >/dev/null
sudo systemctl enable --now pc-remote.service
```

Panel: `http://<tailscale-ip>:5000`

### 5. Wake-on-LAN — PC configuration

**BIOS (MSI Click BIOS 5, B560-A PRO board):**
- `SETTINGS → Advanced → Power Management Setup` → **ErP Ready = Disabled**
- `SETTINGS → Advanced → Wake Up Event Setup` → **Resume By PCI-E/Networking Device = Enabled**

**Windows:**
- Control Panel → Power Options → Choose what the power buttons do →
  **turn off "Fast startup"** (the most common reason WoL from S5 fails)
- Device Manager → network adapter → Advanced → **Wake on Magic Packet = Enabled**
- Adapter power management → allow wake only via magic packet
- If WoL from S5 doesn't fire: install the LAN driver from the MSI site (the
  generic Windows driver is sometimes unreliable when waking from full shutdown)

### 6. Remote Windows login (optional)

After waking, the PC sits on the lock screen. The main way to still reach it over
Tailscale/RDP **without logging in** is to enable **unattended mode** in Tailscale
on the PC (tray → Preferences → "Run unattended") — then the tunnel comes up
before login. The "🔑 Log in" button is a fallback in case that's not enough — the
Pi (as a keyboard) raises the lock-screen curtain and types the password + Enter.

The password is kept **outside the repo** and **encrypted** with a key derived
from your panel password (so the file on the SD card is useless without it):

```bash
PC_REMOTE_CFG_DIR=~/.config/pc-remote \
    PANEL_PW='your-panel-password' WIN_PW='YOUR_WINDOWS_PASSWORD' \
    python3 ~/pc-remote/crypto_secret.py set-windows   # -> login.secret.enc
```

Because the key comes from the panel password, the app caches it only while you
are logged in: **after a service restart, unlock the panel once** before the
"Log in" button can decrypt and type the password. You can override the file
path with `PC_LOGIN_SECRET_FILE`. Without the file the button returns a 400 and
types nothing. (Changing the panel password later requires re-running this so
the Windows password is re-encrypted with the new key.)

> ⚠️ With `KEYBOARD_LAYOUT=pl` the script assumes the **Polish "Programmers"**
> layout on the Windows side (base ASCII = like US, Polish chars via AltGr). The
> **login** screen may use a different layout than the desktop — if the password
> types wrong, check the input language on the login screen. For slow cold-boots
> from S5, increase `settle` in `hid.login`.

## Keyboard layout

Set `KEYBOARD_LAYOUT` in `config.env` (the installer asks for it):

| Value | Layout | Notes |
|-------|--------|-------|
| `pl`  | Polish (Programmers) | US base + ą/ć/ł… via AltGr. **Default.** |
| `us`  | US English | ASCII only; Polish characters are skipped |

Both share the same scancodes for ASCII, so `us` is just `pl` minus the AltGr
Polish characters. The Polish "214" layout would **not** work — its symbols use
different scancodes. The HID usage codes the Pi sends are layout-independent;
what matters is the layout Windows thinks is active.

## PC power states

| State | Description     | WoL    |
|-------|-----------------|--------|
| S3    | sleep           | ✅     |
| S4    | hibernation     | ⚠️ unreliable on MSI |
| S5    | full shutdown   | ✅     |

Works from S3 and S5. Avoid hibernation (S4). The Pi is powered separately from
the wall, so it stays up 24/7 regardless of the PC's state — the panel is always
reachable.

## Security

This panel can run arbitrary commands and type your password on the PC, so
access is gated:

- **Panel password** — the web UI is locked until you enter it. It is stored as
  a one-way **scrypt hash** (`panel.secret`), never in clear text. A login is
  cached in memory for 12 h; a service restart logs everyone out.
- **API token** — a long random token (`api.token`) for automation. Send it as
  `Authorization: Bearer <token>` (or `?token=`). It can drive every action
  **except** "Log in" (typing the Windows password), which needs an interactive
  panel login.
- **Windows password** — stored **encrypted** (`login.secret.enc`) with a key
  derived from the panel password; the key is never written to disk.
- **Cookies** — `HttpOnly` + `SameSite=Strict` (blocks cross-site/CSRF POSTs).
- **HID device** — `/dev/hidg0` is owned by the panel user, `chmod 600` (not
  world-writable).

Because of all this, binding to `0.0.0.0` is acceptable — but keeping the panel
reachable only over **Tailscale** (not your LAN/Wi-Fi) is still recommended. The
panel runs over plain HTTP; over Tailscale that traffic is encrypted by
WireGuard anyway, but on a local LAN it is not.

### Glance "Wake PC" tile

Use the API token from the installer. Example `glance.yml` widget:

```yaml
- type: custom-api
  title: Wake PC
  url: http://<tailscale-ip>:5000/wake
  method: POST
  headers:
    Authorization: Bearer <your-api-token>
```

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Cannot send after transport endpoint shutdown` | Cable in the PWR port instead of USB, or a charge-only cable |
| No `/dev/hidg0` | `libcomposite` not loaded — check `modprobe` in the script |
| `cd: /sys/kernel/config/usb_gadget/: No such file` | same — `libcomposite` didn't come up |
| `/sys/class/udc/` is empty | missing `dr_mode=peripheral` in config.txt |
| WoL doesn't wake | Fast startup on / ErP not Disabled / LAN driver |

Force USB re-enumeration without a reboot:
```bash
echo "" | sudo tee /sys/kernel/config/usb_gadget/picontroller/UDC
ls /sys/class/udc/ | sudo tee /sys/kernel/config/usb_gadget/picontroller/UDC
```

## Notes

- Keyboard layout: with `KEYBOARD_LAYOUT=pl` the script assumes the Windows-side
  **Polish "Programmers"** layout — base ASCII like US, Polish chars (ą/ć/ł…) sent
  as AltGr + letter. Plain US (`us`) works too, minus the Polish characters. The
  "Polish (214)" layout will misbehave — its symbols use different scancodes.

## License

MIT — see [LICENSE](LICENSE).
