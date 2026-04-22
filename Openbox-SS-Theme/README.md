# SimpleSender-Gemini Openbox Theme

This folder contains an Openbox theme that matches the current Simple Sender Gemini-inspired look as closely as Openbox window-manager theming allows.

Theme directory:

- `SimpleSender-Gemini/openbox-3/themerc`
- `SimpleSender-Gemini/openbox-3/*.xbm`
- `SimpleSender-Gemini/rc.xml.theme-snippet`
- `SimpleSender-Gemini/rc.xml.full-example`
- `SimpleSender-Gemini/VISUAL_REFERENCE.md`

Theme characteristics:

- dark blue-black title bars and menus
- Gemini-like blue-violet accent highlights
- near-white active text
- muted inactive window treatment
- restrained borders for good readability on shop/touchscreen systems
- enlarged 16x16 titlebar button glyphs for minimize, maximize, and close

Important titlebar-button note:

- Openbox button glyph size and titlebar button box size are separate.
- The theme now includes larger XBM glyphs for the titlebar buttons.
- To prevent those larger glyphs from being clipped, you must also increase the
  Openbox `ActiveWindow` / `InactiveWindow` title font size in `rc.xml`.
- A ready-to-merge snippet is included in:
  - `SimpleSender-Gemini/rc.xml.theme-snippet`

Install:

1. Copy the `SimpleSender-Gemini` directory into one of these Openbox theme locations:
   - `~/.themes/`
   - `~/.local/share/themes/`
   - `/usr/share/themes/` for a system-wide install
2. The final installed structure should look like:

```text
~/.themes/SimpleSender-Gemini/openbox-3/themerc
~/.themes/SimpleSender-Gemini/openbox-3/close.xbm
~/.themes/SimpleSender-Gemini/openbox-3/iconify.xbm
~/.themes/SimpleSender-Gemini/openbox-3/max.xbm
```
3. Copy the included Openbox theme snippet somewhere convenient, for example:

```text
~/.themes/SimpleSender-Gemini/rc.xml.theme-snippet
```

Raspberry Pi 4 quick path:

1. Copy the whole `SimpleSender-Gemini` folder to:
   - `~/.themes/SimpleSender-Gemini`
2. Merge `rc.xml.theme-snippet` into:
   - `~/.config/openbox/rc.xml`
3. If you want a complete reference instead of a snippet, use:
   - `SimpleSender-Gemini/rc.xml.full-example`
4. Reload Openbox:

```bash
openbox --reconfigure
```

Activate:

1. Preferred: open `obconf` and select `SimpleSender-Gemini`.
2. Edit `~/.config/openbox/rc.xml` and merge in the settings from:
   - `SimpleSender-Gemini/rc.xml.theme-snippet`
   - or use `SimpleSender-Gemini/rc.xml.full-example` as a reference for the theme block
3. At minimum, your `<theme>` section should set the theme name and larger
   window-title fonts, for example:

```xml
<theme>
  <name>SimpleSender-Gemini</name>
  <titleLayout>NLIMC</titleLayout>
  <font place="ActiveWindow">
    <name>Sans</name>
    <size>18</size>
    <weight>bold</weight>
    <slant>normal</slant>
  </font>
  <font place="InactiveWindow">
    <name>Sans</name>
    <size>18</size>
    <weight>bold</weight>
    <slant>normal</slant>
  </font>
</theme>
```

4. Reload Openbox:

```bash
openbox --reconfigure
```

Visual reference:

- `SimpleSender-Gemini/VISUAL_REFERENCE.md` summarizes the intended button appearance and the checks to run if a target system still clips the glyphs.

Limitations:

- Openbox themes only affect window decorations, menus, and Openbox OSD elements. They do not theme the inside of the Tk/ttk app.
- Openbox does not control the file/folder content area inside a GTK file manager such as Thunar, PCManFM, or Nautilus. That content pane is controlled by the file manager's GTK theme, not by Openbox.
- Openbox titlebar button glyphs use XBM masks. This repo now includes larger custom XBM button images, but Openbox still requires matching `rc.xml` title-font sizing so the button boxes are large enough to display them fully.
- Openbox has no direct equivalent for ttk hover animations or some of the app's finer surface/contrast states, so this theme focuses on color harmony, readability, and practical contrast.

GTK / file-manager companion styling:

- If you want the file manager content area to match better, use the optional companion GTK snippet in:
  - `gtk-companion/gtk.css`
- Common install locations:
  - `~/.config/gtk-3.0/gtk.css`
  - `~/.config/gtk-4.0/gtk.css`
- This is optional and toolkit-dependent, but it is the correct path for darkening the actual file/folder content area. Openbox alone cannot do that.
