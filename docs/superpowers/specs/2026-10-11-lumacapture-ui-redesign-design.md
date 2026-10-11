# LumaCapture UI Redesign

## Status

Approved design for implementation in the existing PySide6 desktop application. The redesign changes the presentation layer and preserves the capture, WebRTC, recording, compression, storage, security, and recovery services.

## Product thesis

LumaCapture is a quiet local recording instrument: the live scene is the content, recording is the decisive action, and everything else should appear only when it helps the current capture.

The signature element is a compact floating transport bar that makes the recording state legible at a glance without competing with the live preview.

## Current UI findings

- The current 210 px sidebar consumes workspace width and presents five text-heavy destinations as equal-weight buttons.
- The dashboard uses a large title, a full-width three-button mode row, a four-column settings card, and a separate bottom action row. The preview is visually one block among several instead of the dominant working surface.
- Compression Queue is a permanent navigation destination even though it is a secondary state of the recording library.
- Settings exposes capture, composition, audio, and storage controls in one long form regardless of the current recording mode.
- The recording library is a QTableWidget with no thumbnail-first browsing, search, sorting, or integrated compression progress.
- The theme has panel borders and identical control treatments but no semantic material hierarchy. Windows receives a DWM backdrop attribute; internal widgets do not implement a content-aware blur pass.
- The existing preview is fed by the real bounded source buffers and must remain the source of truth for all live imagery.

## Scope and invariants

### In scope

- Main window composition and navigation presentation.
- Studio dashboard hierarchy, mode selector, preview framing, floating transport bar, inspector, and status surfaces.
- Library browsing presentation and compression queue access.
- Device pairing presentation, including QR/trust setup access.
- Settings presentation and grouping.
- Centralized color, typography, spacing, radius, focus, material, and motion tokens.
- Keyboard focus, tooltips, reduced-motion behavior, and responsive layout behavior.
- Actual rendered screenshot validation at regular and compact desktop sizes.

### Out of scope

- Capture backends, WebRTC signaling or media transport, recording state transitions, FFmpeg encoding, compression jobs, SQLite schema, certificate handling, and network security policy.
- New recording capabilities or changes to media timing.
- Fake video frames or screenshot-based capture.
- A CSS-like backdrop-filter dependency or a new GUI framework.

### Invariants

- Existing `MainWindow` callbacks continue to invoke the same recording, capture, phone server, compression, and library services.
- The real `Sources` buffers remain the only source for the live preview.
- Any internal glass preview is derived from the current rendered frame at bounded low resolution and refreshed only while the preview is visible. If the platform cannot provide a trustworthy blur path, the surface falls back to an opaque material token.
- No UI animation blocks `Recorder.start`, `pause`, `resume`, or `stop`.
- Every icon-only control has a tooltip and accessible name; text equivalents remain available through menus or context actions.
- Existing light and dark appearance settings remain supported.

## Information architecture

The main window has four top-level destinations:

1. **Studio** — live preview, source selection, transport controls, concise status, and contextual inspector.
2. **Library** — recordings, exports, search, sort, playback, file actions, and a compact compression status strip.
3. **Devices** — local interface selection, phone server status, focused pairing/trust surface, and disconnect.
4. **Settings** — infrequently changed preferences grouped into capture, audio, composition, export, appearance, and advanced sections.

The navigation rail is 72 px wide in its normal state, floats over the background with a subtle separation, and uses a stable icon plus a tooltip. The active item uses a narrow accent marker and a quiet surface highlight rather than a filled rectangular button. At compact widths, labels never appear in the rail; the content area remains the priority.

Compression jobs are shown in Library as a status strip and a secondary drawer/panel. A job row links to its source recording, exposes progress and error text, and retains Cancel/Retry actions without creating a fifth permanent navigation item.

## Studio layout

```text
Regular window (minimum supported 1060 × 740)

┌──────┬────────────────────────────────────────────────────────────────┐
│ rail │ top line: Studio       source selector       inspector button │
│      ├────────────────────────────────────────────────────────────────┤
│      │                                                                │
│      │                 live aspect-ratio preview                      │
│      │     empty/loading/source metadata stays inside this region     │
│      │                                                                │
│      │                  ┌──────────────────────┐                      │
│      │                  │ floating transport  │                      │
│      │                  └──────────────────────┘                      │
│      ├────────────────────────────────────────────────────────────────┤
│      │ source status / encoder / capture health       disk status      │
└──────┴────────────────────────────────────────────────────────────────┘

Compact window

┌──────┬──────────────────────────────────────────────┐
│ rail │ Studio · Camera / Screen / Both              │
│      │                                              │
│      │               preview                        │
│      │         transport remains attached           │
│      │                                              │
│      │ status line · inspector opens as drawer      │
└──────┴──────────────────────────────────────────────┘
```

The source selector is a compact segmented control with Camera, Screen, and Both labels. Its selection is communicated by a tonal surface, accent underline, and accessible checked state; it does not use a large saturated fill.

The transport bar is a functional glass layer over the lower portion of the preview. It contains, in order: record/stop, timer, pause/resume, microphone toggle, desktop/system audio toggle, and inspector trigger. The record control is the only persistent tinted primary action. During recording it changes to the recording accent and shows a visible dot plus `Recording` text; the timer uses tabular numerals. Stop remains available without waiting for transitions.

The preview keeps the source aspect ratio and uses contain scaling. The empty state gives one next action appropriate to the selected source: start screen preview, connect phone, or prepare both sources. Source and capture metadata are shown in a small status line below the preview, not as a dense metrics paragraph.

## Contextual inspector

The inspector is a right-side `QFrame` drawer in the Studio page. It is hidden by default and can be opened with the `I` shortcut, the toolbar button, or the application View menu. It is disabled while a recording is active for settings that cannot safely change mid-session; the disabled explanation names the reason.

The inspector uses sections with progressive disclosure:

- **Capture**: display, resolution, frame rate, encoder. Screen-only controls are hidden for Camera mode.
- **Audio**: microphone, system audio, phone audio, devices, and gains. Phone audio appears only when Camera or Both is selected.
- **Composition**: layout, camera position, size, opacity, mirror, corners, border, and disconnect policy. It appears only for Both mode.
- **Export**: automatic compression and preset.

The existing widgets remain the underlying controls so current `config()`, settings persistence, and start validation remain intact. The redesign relocates and groups them instead of replacing their behavior.

## Library layout

The default view is a responsive thumbnail grid with a list fallback at narrow widths. Each recording item contains the real thumbnail, title, duration, date, dimensions/FPS, file size, and a status badge. Exported files are grouped with their original by session and show compression progress inline. Search filters by filename; sorting supports newest, oldest, duration, and size.

Each item exposes Play as the primary row action and a context menu for Open folder, Rename, Compress again, Export, and Delete. Destructive deletion continues to use the existing confirmation path. An empty library explains that recordings remain local and provides a `Go to Studio` action.

## Devices layout

The Devices page is a focused connection surface rather than a permanent instructional block in Studio. It presents a selected private IPv4 interface, server status, current origin, QR pairing code, and a concise step indicator:

1. Select a local network.
2. Trust the desktop certificate when required.
3. Scan the short-lived QR code and approve the camera.

Certificate setup opens a focused dialog containing the fingerprint, offline transfer instructions, optional timed bootstrap action, and explicit close action. A connected phone replaces setup instructions with connection status, negotiated camera details when available, and a prominent Disconnect control.

## Material and token system

`ui/theme.py` becomes the single source of semantic design tokens. Tokens have dark and light values and are referenced by component styles rather than repeated literals.

```text
Dark content       #0B0D12
Dark elevated      #151922
Dark floating      rgba(25, 29, 38, 0.82)
Primary text       #F2F4F8
Secondary text     #9299A8
Separator          rgba(255, 255, 255, 0.07)
Accent             #AFC5FF
Recording          #FF453A
Focus              #C8D7FF
Success            #82D7A4
Warning            #F0C36B
```

Light values use warm-neutral opaque surfaces and preserve the same semantic roles. Body/control text remains at least 13 px on desktop; metadata never drops below 10 px. Timers and file sizes use a tabular system monospace fallback. Spacing uses 8 px units with 4 px optical adjustments only for icon alignment. Corner radii are 8 px for controls, 12 px for the preview, and 16 px for the floating transport/inspector surfaces; the rail itself is not a large rounded card.

Materials:

- `content`: opaque background for media and text.
- `glass`: functional overlay with translucent fill, hairline highlight, and content-aware low-resolution blur where supported.
- `elevated`: more opaque functional surface for text-heavy inspector/library controls.
- `opaque_fallback`: fully opaque surface used for reduced transparency, unsupported compositor blur, and high-contrast modes.
- `separator`, `focus`, and status colors are semantic accents, not general decoration.

The toolbar blur is a bounded render pass over the current preview image: the preview exposes a low-resolution crop of the current frame, the glass surface blurs that crop, and the surface composites its tint/highlight over it. It is never sourced from a cached desktop screenshot. The pass is skipped when no live frame exists or when reduced transparency is requested. Windows retains the documented DWM backdrop attribute for the outer window; Linux remains compositor-aware and uses the opaque fallback when the compositor cannot supply reliable blur.

## Motion and accessibility

- Mode selection uses a 160 ms opacity/underline transition; no layout jump is required.
- The inspector uses a 180 ms slide/fade only when motion is allowed.
- Recording state uses a restrained 900 ms pulse on the status dot, paired with text and icon changes.
- `QT_NO_ANIMATIONS=1`, the platform reduced-motion hint where available, or the stored accessibility override disables custom transitions and pulse effects.
- Keyboard focus uses the focus token and is visible on all buttons, segments, rail items, combo boxes, and list/grid items.
- Rail buttons have at least 40 px square hit regions; toolbar controls have at least 36 px hit regions. All controls have tooltips and accessible descriptions where the icon is not self-evident.
- Live preview is not used as the only status channel: recording state, phone connection, errors, and compression progress have text equivalents.
- Window resize preserves the preview aspect ratio and moves the inspector into a full-width drawer if the content width becomes too narrow.

## Component boundaries

- `ui/theme.py`: semantic tokens, palette variants, stylesheet fragments, and motion/reduced-transparency helpers.
- `ui/components.py`: reusable rail button, segmented selector, material surface, status pill, icon button, transport bar, inspector section, and library item.
- `ui/preview.py`: preview widget and current-frame provider used by the toolbar render pass. It owns only display and painting, not capture.
- `ui/window.py`: page composition, service callbacks, state binding, keyboard commands, and navigation. It should no longer contain repeated style definitions or a monolithic dashboard form.
- Existing service classes under `capture/`, `recording/`, `compression/`, `storage/`, `networking/`, and `security/` remain unchanged unless a presentation-only adapter is required.

## Validation strategy

1. Run the existing Python tests and Ruff before UI changes.
2. Add focused widget tests for navigation destinations, mode selection, inspector visibility, recording control enablement, and library empty/populated rendering. Tests use offscreen Qt and service fakes; they never claim capture success.
3. Run the actual app in offscreen and X11 sessions with a fixed synthetic source only for visual fixture generation; production preview code continues to use `Sources`.
4. Capture screenshots at 1240×870 and 1060×740 for Studio, Devices, Library, and Settings. Capture an active-recording Studio frame using a real recording state with the existing smoke/integration fixture.
5. Inspect the screenshots for hierarchy, clipping, focus visibility, light/dark contrast, preview ratio, toolbar placement, and compact-width behavior. Fix defects and repeat.
6. Re-run the full test suite, Ruff, and the existing media smoke test. Report platform-specific blur and physical-device verification honestly.

## Success criteria

- The old text sidebar, four-column dashboard card, and permanent Compression Queue navigation item are gone.
- Studio gives the live preview the largest visual area and presents recording as the primary action.
- Advanced controls are contextual and remain functionally equivalent.
- Library is thumbnail-first with search, sorting, quick playback, file actions, and integrated compression status.
- Devices presents pairing as a focused connection flow.
- Materials are centralized, layered only on functional surfaces, and have opaque fallbacks.
- The real application renders without clipping at both target sizes and retains existing recording functionality.
- Visual validation evidence is stored under `docs/screenshots/ui-redesign/` and matches the actual application state.
