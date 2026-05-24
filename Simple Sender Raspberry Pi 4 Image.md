# Simple Sender Raspberry Pi 4 Image

This repository does not currently include a checked-in Raspberry Pi 4 image artifact. The application baseline is now **Simple Sender 3.16**. If you need a Raspberry Pi image for this release line, rebuild it from the current repo state and publish it separately, for example as a release asset.

Simple Sender was built to run well on affordable hardware such as a **Raspberry Pi 4**, remain easy to use from a touchscreen, and stream very large G-code files reliably. It emphasizes clarity, dependability, and practical shop usability instead of trying to become an overloaded all-in-one platform.

Key workflow-oriented features of the current app baseline include:

- Reliable large-file streaming for GRBL 1.1h, 3-axis machines.
- A clean, direct interface intended for day-to-day machine use.
- App-local toolbar icons that now use a raster-first runtime path for consistent Windows and Raspberry Pi / Linux display, with safe fallbacks still preserved if an asset cannot be loaded.
- Guided multi-tool workflow support with tool reference and offset handling.
- Custom sender directives such as `VACUUM_ON`, `VACUUM_OFF`, and `TC:<tool name>`.
- Included support for VCarve Pro 12.5 post processors in both inch and mm variants.
- Keyboard shortcuts, joystick bindings, and macro support for repeatable operator workflows.
- Job Setup safeguards intended to help prevent starting a job before setup is complete.
- Support for Kasa-connected accessories such as a shop vacuum or machine light, with bounded failure diagnostics for Pi/Kasa network troubleshooting.

> **Safety notice:** Always test "in the air" with the spindle off before cutting material.

For the full application manual and feature documentation, see `README.md`. For a higher-level project summary, see `ABOUT - Simple Sender v3.16.md`.

## Equipment Used / Recommended Hardware

The following hardware was used or is recommended for this Raspberry Pi 4 Simple Sender setup:

- **Raspberry Pi 4 (8GB recommended)**
- **15.6 Inch Raspberry Pi Screen, FHD 1920x1080 touchscreen monitor**
  https://www.amazon.com/dp/B0DRF6WLHZ
- **USB controller for PC gaming**
  https://www.amazon.com/dp/B073Z9MKKH
- **Amazon Basics micro HDMI to HDMI cable**
  https://www.amazon.com/dp/B07KSDB25X
- **Kasa Smart Plug Wi-Fi outlet with 2 sockets**
  https://www.amazon.com/Kasa-Smart-Resistance-Compatible-EP40/dp/B091FXH2FR
  Useful for controlling accessories such as a shop vacuum and/or machine light through Simple Sender.
- **N.O. (normally open) CNC tool touch sensor**
  https://www.amazon.com/dp/B08M97W5MV
  Useful for tool height measurement and guided tool-reference workflows.

It is also **highly recommended** to boot and run the system from an **M.2 drive via USB** instead of relying on the SD card long-term. This generally improves longevity, stability, and overall responsiveness.

## Before First Boot: Wi-Fi Setup

Before booting the Pi for the first time, mount the flashed SD card on another computer and open the `boot` partition. Edit `dietpi-wifi.txt` so it matches your local Wi-Fi network details.

Update these two lines:

```text
# Entry 0
# - WiFi SSID: required, case sensitive
aWIFI_SSID[0]='!!!! YOUR-WIFI-SSID-HERE !!!!'
# - WiFi key: If no key/open, leave this blank
# - In case of WPA-PSK, alternatively enter the 64-digit hexadecimal key returned by wpa_passphrase
# - Please replace single quote characters ' in your key with '\''. No other escaping is required.
aWIFI_KEY[0]='!!!! YOUR-WIFI-PASSWORD-HERE !!!!'
```

Save the file, safely eject the card, and then boot the Pi.

## Default Access

- The default password for this image is `simple-sender`.
- DietPi Dashboard is installed and available at `http://IP:5252`.
- Use the default password `simple-sender` for the DietPi Dashboard as well.

## Samba Access

Samba is installed and openly accessible on this image for simple file transfer on a trusted local network.

- In a file manager, use `//IP/` to browse the Pi shares.
- On Windows, you can also enter `\\IP\` in File Explorer.
- This setup is intentionally convenience-first and is best treated as a protected home or shop LAN setup.

## Notes

- Simple Sender is intended to run well on Raspberry Pi 4 hardware and fit a dedicated machine-side workflow.
- Current toolbar assets live in `simple_sender/ui/icons`. The normal runtime path now prefers the app-local PNG toolbar assets, keeps SVG lookup only as a compatibility fallback, and only falls back to the drawn icons if asset loading really fails.
- On Linux, shared file dialogs default to `/root/CNC_Jobs`. You can change that in **App Settings > Theme > Linux File Dialog Default Path**; invalid paths fall back safely.
- Do not sync or overwrite the Simple Sender install while the app is running. Close the application first, or reboot/shutdown the Pi before updating runtime files.
- The runtime marker/duplicate-instance guard exists to block overlapping runtime/live-overwrite conditions; it is a safety measure, not a live-update workflow.
- The Windows share sync helper stages into a sibling pending-update folder when it detects the runtime marker; it does not hot-overwrite the live install while the app is still running.
- No Raspberry Pi image artifact is currently checked into this repository. If you need strict build provenance or a version-aligned distributable image, rebuild it from the current `3.16` baseline and publish it separately before distribution.
- If you keep this image on a shared or less-trusted network, change default passwords and review Samba exposure before regular use.
