# LumaCapture UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current dashboard-style PySide6 interface with a compact, preview-first recording studio while preserving all existing media and service behavior.

**Architecture:** Keep `MainWindow` as the service orchestration boundary, split reusable presentation primitives into `ui/components.py`, move preview painting into `ui/preview.py`, and centralize semantic appearance tokens in `ui/theme.py`. The new shell keeps the existing `Recorder`, `DesktopCapture`, `PhoneServer`, `CompressionQueue`, `Library`, and `Sources` instances and only changes how their state is presented and how existing controls are grouped.

**Tech Stack:** Python 3.12+, PySide6 6.12, Qt Widgets/QSS, Pillow, NumPy, existing PyAV/FFmpeg media services, pytest, and a small local offscreen QApplication fixture (no new test dependency).

**Spec:** `docs/superpowers/specs/2026-10-11-lumacapture-ui-redesign-design.md`

## Global Constraints

- Preserve the existing `Recorder`, capture, WebRTC, compression, storage, security, and recovery behavior.
- Use real frames from `Sources`; never add mock media paths to production code.
- Use glass only on functional overlays and provide opaque fallbacks for unsupported blur, reduced transparency, and high-contrast contexts.
- Keep all desktop controls keyboard reachable with visible focus, tooltips, and accessible names.
- Preserve the existing light/dark setting and do not add a new required dependency.
- Keep UI work non-blocking; no animation or blur pass may run on the recording worker thread.
- Verify with `python -m pytest -q`, `python -m ruff check src tests tools`, the existing media smoke test, and actual rendered screenshots.

---

### Task 1: Establish semantic tokens and reusable presentation primitives

**Files:**
- Create: `src/lumacapture/ui/components.py`
- Create: `src/lumacapture/ui/preview.py`
- Modify: `src/lumacapture/ui/theme.py`
- Modify: `src/lumacapture/ui/__init__.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- `theme.palette(light: bool, high_contrast: bool = False) -> dict[str, str]` returns semantic colors.
- `theme.motion_enabled() -> bool` reads `QT_NO_ANIMATIONS` and the Qt application style hint.
- `PreviewWidget(QWidget)` exposes `set_frame(image: QImage)`, `set_empty_state(title: str, detail: str)`, `set_frame_info(text: str)`, `current_frame() -> QImage`, and emits `fullscreenRequested`.
- `MaterialSurface(QFrame)` accepts `material: str` (`content`, `glass`, `elevated`, or `opaque`) and paints the correct fallback.
- `IconRailButton(QToolButton)` accepts `icon_name`, `tooltip`, and `checked` state and exposes an accessible name.
- `SegmentedControl(QWidget)` exposes `add_item(label: str, value: str)`, `current_value() -> str`, and `valueChanged(str)`.
- `StatusPill(QLabel)` exposes `set_status(label: str, tone: str)`.
- `FloatingTransport(QWidget)` emits `recordRequested`, `pauseRequested`, `stopRequested`, `inspectorRequested`, and `audioToggled(bool)`.

- [ ] **Step 1: Add failing token/component tests**

```python
@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_palette_has_semantic_dark_and_light_roles():
    dark = palette(False)
    light = palette(True)
    assert dark["content"] == "#0B0D12"
    assert dark["recording"] == "#FF453A"
    assert light["content"] != dark["content"]
    assert {"content", "glass", "elevated", "opaque", "text", "secondary", "accent", "focus"} <= dark.keys()


def test_segmented_control_emits_value(qapp):
    control = SegmentedControl()
    control.add_item("Camera", "phone")
    control.add_item("Screen", "screen")
    spy = QSignalSpy(control.valueChanged)
    control.set_current_value("screen")
    assert spy.count() == 1
    assert control.current_value() == "screen"


def test_preview_preserves_current_frame(qapp):
    preview = PreviewWidget()
    frame = QImage(320, 180, QImage.Format.Format_RGB888)
    preview.set_frame(frame)
    assert preview.current_frame().size() == QSize(320, 180)
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the new primitives do not exist**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "palette or segmented or preview"`

Expected: FAIL with import or attribute errors for the new UI primitives.

- [ ] **Step 3: Implement semantic tokens and the primitive widgets**

Use a `PALETTES` mapping for dark/light/high-contrast roles and generate QSS from semantic values. Use Qt's standard `QStyle.SP_*` icons where available; keep a text fallback and tooltip for every icon. Keep `PreviewWidget` responsible only for aspect-ratio-preserving painting and the current frame; it must not know about `Sources` or recording state.

Implement `MaterialSurface.paintEvent` with a translucent fill and edge highlight only for functional surfaces. Expose a boolean `blur_available` and use the opaque palette when `QT_NO_ANIMATIONS`, reduced transparency, or high contrast is active. Do not capture the desktop or use a stale screenshot as a blur source.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "palette or segmented or preview"`

Expected: PASS.

- [ ] **Step 5: Run Ruff for the new files**

Run: `.venv/bin/python -m ruff check src/lumacapture/ui/components.py src/lumacapture/ui/preview.py src/lumacapture/ui/theme.py tests/test_ui.py`

Expected: `All checks passed!`

---

### Task 2: Replace the window shell and build the preview-first Studio

**Files:**
- Modify: `src/lumacapture/ui/window.py`
- Modify: `src/lumacapture/ui/platform_effects.py`
- Modify: `src/lumacapture/ui/theme.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- `MainWindow.pages` contains exactly Studio, Library, Devices, and Settings.
- `MainWindow.navigate(index: int)` continues to select a page and active rail item.
- `MainWindow.change_mode(mode: str)` retains the existing `phone`, `screen`, and `both` values.
- `MainWindow.config()` continues returning `RecordingConfig` with the same field meanings.
- `MainWindow.toggle_preview`, `start_recording`, `pause_recording`, `stop_recording`, and `toggle_server` retain their service behavior.

- [ ] **Step 1: Add failing shell and mode tests**

```python
def test_main_window_has_compact_four_destination_shell(qapp, window):
    assert window.rail.width() == 72
    assert window.pages.count() == 4
    assert [window.rail.button(i).toolTip() for i in range(4)] == [
        "Studio", "Library", "Devices", "Settings"
    ]


def test_mode_selector_changes_existing_recording_mode(qapp, window):
    window.mode_selector.set_current_value("phone")
    assert window.mode == "phone"
    assert window.config().mode == "phone"


def test_recording_controls_are_not_enabled_while_idle_without_source(qapp, window):
    window.tick()
    assert not window.stop_control.isEnabled()
    assert window.record_control.isEnabled()
```

- [ ] **Step 2: Run the shell tests and confirm they fail against the old layout**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "shell or mode_selector or recording_controls"`

Expected: FAIL because the old window has five text destinations and no `rail`, `mode_selector`, or floating control attributes.

- [ ] **Step 3: Implement the compact shell**

Replace `build_ui()` with a root `QHBoxLayout` containing a 72 px `QFrame` rail and a `QStackedWidget`. Create four `IconRailButton` instances with the Studio, Library, Devices, and Settings tooltips and standard Qt icons. Keep the rail always visible, but remove the old brand block and footer copy. Add a compact logo mark at the top and a settings/status action at the bottom without putting critical recording information in the rail.

Create a Studio page with a small header row, `SegmentedControl`, a `QStackedLayout`-based preview region, and a `FloatingTransport`. Replace the old `Preview` class with `PreviewWidget` while keeping the existing `tick()` source-buffer composition logic. Feed the composed `QImage` into `PreviewWidget.set_frame()` and preserve the existing `compose()` call and frame sizing.

Move `start_recording`, `pause_recording`, and `stop_recording` callbacks to the transport signals. Keep text status labels adjacent to icons so color is not the only recording indicator. Add the existing performance text as a compact status line with a tooltip for detailed metrics instead of a full-width paragraph.

- [ ] **Step 4: Implement the floating transport and responsive preview layout**

Use a preview overlay layout with the transport docked to the lower edge of the preview region. The preview itself must use a minimum height of 280 px and a `QSizePolicy.Expanding`. Add a fullscreen button to the preview header using `showFullScreen()`/`showNormal()` without changing the recorder. Use `QTimer` only for existing state refresh and the recording pulse; no timer may be used to create capture frames.

- [ ] **Step 5: Add keyboard commands and focus styling**

Add `QShortcut` actions for `Ctrl+1` through `Ctrl+4` navigation, `Space` record/pause based on current state, `Escape` to close the inspector, and `I` to toggle the inspector. Set accessible names for the rail, mode selector, record, pause, stop, microphone, audio, inspector, and fullscreen controls. Ensure `:focus-visible`-equivalent Qt focus styling uses the focus token.

- [ ] **Step 6: Run the shell tests and full existing tests**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py tests/test_core.py tests/test_media.py tests/test_network.py`

Expected: all existing tests plus the new UI tests pass.

---

### Task 3: Add the contextual Studio inspector and focused Devices surface

**Files:**
- Modify: `src/lumacapture/ui/window.py`
- Modify: `src/lumacapture/ui/components.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- `MainWindow.inspector` is a `QFrame` with `setVisible(bool)` and `toggle_inspector()`.
- `MainWindow.refresh_inspector()` updates visibility/enabled state based on `self.mode` and recording state.
- Existing widgets retain these names so service bindings continue: `monitor`, `resolution`, `fps`, `encoder`, `layout_mode`, `position`, `overlay_size`, `opacity`, `radius`, `border`, `mirror`, `disconnect_policy`, `phone_audio`, `mic_enabled`, `mic_device`, `system_device`, `system_enabled`, `compression_preset`.
- Devices keeps `networks`, `server_btn`, `qr_label`, `pair_url`, `connection_detail`, `toggle_server`, `new_pairing`, `certificate_setup`, and `disconnect_phone` behavior.

- [ ] **Step 1: Add failing inspector tests**

```python
def test_inspector_is_hidden_by_default_and_opens_on_request(qapp, window):
    assert not window.inspector.isVisible()
    window.toggle_inspector()
    assert window.inspector.isVisible()


def test_inspector_hides_screen_controls_for_phone_mode(qapp, window):
    window.change_mode("phone")
    window.refresh_inspector()
    assert not window.monitor.isVisible()
    assert not window.resolution.isVisible()
    assert window.phone_audio.isVisible()


def test_devices_page_has_no_pairing_instructions_on_studio(qapp, window):
    assert window.connection_steps.parentWidget() is window.devices_page_widget
    assert window.connection_steps.parentWidget() is not window.studio_page_widget
```

- [ ] **Step 2: Run the inspector tests and confirm they fail**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "inspector or devices_page"`

Expected: FAIL because the old controls are in the dashboard card and Devices instructions are not separated through named page widgets.

- [ ] **Step 3: Move existing controls into inspector sections**

Create `InspectorSection` containers with a compact header and a collapsible content widget. Reparent the existing capture, audio, composition, and export widgets into the relevant sections. Use `QFormLayout` only inside the inspector, with two-column label/control alignment and no permanent four-column dashboard. `refresh_inspector()` hides screen controls in Phone mode, hides composition controls unless Both is selected, and disables immutable settings while the recorder is active.

- [ ] **Step 4: Refactor Devices into a focused connection surface**

Keep the network selector, server, QR generation, certificate dialog, and disconnect callbacks intact. Replace the large instructional text block with a compact step indicator and a status panel. Move all certificate setup text into the existing dialog. When `PhoneServer` reports a connected state, replace the steps with the live status and disconnect action.

- [ ] **Step 5: Run focused and full tests**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py tests/test_core.py tests/test_media.py tests/test_network.py`

Expected: PASS with existing service behavior unchanged.

---

### Task 4: Replace the table-first Library with a thumbnail-first responsive view

**Files:**
- Create: `src/lumacapture/ui/library_view.py`
- Modify: `src/lumacapture/ui/window.py`
- Modify: `src/lumacapture/ui/components.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- `LibraryView(QWidget)` exposes `refresh(items: list[dict], jobs: list[dict])`, `set_empty_action(callback)`, and `recordingActivated(str)`.
- `LibraryView.search` is a `QLineEdit`; `LibraryView.sort` is a `QComboBox`.
- `MainWindow.library_action(action)` continues to receive `play`, `folder`, `rename`, `compress`, `export`, and `delete`.
- `MainWindow.selected_recording_id() -> str | None` replaces direct table row lookup.

- [ ] **Step 1: Add failing library rendering tests**

```python
def test_library_empty_state_has_next_action(qapp, window):
    window.library_view.refresh([], [])
    assert window.library_view.empty_state.isVisible()
    assert window.library_view.empty_state_button.text() == "Go to Studio"


def test_library_renders_real_thumbnail_and_compression_status(qapp, window, media_file):
    item = window.library.add(media_file, inspect_media(media_file))
    job = window.library.enqueue(item, {"codec": "libx264"})
    window.library_view.refresh(window.library.recordings(), window.library.jobs())
    assert window.library_view.item_count() == 1
    assert window.library_view.item_for(item).thumbnail.isNull() is False
    assert "queued" in window.library_view.item_for(item).status.text().lower()
```

- [ ] **Step 2: Run the library tests and confirm they fail**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "library"`

Expected: FAIL because `LibraryView` does not exist and the old page is a `QTableWidget`.

- [ ] **Step 3: Implement the responsive library view**

Use a `QScrollArea` containing a flow/grid layout of `RecordingTile` widgets. Each tile loads the actual thumbnail path into a `QPixmap`, shows title, duration, date, dimensions/FPS, size, status, and an overflow button. Use a `QStackedWidget` to switch to a compact list layout when the library content width is below 760 px. Search filters filenames case-insensitively. Sort with newest, oldest, duration, and size options without mutating database order.

- [ ] **Step 4: Integrate compression status and existing actions**

Render queued/running/complete/failed/interrupted status on the tile and show progress for running jobs. The overflow menu calls `MainWindow.library_action()` with the existing action strings. Keep delete confirmation and rename safety rules unchanged. Double-click or primary tile activation calls `play`.

- [ ] **Step 5: Run focused and full tests**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py tests/test_media.py tests/test_core.py tests/test_network.py`

Expected: PASS.

---

### Task 5: Refactor Settings into grouped contextual sections and finish materials

**Files:**
- Modify: `src/lumacapture/ui/window.py`
- Modify: `src/lumacapture/ui/theme.py`
- Modify: `src/lumacapture/ui/platform_effects.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- `MainWindow.settings_page()` retains all existing settings widget attributes and persistence callbacks.
- `theme.stylesheet(light: bool, high_contrast: bool = False) -> str` remains the stylesheet entry point used by the window.
- `platform_effects.apply_window_effects(window, dark: bool) -> bool` remains safe on non-Windows platforms.

- [ ] **Step 1: Add failing theme/fallback tests**

```python
def test_stylesheet_uses_material_roles_and_focus_state():
    css = stylesheet(False)
    assert "#0B0D12" in css
    assert "QPushButton:focus" in css
    assert "material" not in css.lower() or "glass" in css.lower()


def test_high_contrast_uses_opaque_surface():
    css = stylesheet(False, high_contrast=True)
    assert "rgba(" not in css
```

- [ ] **Step 2: Run the theme tests and confirm they fail against the old stylesheet signature**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "stylesheet or high_contrast"`

Expected: FAIL because `stylesheet()` has no high-contrast parameter and the old palette is not the new semantic system.

- [ ] **Step 3: Implement token-driven styles and grouped Settings**

Define `PALETTES`, `SPACING`, `RADII`, and `MOTION` constants in `theme.py`. Generate styles for content, elevated, glass, opaque fallback, focus, selected segments, disabled controls, scrollbars, list tiles, and dialogs. Use `QPalette` roles for text and base colors so native child widgets inherit readable values. Use `QFrame#material-glass`, `QFrame#material-elevated`, and `QFrame#material-opaque` object names instead of styling arbitrary cards.

Keep the Settings page as a scrollable form, but group it into compact sections with progressive disclosure: Appearance, Capture, Audio, Composition, Export, and Advanced. All existing settings values and signal connections remain intact.

- [ ] **Step 4: Preserve platform effects and avoid fake blur**

Keep the Windows DWM call and return value. Add a Linux capability function that reports compositor blur as unavailable unless the application can confirm a supported compositor path; use the opaque fallback rather than claiming blur. The internal transport glass samples only the current `PreviewWidget.current_frame()` at bounded size and never reads the desktop.

- [ ] **Step 5: Run focused and full tests**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py tests/test_core.py tests/test_media.py tests/test_network.py`

Expected: PASS.

---

### Task 6: Add real screenshot capture, visual QA, and regression checks

**Files:**
- Create: `tools/ui_screenshots.py`
- Create: `docs/screenshots/ui-redesign/README.md`
- Create: `docs/screenshots/ui-redesign/studio-dark.png`
- Create: `docs/screenshots/ui-redesign/studio-compact.png`
- Create: `docs/screenshots/ui-redesign/devices.png`
- Create: `docs/screenshots/ui-redesign/library.png`
- Create: `docs/screenshots/ui-redesign/settings.png`
- Modify: `README.md`
- Test: `tests/test_ui.py`

**Interfaces:**
- `tools/ui_screenshots.py --output DIRECTORY --width WIDTH --height HEIGHT` launches the actual `MainWindow` with a temporary data directory and saves named page screenshots.
- The screenshot tool uses `QT_QPA_PLATFORM=offscreen` when no display is available and never writes recordings or fake media into production paths.

- [ ] **Step 1: Add failing screenshot-tool smoke test**

```python
def test_screenshot_tool_has_documented_page_targets():
    from tools.ui_screenshots import PAGE_NAMES
    assert PAGE_NAMES == ("studio", "devices", "library", "settings")
```

- [ ] **Step 2: Run the screenshot test and confirm it fails**

Run: `.venv/bin/python -m pytest -q tests/test_ui.py -k "screenshot_tool"`

Expected: FAIL because `tools/ui_screenshots.py` does not exist.

- [ ] **Step 3: Implement screenshot capture against the actual application**

Create a QApplication, instantiate `MainWindow` with a temporary Library/data root, resize it to the requested size, show each page through `navigate()`, process events, and save `window.grab()` images. Seed only UI metadata fixtures for the Library page and use the existing `PreviewWidget` empty state for the baseline Studio image. Add a second invocation path for a live frame supplied through `Sources` so the preview screenshot can show real rendered pixels without changing production behavior.

- [ ] **Step 4: Generate regular and compact screenshots**

Run:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui_screenshots.py --output docs/screenshots/ui-redesign --width 1240 --height 870
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui_screenshots.py --output /tmp/lumacapture-ui-compact --width 1060 --height 740
```

Expected: screenshots for Studio, Devices, Library, and Settings with no clipping or uncaught exceptions.

- [ ] **Step 5: Inspect screenshots and fix visible defects**

Use the image reader on every generated screenshot. Check that the preview is the dominant region, the rail is approximately 72 px, the transport has one primary action, inspector/settings content does not leak into Studio, library tiles show real thumbnails, the QR surface is focused, and compact dimensions do not clip controls. Repeat the screenshot commands after any correction.

- [ ] **Step 6: Update README with actual redesign evidence**

Replace the dashboard screenshot reference with `docs/screenshots/ui-redesign/studio-dark.png`, add the Library and Devices screenshots, and describe the compact rail, contextual inspector, and opaque blur fallback accurately. Do not claim compositor blur or platform behavior that was not observed on the verification host.

- [ ] **Step 7: Run the complete verification suite**

Run:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests tools
.venv/bin/python -m lumacapture.app --smoke-test --data-dir /tmp/lumacapture-redesign-smoke --screenshot /tmp/lumacapture-redesign-smoke.png
```

Expected: all tests pass, Ruff passes, and the real application smoke test emits valid media metadata and a screenshot.

---

## Final review checklist

- [ ] `git diff --check` passes.
- [ ] No capture, recording, WebRTC, compression, database, certificate, or security service files were changed unintentionally.
- [ ] The old five-item text navigation and four-column dashboard card are gone.
- [ ] Studio, Library, Devices, and Settings remain reachable from the rail and keyboard shortcuts.
- [ ] The preview uses real `Sources` frames and retains aspect ratio.
- [ ] Recording controls remain enabled/disabled according to `State` and stop remains available during finalization.
- [ ] Inspector fields preserve existing `RecordingConfig` and settings persistence.
- [ ] Library actions preserve existing safety checks and originals remain retained.
- [ ] Screenshots were inspected at 1240×870 and 1060×740.
- [ ] README screenshots and claims match the generated evidence.
