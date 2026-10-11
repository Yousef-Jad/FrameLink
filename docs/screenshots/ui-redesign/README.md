# LumaCapture UI redesign evidence

These screenshots were captured from the running PySide6 application with
`tools/ui_screenshots.py` on the Linux/Wayland verification host.

## Regular window

- `studio.png` — preview-first Studio with compact rail, source selector, and floating transport.
- `studio-inspector.png` — contextual capture inspector opened from Studio.
- `studio-screen-active.png` — actual recording state with a bounded visual fixture source.
- `studio-both-active.png` — combined recording layout with picture-in-picture.
- `library.png` — populated thumbnail-first Library with search and sorting.
- `devices.png` — focused local phone pairing surface.
- `settings.png` — grouped persistent preferences.

## Compact window

The compact set is generated into a separate review directory during validation at
`1060 × 740` and covers the same four top-level pages. It is not copied into the
repository because the regular images are the documentation references; the compact
run is an explicit regression check for clipping and layout collapse.

The active-scene images use generated in-memory frames only for visual state coverage.
They do not change the production capture path or represent physical display/phone
verification.

## Reproduce

```sh
QT_QPA_PLATFORM=offscreen python tools/ui_screenshots.py \
  --output docs/screenshots/ui-redesign \
  --width 1240 --height 870 --active-scenes

QT_QPA_PLATFORM=offscreen python tools/ui_screenshots.py \
  --output /tmp/lumacapture-ui-compact \
  --width 1060 --height 740
```
