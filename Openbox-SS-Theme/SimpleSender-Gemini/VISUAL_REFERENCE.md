# SimpleSender-Gemini Visual Reference

This theme package is tuned for Raspberry Pi 4 touchscreen-style use with:

- larger titlebar button glyphs in `openbox-3/*.xbm`
- larger titlebar button boxes via the `ActiveWindow` / `InactiveWindow` font sizes in:
  - `rc.xml.theme-snippet`
  - `rc.xml.full-example`

Expected visible result:

- minimize, maximize, and close glyphs look substantially larger than the Openbox defaults
- glyphs are centered inside the button background rather than clipped in the top-left corner
- titlebar text remains readable on a Pi touchscreen without overwhelming the titlebar

If the glyphs still look clipped on the target system:

1. verify the theme name in `~/.config/openbox/rc.xml` is `SimpleSender-Gemini`
2. verify the XBM files from `openbox-3/` were copied with the theme
3. verify the Openbox `ActiveWindow` and `InactiveWindow` font sizes were updated to `18`
4. run:

```bash
openbox --reconfigure
```

If needed, increase the two titlebar font sizes from `18` to `20`.
