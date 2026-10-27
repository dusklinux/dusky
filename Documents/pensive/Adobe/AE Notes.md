---
title: "Adobe After Effects 26.5 — Complete Working Guide"
aliases:
  - AE Master Guide
  - After Effects Shortcuts
  - After Effects Effects Handbook
tags:
  - after-effects
  - motion-design
  - compositing
  - vfx
  - keyboard-shortcuts
  - reference
application: Adobe After Effects
version: "26.5 (September 2026 stable release)"
verified_as_of: 2026-10-27
platform: "Windows; default English keyboard layout"
status: reference
---

# After Effects 26.5 — The Complete Working Guide

> [!abstract] What this note is
> A **learn-by-doing manual**, **Windows keyboard-shortcut reference**, **effects field guide**, and **troubleshooting playbook** for returning users and beginners. It covers the **September 2026 After Effects 26.5 stable release**, not features that exist only in Beta.
>
> **Windows only.** Shortcut labels refer to the default English keyboard preset. Learn the first table, then use the categorized sections as a searchable desk reference.
>
> **Coverage:** core learning workflows, the documented default shortcut families, an effects directory, recent stable changes, and a separate Beta watchlist. The installed shortcut editor resolves custom presets and documentation conflicts; this note does not reproduce every parameter of every plugin.
>
> **Start here:** [[#01 — Learn the Logic First]] → [[#02 — The 35 Shortcuts Worth Memorizing First]] → [[#03 — Complete Shortcut Reference]] → [[#04 — The Core Workflows]] → [[#05 — Effects Encyclopedia]]. This file works offline; external documentation links are included only for future verification.

## Contents

1. [[#01 — Learn the Logic First|How AE thinks; how shortcuts work]]
2. [[#02 — The 35 Shortcuts Worth Memorizing First|The first shortcuts to internalize]]
3. [[#03 — Complete Shortcut Reference|Full categorized shortcut reference]]
4. [[#04 — The Core Workflows|Step-by-step compositing and animation]]
5. [[#05 — Effects Encyclopedia|What the effects actually do]]
6. [[#06 — Everything New and Important Through 26.5|Recent releases and modern features]]
7. [[#07 — Troubleshooting and Performance|Work faster; solve common failures]]
8. [[#08 — Practical Recipes (Copy the Method, Not Just the Shortcut)|Repeatable real-world techniques]]
9. [[#09 — Expressions and Data-Driven Animation|Expression cheat sheet; JSON/CSV]]
10. [[#10 — Practice Curriculum (Rebuild Muscle Memory)|Rebuild muscle memory]]
11. [[#11 — Glossary and Reference Sources|Definitions; verified documentation]]

---

# 01 — Learn the Logic First

## The mental model

Think of After Effects as a **timeline of layers inside compositions**. A composition (comp) is a scene, a layer is an element within that scene, a property describes an element, and a keyframe describes how a property changes **over time**. An **effect** processes the image or audio. A **mask** limits a layer's pixels. A **track matte** uses another layer to decide what remains visible. A **precomposition** is a composition nested into another composition.

```text
PROJECT (.aep)
  ├─ Imported sources: clips / audio / stills / PSD / AI / SVG / 3D models / data
  └─ COMPOSITION (resolution, FPS, duration, color management)
       ├─ Layer 1 ─ Transform ─ Position / Scale / Rotation / Opacity / Anchor
       │             ├─ Masks / Effects / Styles / Expressions / Keyframes
       │             └─ Parent / Track matte / 3D settings
       ├─ Layer 2 ─ ...
       └─ Precomp ─ its own layers, timeline, and properties
```

### Seven distinctions that prevent most beginner mistakes

| Concept A | Concept B | What changes in practice |
|---|---|---|
| **Anchor point** | **Position** | Anchor is the pivot **inside layer coordinates**; Position locates that pivot in parent/comp space. Move anchor without moving the visible image using **Y** (Pan Behind) or **Ctrl+Alt+Home** (center anchor in visible content). |
| **Move a layer** | **Trim a layer** | `[` / `]` shifts a layer in time without cutting content; `Alt+[` / `Alt+]` cuts the visible in/out at the playhead. |
| **Layer switches** | **Effects** | The **fx** layer switch disables layer effects **without deleting them**; `Ctrl+Shift+E` **deletes all effects** from selected layers. These are not interchangeable. |
| **Mask** | **Shape** | A path drawn on a selected image/solid commonly creates a mask; drawn with no layer selected it creates a shape layer. Use toolbar **Tool Creates Mask / Tool Creates Shape** settings to choose intentionally. |
| **Opacity** | **Alpha** | Opacity is one property; alpha also comes from masks, effects, mattes, footage transparency and compositing. |
| **Continuous Rasterization** | **Collapse Transformations** | They share a star/sun-like switch, but vector layers use it to keep vector edges sharp; precomps use it to collapse the nested transform/render order. It is **not** safe to enable blindly. |
| **8/16/32 bpc** | **10-bit source** | AE's project calculation precision is not the same as source encoding bit depth. A 10-bit file can be processed in a 16-bpc or 32-bpc AE project. |

### What a shortcut actually means

- **Focus matters.** Press `P` with Timeline focus to show Position; type `p` inside a text box and it types a letter. A shortcut can have a different meaning inside a different panel.
- **Selection matters.** `Ctrl+D` duplicates a selected layer, effect, or item depending on where selection lives.
- **`Ctrl`**, **`Alt`**, **`Shift`**, and **`Win`** are not interchangeable. This guide uses **Windows only**. `Win` refers to the Windows key; operating-system shortcuts may intercept application commands.
- **Main keyboard ≠ numeric keypad.** `0` on the **numpad** previews; `0` on the main keyboard can be a composition marker. A laptop may require `Fn` to access Home/End/PgUp/PgDn, or may lack a numpad entirely.
- **Tap twice quickly:** `UU`, `EE`, `LL`, `MM`, `RR`, `AA`, `PP`, `SS`, `TT`, and `FF` are **sequences**, not simultaneous key combinations.
- **Unless identified otherwise, shortcuts below are default Windows shortcuts** based on Adobe's public shortcut reference, updated May 2026, with newer documented shortcuts added separately. A changed/custom keyboard preset can override them.

> [!tip] The shortcut editor is your definitive installed-version check
> **Edit → Keyboard Shortcuts** = `Ctrl+Alt+'`. Search a *command name*, see whether it already has a binding, check the **Application** versus **panel-specific** context, and save your custom preset. Some property-reveal sequences cannot be reassigned. Do **not** assume every possible menu command already has a default shortcut. Put custom bindings in your own section at the end of this note.

## Start every new project with these decisions

1. **Composition:** select delivery width/height, frames per second, duration, and pixel aspect ratio (`Ctrl+N`, then `Ctrl+K` to revisit).
2. **Color:** check Project Settings and use an intentional SDR/linear/HDR/ACES workflow. Increasing project bpc does not manufacture lost detail.
3. **Footage:** verify interpreted frame rate, alpha interpretation, color metadata, and audio sync; transcode troublesome variable-frame-rate footage to a constant-frame-rate mezzanine if needed.
4. **Structure:** label/color important layers, precomp by purpose, name controls, and save incrementally (`Ctrl+Alt+Shift+S`).
5. **Preview:** half/quarter resolution, skip frames and shorter work area can accelerate *preview*; check Render Settings for final resolution and quality; use explicit Full/Best settings when that is the delivery requirement.
6. **Render:** pick the **deliverable**, not a dogma: directly export H.264 for ordinary reviews/delivery, or a high-quality mezzanine/master (ProRes, DNxHR, EXR) for additional processing, alpha, archival or client specifications.

---

# 02 — The 35 Shortcuts Worth Memorizing First

These build the fastest return to AE. Learn them by **task**, not by alphabet.

| Task | Windows shortcut | Memory hook |
|---|---|---|
| Selection tool | `V` | **V** = selection arrow |
| Hand / move around viewer | `H` / hold `Space` | **H**and |
| Pan Behind / anchor tool | `Y` | Edit the pivot, not the object |
| Pen / mask-feather tools | `G` | Draw/edit paths |
| Cycle shape tools | `Q` | Rectangle, ellipse, polygon, star |
| Text tool | `Ctrl+T` | **T**ype (when not editing a property) |
| Position | `P` | **P**osition |
| Scale | `S` | **S**cale |
| Rotation/orientation | `R` | **R**otate |
| Opacity | `T` | **T**ransparency |
| Anchor point | `A` | **A**nchor |
| Add another visible property | `Shift+P/S/R/T/A` | Keep existing properties open |
| Show animated properties | `U` | **U** = used keyframes |
| Show modified properties | `U`, `U` | **UU** = changed from defaults, incl. expressions/effects |
| Show expressions | `E`, `E` | **EE** = expressions |
| Show effect group | `E` | **E**ffects |
| Show audio waveform | `L`, `L` | **LL** = waveform |
| Toggle animation on property | Click stopwatch | Do it deliberately |
| Add/remove keyframe now | `Alt+Shift+P/S/R/T/A` | Toggle a key at the current time |
| Easy Ease | `F9` | Smooth velocity in/out |
| Show Graph Editor | `Shift+F3` | Edit velocity curves |
| One frame forward/backward | `Page Down` / `Page Up` | Frame stepping |
| Next/previous visible key/marker | `K` / `J` | K = next, J = previous |
| Layer in/out | `I` / `O` | **I**n / **O**ut |
| Work area start/end | `B` / `N` | Boundary markers |
| Move layer in/out to playhead | `[` / `]` | Slide intact bar to time |
| Trim layer start/end at playhead | `Alt+[` / `Alt+]` | Cut bar at time |
| Split layer | `Ctrl+Shift+D` | Duplicate-and-split edit |
| Duplicate selection | `Ctrl+D` | **D**uplicate |
| Precompose | `Ctrl+Shift+C` | Put selected layers in nested comp |
| Add solid / adjustment / null | `Ctrl+Y` / `Ctrl+Alt+Y` / `Ctrl+Alt+Shift+Y` | Three controller-layer shortcuts |
| Quick Apply (26.2+) | `Ctrl+Enter` | Search effects, presets **and** commands |
| Keyboard Shortcut Editor | `Ctrl+Alt+'` | Check/change bindings |
| Toggle Timeline switches / modes | `F4` | Reveal matte and blending controls |
| Save | `Ctrl+S` | Save frequently |

> [!example] 60-second warm-up
> Create a new comp → `Ctrl+Y` add solid → `P` → click Position stopwatch → move forward 15 frames with `Shift+Page Down`, then five `Page Down` → move the object → select both keyframes → `F9` → `Shift+F3` adjust speed graph → `UU` inspect changed values → `Space` preview → `Ctrl+S`. Do this without opening menus.

---

# 03 — Complete Shortcut Reference

> [!info] Reading the tables
> **All entries in this section are Windows defaults unless noted.** Use the headings in Obsidian’s Outline panel to jump between tasks. The tables intentionally include some *mouse gestures* and *contextual* actions: their exact action is often faster than a key combination. **`Num`** means the numeric keypad. Keyboard punctuation follows the default English layout; laptop function-key settings and remapped presets can change which physical keys you need.


### Find the right shortcut table quickly

| I need to… | Go to |
|---|---|
| Open, save, import, create or export | [[#3.1 File, project, composition and application]] |
| Fix panels or change focus | [[#3.2 Windows, workspace, panels and viewer]] |
| Choose a drawing, anchor, paint or camera tool | [[#3.3 Tools and drawing]] |
| Navigate time, preview or zoom | [[#3.4 Navigation: frames, keyframes, work area and preview]] |
| Select, reorder, stagger, trim or split layers | [[#3.5 Timeline: select, stack, move, trim, split and time]] |
| Reveal properties or transform artwork | [[#3.6 Properties and layer transforms]] |
| Adjust keyframes or easing | [[#3.7 Keyframes and Graph Editor (full set)]] |
| Edit typography | [[#3.8 Text keyboard commands (when actively editing text)]] |
| Edit masks, shapes, paint, markers or tracking regions | [[#3.9 Masks, shapes, painting and markers]] |
| Work with cameras, footage or render commands | [[#3.10 3D cameras, lights, effects, footage and rendering]] |
| Inspect alpha, channels, guides or grids | [[#3.11 Guides, grids, channels and inspection]] |
| Search multiple terms or use toolbar alternatives | [[#3.14 Search and mouse alternatives]] |
| Use specialist gestures or diagnose key conflicts | [[#3.12 Less frequent commands and contextual gestures]] |
| Edit graphs precisely or distinguish snapshots from copied frames | [[#3.13 Precision editing and frame capture]] |

## 3.1 File, project, composition and application

| Action | Windows | Clarification |
|---|---|---|
| New project | `Ctrl+Alt+N` | New, empty `.aep` |
| Open project | `Ctrl+O` | Open `.aep` / project |
| Open most recent project | `Ctrl+Alt+Shift+P` | Recents |
| Save project | `Ctrl+S` | Save often |
| Save As | `Ctrl+Shift+S` | Separate copy/name |
| Increment and Save | `Ctrl+Alt+Shift+S` | Versioned iterative save |
| New composition | `Ctrl+N` | A new scene and timeline |
| Composition Settings | `Ctrl+K` | Selected/active comp |
| New composition from selection | `Alt+\` | Selected footage/items |
| Project Settings | `Ctrl+Alt+Shift+K` | Color, renderer-related project controls |
| New folder in Project panel | `Ctrl+Alt+Shift+N` | Folder in project hierarchy |
| Import file/sequence | `Ctrl+I` | Choose asset |
| Import multiple files/sequences | `Ctrl+Alt+I` | Multi-file importer |
| Edit original source | `Ctrl+E` | Open associated app |
| New solid | `Ctrl+Y` | Color solid/blank canvas |
| Edit selected solid/light/camera/null/adjustment settings | `Ctrl+Shift+Y` | Type-specific settings |
| New adjustment layer | `Ctrl+Alt+Y` | Effects affect composited pixels below |
| New null object | `Ctrl+Alt+Shift+Y` | Parenting and controllers |
| New text layer | `Ctrl+Alt+Shift+T` | May be context-dependent while editing text |
| Precompose selection | `Ctrl+Shift+C` | Nest in child comp |
| Trim comp to work area | `Ctrl+Shift+X` | Changes composition duration |
| Work area from selected layer span, or full comp if none | `Ctrl+Alt+B` | Quick range reset |
| Add comp to Render Queue | `Ctrl+Shift+/` (main keyboard) | `/` is slash, not numpad divide |
| Add comp to Adobe Media Encoder | `Ctrl+Alt+M` | Encoding queue |
| Render current frame via Render Queue | `Ctrl+Alt+S` | Still frame |
| Keyboard Shortcut Editor | `Ctrl+Alt+'` | Remap a command |
| Preferences | `Ctrl+Alt+;` | Semicolon, **not** apostrophe |
| Purge all memory | `Ctrl+Alt+/` (Num) | Discards caches; may require recaching |
| Quit AE | `Ctrl+Q` | Save work first |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Shift+Z` | Standard edit history |
| Select all / Deselect all | `Ctrl+A` / `F2` or `Ctrl+Shift+A` | Current active context |
| Rename selected item | `Enter` (main keyboard) | Selected layer, effect, comp, project folder, mask |
| Open selected item | `Enter` (Num) | Unlike main Enter |
| Duplicate selected item | `Ctrl+D` | Works on layer, mask, effect, property group, project items |
| Cut / Copy / Paste | `Ctrl+X` / `Ctrl+C` / `Ctrl+V` | Context-sensitive |
| Paste layers at current time | `Ctrl+Alt+V` | Timeline-specific paste |
| Open Composition Mini-Flowchart | `Tab` | Active composition and its hierarchy; Project Flowchart uses `Ctrl+F11`. |
| Project panel search | `Ctrl+F` with Project focus | With Timeline focus searches Timeline instead |
| Display source frame filename in Info panel | `Ctrl+Alt+E` | At current time |
| Stop running script | `Esc` | Interrupt execution where supported |

## 3.2 Windows, workspace, panels and viewer

| Action | Windows | Notes |
|---|---|---|
| Project panel | `Ctrl+0` | |
| Render Queue panel | `Ctrl+Alt+0` | |
| Tools panel | `Ctrl+1` | |
| Info panel | `Ctrl+2` | Pixel samples, cursor coords and durations |
| Preview panel | `Ctrl+3` | Work area, frame skipping, audio |
| Audio panel | `Ctrl+4` | |
| Effects & Presets | `Ctrl+5` | |
| Character | `Ctrl+6` | |
| Paragraph | `Ctrl+7` | |
| Paint | `Ctrl+8` | |
| Brushes | `Ctrl+9` | |
| Effect Controls for selected layer | `F3` or `Ctrl+Shift+T` | Direct control panel |
| Project Flowchart | `Ctrl+F11` | Distinct from mini-flowchart |
| Assigned workspaces | `Shift+F10/F11/F12` | If workspace presets configured |
| Close active panel/viewer content | `Ctrl+W` | Panel may close content before frame |
| Close all viewers of active type / active panel | `Ctrl+Shift+W` | Can close multiple timelines |
| Split a viewer (opposite lock state) | `Ctrl+Alt+Shift+N` | Lock one viewer for side-by-side comparisons |
| Maximize panel under mouse | `` ` `` | Grave/backtick above Tab; tap again to restore |
| Resize application window to fit screen | `Ctrl+\` | Not true OS-exclusive fullscreen |
| Move window to main monitor / fit | `Ctrl+Alt+\` | |
| Switch focus between Comp and Timeline | `\` | No Ctrl |
| Previous/next open item in viewer | `Shift+,` / `Shift+.` | |
| Previous/next panel in same frame | `Alt+Shift+,` / `Alt+Shift+.` | |
| Show Switches/Modes columns | `F4` | Needed for matte and blending controls |
| Show/hide Parent column | `Shift+F4` | Parent & Link |
| Timeline search | `Ctrl+F` with Timeline focus | Type part of a property/effect/layer name |
| Expand/collapse selected layer completely | **Ctrl + backtick** (`Ctrl` + `` ` ``) | Backtick character |
| Expand nested property group recursively | `Ctrl+click` disclosure triangle | Contextual |
| Hide a visible property or group | `Alt+Shift+click` property/group name | **Not** Ctrl+Alt+Shift+click |
| Scroll top selected layer to top of timeline | `X` | Does not change stacking order |
| Toggle color display management in active view | `Shift+/` (Num) | Display only, not file conversion |

> [!tip] The quickest interface reset
> Hover a panel and press the **backtick** key to fill the screen temporarily. Press again to return. If blending modes/mattes are missing, press `F4`. If nothing is animating visually, verify **Caps Lock is off**. For a completely mangled panel layout, choose **Window → Workspace → Reset [workspace] to Saved Layout**.

## 3.3 Tools and drawing

| Tool/action | Windows | Notes |
|---|---|---|
| Selection | `V` | Default move/selection |
| Hand | `H` | Pan viewer |
| Hand momentarily | Hold `Space` / middle mouse | During most drawing/editing |
| Zoom In | `Z` | Alt while active temporarily zooms out |
| Rotation | `W` | Rotate in viewer |
| Pan Behind (Anchor Point) | `Y` | Shift pivot while preserving visual placement |
| Rectangle/Rounded Rectangle/Ellipse/Polygon/Star | `Q` repeated | Cycle modes |
| Pen/Mask Feather | `G` repeated | Preferences can change cycling behavior |
| Horizontal/Vertical Type | `Ctrl+T` repeated | Activates text tool; text-area vs point-text depends on dragging/clicking |
| Brush/Clone Stamp/Eraser | `Ctrl+B` repeated | All paint *in Layer viewer* |
| Puppet tools | `Ctrl+P` repeated | Pin, starch, overlap etc. as available |
| Camera tools | `C` repeated | Camera context determines available tools |
| Roto Brush/Refine Edge (legacy context) | `Alt+W` | Object Matte tool family added in 2026; see below |
| Object Matte tool (26.2+) | Toolbar; verify `Ctrl+W` locally | Dedicated help lists this binding, but it conflicts with Close; see §3.12. |
| Temporarily use Selection from Pen | Hold `Ctrl` | With Pen selected |
| Temporarily edit path from Selection | Hold `Ctrl+Alt` over path | Can select/add/convert vertices |
| Temporarily duplicate shape | Hold `Alt` with selection tool | Shape context |
| Temporarily direct-select shape | Hold `Ctrl` with selection tool | Shape context |
| Use a single-letter tool temporarily | Hold its tool key, then release | Returns to the previous tool; tap to select it persistently. |
| Cycle tool variations | `Alt+click` tool icon | Some groups only |

**Drawing modifiers (held *while drawing*, before mouse-up):** `Shift` = square/circle or constrain rotation; `Space` = reposition new shape; `Up/Down` = more/fewer polygon sides or star points; `Left/Right` = change corner or outer roundness; `Page Up/Down` = star inner roundness; `Ctrl` = star outer radius. Double-clicking certain shape tools makes a comp-sized shape/mask in the relevant context. Hover the Tool Creates Shape/Mask settings and check selected layers to avoid drawing the wrong thing.

## 3.4 Navigation: frames, keyframes, work area and preview

| Need to... | Windows | Notes |
|---|---|---|
| Go **forward** one frame | `Page Down` or `Ctrl+Right` | Not Alt+Right (moves keyframes) |
| Go **backward** one frame | `Page Up` or `Ctrl+Left` | |
| Go ±10 frames | `Shift+Page Down/Up` or `Ctrl+Shift+Right/Left` | |
| Beginning/end of composition | `Home` / `End` | Or `Ctrl+Alt+Left/Right` |
| Beginning/end of **work area** | `Shift+Home` / `Shift+End` | |
| Next/previous visible timeline item | `K` / `J` | Visible keys/markers and work-area boundaries; Layer-view roto spans add relevant boundaries/base frames. |
| Layer in/out | `I` / `O` | With multiple layers: earliest In / latest Out. |
| Next/previous layer in/out (any layer) | `Ctrl+Alt+Shift+Right/Left` | |
| Scroll Timeline to current time | `D` | Center current-time region |
| Jump to a particular time | `Alt+Shift+J` | Enter timecode/time |
| Work area start / end at playhead | `B` / `N` | Independent of layer in/out |
| Work area from selected layers / comp | `Ctrl+Alt+B` | Start/end bounds |
| Start/stop playback | `Space`, `Shift+Space` or `Num 0` | Preview panel controls caching/looping |
| Audio-only preview from playhead | `Num .` | Numpad decimal |
| Audio-only preview work area | `Alt+Num .` | |
| Preview alternate-frame count | `Alt+Num 0` | Preview prefs govern count |
| Navigate keys/markers of selected layers/properties | `Shift+J` / `Shift+K` | Previous / next within the selection. |
| Snap playhead during a drag | `Shift+drag` CTI | Snaps to eligible keys/markers. |
| Copy current frame to clipboard (26.3+) | `Ctrl+Alt+Shift+F5` | Activate a viewer first; see §3.13 for color/alpha behavior. |
| Manual audio scrubbing | `Ctrl+drag` current-time indicator | Audio scrub |
| Snapshot to slot 5–8 | `Shift+F5` … `Shift+F8` | Four snapshot slots |
| Display snapshot slot 5–8 | `F5` … `F8` | Compare frames |
| Purge snapshot slot 5–8 | `Ctrl+Shift+F5` … `Ctrl+Shift+F8` | |
| Zoom Timeline IN/OUT | `=` / `-` (main keyboard) | Time magnification |
| Timeline magnification toggle | `;` | One-frame units ↔ composition extent |
| Show entire Timeline duration | `Shift+;` | Toggle previous view |
| Viewer zoom IN/OUT | `.` / `,` (main keyboard) | **Not** period = audio preview |
| Viewer at 100% | `/` (main keyboard) | Pixel-level review |
| Fit viewer to panel | `Shift+/` (main keyboard) | `?` on US layout |
| Fit viewer, cap at 100% | `Alt+/` (main keyboard) | Useful for quality review |
| Center comp in viewer | Double-click Hand tool | |
| Stop viewer render while changing timeline | `Caps Lock` | Must turn OFF to judge visuals |
| Comp full/half/custom preview resolution | `Ctrl+J` / `Ctrl+Shift+J` / `Ctrl+Alt+J` | Resolution dropdown |
| Fast Previews: Off | `Ctrl+Alt+1` | |
| Fast Previews: Adaptive Resolution | `Ctrl+Alt+2` | |
| Fast Previews: Draft | `Ctrl+Alt+3` | |
| Fast Previews: Fast Draft | `Ctrl+Alt+4` | |
| Fast Previews: Wireframe | `Ctrl+Alt+5` | |
| Mercury Transmit preview | `Num /` | External monitor output |

## 3.5 Timeline: select, stack, move, trim, split and time

| Action | Windows | Watch out for |
|---|---|---|
| Select next / previous layer in stack | `Ctrl+Down` / `Ctrl+Up` | Panel must be Timeline |
| Extend layer selection downward/upward | `Ctrl+Shift+Down` / `Ctrl+Shift+Up` | |
| Select layer number 1–999 | Type its number on **numpad** | Fast entry for 2–3 digits |
| Add/remove numbered layer to selection | `Shift`+numpad number | |
| Deselect layers | `F2` / `Ctrl+Shift+A` | |
| Move selected layer backward/forward ONE layer in stack | `Ctrl+Alt+Down/Up` | Affects compositing order, **not** time |
| Move layer(s) to bottom/top of stack | `Ctrl+Alt+Shift+Down/Up` | |
| Duplicate layers | `Ctrl+D` | Does not advance time |
| Move entire layer bar so **IN** starts at playhead | `[` | Preserves layer span |
| Move entire layer bar so **OUT** ends at playhead | `]` | Preserves layer span |
| **Trim** start to playhead | `Alt+[` | Does not move rest of layer |
| **Trim** end to playhead | `Alt+]` | Does not move rest of layer |
| Shift whole layer ±1 frame | `Alt+Page Up/Down` | Distinct from keyframe nudge |
| Shift whole layer ±10 frames | `Alt+Shift+Page Up/Down` | |
| Move layer IN to start of comp | `Alt+Home` | Align full layer start |
| Move layer OUT to comp end | `Alt+End` | Align full layer end |
| Split at playhead | `Ctrl+Shift+D` | Splits selected layers; with none selected, splits all eligible layers. |
| Reverse layer(s) in time | `Ctrl+Alt+R` | Time → Time-Reverse Layer |
| Enable Time Remapping | `Ctrl+Alt+T` | Source-time keyframes; works with time-based layers, incl. footage/precomp |
| Precompose | `Ctrl+Shift+C` | Choosing *Move all attributes* vs *Leave all attributes* matters |
| Lock selected layers | `Ctrl+L` | Prevent accidental edits |
| Unlock all layers | `Ctrl+Shift+L` | Unlocks **all**, not a subset |
| Toggle selected layer's video switch | `Ctrl+Alt+Shift+V` | Eye, not effects switch |
| Disable other solo switches | `Alt+click` Solo | Efficient isolation |
| Toggle Switches/Modes | `F4` | Reveals TrkMat and blending mode |
| Cycle blending modes | `Shift+-` / `Shift+=` | Selected layers |
| Find in Timeline | `Ctrl+F` | Property/layer search |
| Show/hide Parent & Link column | `Shift+F4` | |
| Quality: Best / Draft / Wireframe | `Ctrl+U` / `Ctrl+Shift+U` / `Ctrl+Alt+Shift+U` | Affects layer sampling/quality |
| Sampling: Bilinear / Bicubic | `Alt+B` / `Alt+Shift+B` | Visual resampling method |
| Quick Offset (25.4+) | **Ctrl+Alt+drag** selected layer bars or selected keyframes | Distributes them across time in **selection order**; first selection stays in place |
| Sequence Layers | `Animation → Keyframe Assistant → Sequence Layers` | Arrange sequentially; optionally overlap |
| Time-Reverse Keyframes | `Animation → Keyframe Assistant → Time-Reverse Keyframes` | Reverses selected **keyframes**, not source layer |
| Reverse Paste Keyframes | `Ctrl+Shift+V` | Paste previously copied keyframes in reverse order |

> [!example] Remember these four pairs
> `Page Up/Down` moves the **playhead**; `Alt+Page Up/Down` moves the **whole layer**. `Alt+Left/Right` moves **keyframes**. `[` / `]` repositions a **layer boundary**; `Alt+[` / `Alt+]` actually **trims** it. `Ctrl+Alt+R` reverses **source playback**; *Time-Reverse Keyframes* reverses only the **timing of selected animation keys**.

### Keep the relative timing of several layers — 26.0+

| Goal at the playhead | Windows | Result |
|---|---|---|
| Align the beginning of a staggered selection | `Shift+[` | The earliest In point meets the playhead; all selected layers move together. |
| Align the end of a staggered selection | `Shift+]` | The latest Out point meets the playhead; relative layer timing remains intact. |
| Adjust the selection's starting boundaries together | `Alt+Shift+[` | Trim/extend relative In points using the earliest In point as the reference. |
| Adjust the selection's ending boundaries together | `Alt+Shift+]` | Trim/extend relative Out points using the latest Out point as the reference. |

Without `Shift`, the bracket commands act on **each layer's own** boundary. Use two staggered solids to learn the difference before editing a complex selection. Extending footage still depends on available source frames. [Adobe: layer timing and group edits](https://helpx.adobe.com/after-effects/desktop/work-with-layers/select-and-arrange-layers/selecting-arranging-layers.html).

## 3.6 Properties and layer transforms

| Show only... | Windows | What it shows |
|---|---|---|
| Anchor Point / point of interest | `A` | Pivot |
| Position | `P` | Position in comp/parent space |
| Scale | `S` | x/y(/z) percentages |
| Rotation / Orientation | `R` | Z, plus 3D values if enabled |
| Opacity / light Intensity | `T` | Transparency |
| Audio Levels | `L` | dB gain |
| Audio waveform | `LL` | Drawn waveform |
| Time Remap | `RR` | Requires remapping enabled |
| Effect group | `E` | All applied effects |
| Expression-bearing properties | `EE` | Property with expression, enabled or modified |
| Keyframed properties | `U` | Properties with keyframes |
| Modified properties | `UU` | Changed-from-default settings, including effects |
| Masks | `MM` | All mask groups |
| Mask path | `M` | Paths only |
| Mask feather | `F` | Feather only |
| Mask opacity | `TT` | Opacity for masks |
| Material options (3D) | `AA` | Surface/material settings |
| Paint/Puppet/Roto strokes | `PP` | Deformation/paint details |
| Missing effects | `FF` | Locate unavailable effects |
| Selected properties/groups only | `SS` | Isolate selected parameters in timeline |
| Multiple sets of reveal shortcuts | `Shift+P` etc. | Add/remove without collapsing the rest |

| Modify/action | Windows | Detail |
|---|---|---|
| Add/remove keyframe on a revealable property at current time | `Alt+Shift+P/S/R/T/A` etc. | Does not require opening stopwatch; must have valid layer and property |
| Edit Position using numeric dialog | `Ctrl+Shift+P` | With selected layer, not text editing |
| Edit Rotation numeric dialog | `Ctrl+Shift+R` | |
| Edit Opacity numeric dialog | `Ctrl+Shift+O` | **O**, not T |
| Auto-Orient dialog | `Ctrl+Alt+O` | Orient along path / toward camera |
| Move layers 1 viewer pixel | `Arrow keys` | Zoom-dependent composition-space increment |
| Move layers 10 viewer pixels | `Shift+Arrow keys` | Magnification matters |
| Move value at normal increments | Drag hot-text value | Numeric scrub |
| Move value 10× faster | `Shift+drag` hot-text | |
| Move value 10× slower | `Ctrl+drag` hot-text | Fine adjustment |
| Center **layer** anchor position in comp view | `Ctrl+Home` | Changes Position; doesn't center anchor inside content |
| Center **anchor point** in visible layer content | `Ctrl+Alt+Home` | Changes anchor and compensates position |
| Reset Rotation to 0° | Double-click Rotation tool | |
| Reset Scale to 100% | Double-click Selection tool | |
| Rotate Z 1° / 10° | `Num +/-` / `Shift+Num +/-` | Selected layers |
| Scale +/−1% | `Ctrl+Num +/-` or `Alt+Num +/-` | |
| Scale +/−10% | `Ctrl+Shift+Num +/-` or `Alt+Shift+Num +/-` | |
| Opacity +/−1% | `Ctrl+Alt+Num +/-` | |
| Opacity +/−10% | `Ctrl+Alt+Shift+Num +/-` | |
| Constrain interactive rotation 15° | Hold `Shift` while using Rotation tool | |
| Preserve aspect ratio when changing layer bounding-box size | Hold `Shift` while dragging handle | Behavior also depends on current transform modes |
| Fit layer by distortion to comp width **and** height | `Ctrl+Alt+F` | Can stretch image |
| Fit layer to **comp width** preserving ratio | `Ctrl+Alt+Shift+H` | H = horizontal |
| Fit layer to **comp height** preserving ratio | `Ctrl+Alt+Shift+G` | G = vertical mapped shortcut |
| Effects: disable selected layer's effects | Click timeline **fx** switch | Nondestructive disable |
| Effects: delete **all** on selected layer(s) | `Ctrl+Shift+E` | **Destructive** removal |
| Copy with property links | `Ctrl+Alt+C` (where exposed) / `Edit → Copy with Property Links` | Creates expressions referencing source property; test across comps |
| Copy Expression Only | `Edit → Copy Expression Only` | Assign custom key if desired; does not copy values |
| Add an expression to a selected property | `Alt+Shift+=` / Animation → Add Expression | Adobe-documented binding; verify your installed preset. Use the expression switch for temporary bypass. |
| Toggle expression editor for one property | `Alt+click` stopwatch | Can **remove** existing expression; prefer `=` switch to disable temporarily |

> [!warning] Scale vs position dimensions
> **Separate Dimensions** is available for eligible Position properties (X/Y/Z Position); it removes the *single* motion-path representation for that property. Scale has a **constrain-proportions link** but not the same Separate Dimensions command. Keep Position combined when you want to edit a spatial Bézier trajectory in the Comp viewer.

## 3.7 Keyframes and Graph Editor (full set)

| Task | Windows | Meaning |
|---|---|---|
| Show Graph Editor | `Shift+F3` | Toggle timeline/graph |
| Add/remove keyframe at CTI | `Alt+Shift+P/S/R/T/A` etc. | Current property value |
| Go previous / next visible key | `J` / `K` | Also jumps timeline markers/bounds |
| Easy Ease | `F9` | Eases into *and* out of keyframe |
| Easy Ease In | `Shift+F9` | Slow into selected keyframe |
| Easy Ease Out | `Ctrl+Shift+F9` | Slow away from selected keyframe |
| Keyframe Velocity dialog | `Ctrl+Shift+K` | Precise speeds/influence |
| Keyframe Interpolation dialog | `Ctrl+Alt+K` | Temporal/spatial controls |
| Hold ↔ Auto Bézier method | `Ctrl+Alt+H` | Context-dependent selection |
| Linear ↔ Auto Bézier | `Ctrl+click` keyframe (layer bars) | |
| Linear ↔ Hold | `Ctrl+Alt+click` keyframe (layer bars) | Different modifier from above |
| Nudge selected keyframes ±1 frame | `Alt+Left/Right` | Keyframes selected, not layer |
| Nudge selected keyframes ±10 frames | `Alt+Shift+Left/Right` | |
| Select all keys on one property | Click property name | Timeline |
| Select all visible keyframes/properties | `Ctrl+Alt+A` | Only visible elements |
| Deselect keys/properties/groups | `Shift+F2` / `Ctrl+Alt+Shift+A` | |
| Reverse pasted keyframes | `Ctrl+Shift+V` | First copy with `Ctrl+C` |
| Proportionally rescale a group of keyframes | `Alt+drag` first/last selected keyframe | Stretches timing around opposite boundary |
| Reverse keyframe sequence in its current span | Keyframe Assistant → **Time-Reverse Keyframes** | Preserve original placement range |
| Rove a spatial keyframe across time | Right-click keyframe → **Rove Across Time** | For middle spatial keys; optimizes speed along motion path |
| Change selected interpolation to Hold | Right-click keyframe → **Toggle Hold Keyframe** | Useful for stepped animation |
| Allow subframe keys (graph mode) | Graph Editor options → **Allow Keyframes Between Frames** | Usually leave off for frame-accurate video timing |

**Interpolation theory — know three separate ideas:**

1. **Temporal interpolation** = how a numerical value progresses between two keyframes (Linear, Bézier, Continuous Bézier, Auto Bézier, Hold). **Easy Ease** adjusts temporal speed around a keyframe; it is not a separate kind of spatial path.
2. **Spatial interpolation** = the motion **trajectory** of position: straight versus curved (Bézier). A straight path can accelerate; a curved path can move at nearly constant speed.
3. **Graph choices** = **Value Graph** shows values changing, **Speed Graph** shows change per second. Choose the graph that exposes what you want to control. Ease handles can be linked or independent according to interpolation choices.

**Position path dots** are temporal samples along the motion path: tightly spaced dots indicate slow travel, wider-spaced dots indicate fast travel. They are **not** the keyframes themselves. Drag a motion-path vertex with selection/Pen tools to change trajectory; adjust timing in the Graph Editor.

**Roving explained:** when a motion has several spatial Position keyframes, enable **Rove Across Time** for intermediate spatial keys to distribute their timings for more consistent motion, while endpoints stay anchored. It is not a universal “evenly space all types of keys” button.

## 3.8 Text keyboard commands (when actively editing text)

| Result | Windows | Notes |
|---|---|---|
| Horizontal/vertical Type tool | `Ctrl+T` (cycle) | |
| New text layer | `Ctrl+Alt+Shift+T` | |
| Left / center / right align | `Ctrl+Shift+L/C/R` | Paragraph alignment |
| All Caps / Small Caps | `Ctrl+Shift+K` / `Ctrl+Alt+Shift+K` | Text selected |
| Superscript / Subscript | `Ctrl+Shift+=` / `Ctrl+Alt+Shift+=` | |
| Reset horizontal / vertical text scale to 100% | `Ctrl+Shift+X` / `Ctrl+Alt+Shift+X` | |
| Automatic leading | `Ctrl+Alt+Shift+A` | |
| Reset tracking to zero | `Ctrl+Shift+Q` | |
| Justify, last line left / right / force | `Ctrl+Shift+J` / `Ctrl+Alt+Shift+J` / `Ctrl+Shift+F` | |
| Decrease/increase font size by 2 | `Ctrl+Shift+,` / `Ctrl+Shift+.` | Text selected |
| Decrease/increase font size by 10 | `Ctrl+Alt+Shift+,` / `Ctrl+Alt+Shift+.` | |
| Increase/decrease leading by 2 | `Alt+Down/Up` | Between baselines |
| Leading by 10 | `Ctrl+Alt+Down/Up` | |
| Baseline shift by 2 / 10 | `Alt+Shift+Down/Up` / `Ctrl+Alt+Shift+Down/Up` | Vertical text offset |
| Kerning/tracking by 20 / 100 units | `Alt+Left/Right` / `Ctrl+Alt+Left/Right` | 1/1000 em units |
| Toggle paragraph composer | `Ctrl+Alt+Shift+T` | Context matters; same keys may mean new text layer elsewhere |
| Move by character / word | `Left/Right` / `Ctrl+Left/Right` | Text caret in a text box |
| Move by line / paragraph | `Up/Down` / `Ctrl+Up/Down` | |
| Extend selection by char / word | `Shift+Left/Right` / `Ctrl+Shift+Left/Right` | |
| Extend text selection by line | `Shift+Up/Down` | |
| Select to line start/end | `Shift+Home/End` | |
| Select to text frame beginning/end | `Ctrl+Shift+Home/End` | |
| Extend selection to click point | `Shift+click` | |
| Select word/line/paragraph/all | Double/triple/quadruple/quintuple click Type tool | Contextual |
| Finish entering text | `Num Enter` | Main `Enter` generally inserts line break |

**Text animator tip:** source text editing changes the string, while **Text → Animate → Position / Opacity / Tracking / Fill Color** creates animators. Use **Range Selector** or **Expression Selector** for letter/word/line animation. A text **mask path** can also serve as a **Path Options** route for letters; it is not the same as the text's transform Position.

## 3.9 Masks, shapes, painting and markers

| Task | Windows | Notes |
|---|---|---|
| New mask | `Ctrl+Shift+N` | On a valid selected layer |
| Select next/previous mask | **Alt + backtick** / **Alt+Shift+backtick** | Grave key above Tab |
| Select all vertices of mask | `Alt+click` mask | |
| Free Transform mask points | Double-click mask with Selection tool, or select mask then `Ctrl+T` | Exit `Esc` |
| Scale from center in Free Transform | `Ctrl+drag` | |
| Nudge vertices 1/10 pixel-at-view | `Arrow` / `Shift+Arrow` | Magnification-based |
| Toggle smooth ↔ corner mask point | `Ctrl+Alt+click` vertex | |
| Create/redraw Bézier handle | `Ctrl+Alt+drag` vertex | |
| Invert mask | `Ctrl+Shift+I` | |
| Mask Feather dialog | `Ctrl+Shift+F` | |
| Mask Shape dialog | `Ctrl+Shift+M` | |
| Change closed-mask mode during drawing/editing | `A` Add; `S` Subtract; `D` Darken; `F` Difference; `I` Intersect; `L` Lighten; `N` None | Hold the mouse button during mask drawing/modification; see below. |

### Shape grouping and paths

| Task | Windows | Notes |
|---|---|---|
| Group shapes / Ungroup shapes | `Ctrl+G` / `Ctrl+Shift+G` | Selected shape groups |
| Free-transform shape Path | Select Path then `Ctrl+T` | |

### Painting and Clone Stamp

| Task | Windows | Notes |
|---|---|---|
| Paint foreground/background swap | `X` | When paint tool active |
| Paint black/white default colors | `D` | In paint context; Timeline `D` scrolls to CTI |
| Paint foreground sample | `Alt+click` | In Layer viewer |
| Average 4×4 color sample | `Ctrl+Alt+click` | Paint tool active |
| Resize brush / change hardness | `Ctrl+drag` / begin `Ctrl+drag`, release Ctrl as dragging | |
| Join stroke to prior paint stroke | Hold `Shift` at start of stroke | |
| Clone Stamp source point | `Alt+click` | Set sample before painting |
| Paint opacity 10–90% | `Num 1–9` | Paint context, not layer select |
| Paint opacity 100% | `Num .` | |
| Paint flow 10–90% | `Shift+Num 1–9` | |
| Paint flow 100% | `Shift+Num .` | |
| Move playhead by paint-stroke duration | `Ctrl+Page Up/Down` or `1/2` (main keyboard) | Paint context |

### Layer and composition markers

| Task | Windows | Notes |
|---|---|---|
| Marker at CTI | `Num *` | Layer marker if layer selected; else composition marker |
| Marker with dialog | `Alt+Num *` | |
| Set numbered comp marker 0–9 | `Shift+0–9` (main keyboard) | |
| Jump to numbered comp marker | `0–9` (main keyboard) | Conflicts with number entry if text focused |
| Duration between two keys/markers in Info panel | `Alt+click` first and second | |
| Remove marker | `Ctrl+click` marker | |

### Point-tracker regions

| Task | Windows | Notes |
|---|---|---|
| Change mask/track feature region ±1/10 | `Arrow` / `Shift+Arrow` | Tracking context |
| Move feature/search region but not attach point ±1/10 | `Alt+Arrow` / `Alt+Shift+Arrow` | Tracking context |

**Mask-mode keys require a mouse gesture.** With a shape tool, press the mode key **before releasing the mouse** while drawing. With Pen, hold the mouse on the closing vertex, press the mode key, then release. When modifying a closed mask, hold the mouse button during the edit and press the key. Open paths have no mask mode. Pressing `S` afterward in Timeline instead reveals Scale. [Adobe: mask modes](https://helpx.adobe.com/after-effects/desktop/work-with-transparency-and-compositing/work-with-alpha-channels-and-masks/alpha-channels-masks-mattes.html).

**Shape creation refresher:** Creating **Layer → New → Shape Layer** creates an empty container. Expand **Contents**; each group can have its own shape Path, Fill, Stroke and Group Transform, plus the layer's overall Transform. Apply **Trim Paths**, **Repeater**, **Offset Paths**, **Pucker & Bloat**, **Wiggle Paths**, **Wiggle Transform**, **Zig Zag**, **Twist** as *shape operators*, not Effect Controls plugins. Convert parametric **Rectangle/Ellipse/Polystar Path → Bézier Path** only when you actually need editable vertices or morphing. Parametric paths preserve convenient live size/roundness controls.

## 3.10 3D cameras, lights, effects, footage and rendering

| Action | Windows | Note |
|---|---|---|
| New camera | `Ctrl+Alt+Shift+C` | Camera layer |
| New light | `Ctrl+Alt+Shift+L` | Light layer |
| Camera orbit/pan/dolly mode | `1` / `2` / `3` (main keyboard) | 3D-view camera controls |
| Universal / Position / Scale / Rotation gizmo | `V` / `4` / `5` / `6` (main keyboard) | 3D viewer context |
| Camera tool family | `C` repeated | |
| 3D view 1 / 2 / 3 | `F10` / `F11` / `F12` | Defaults Front / Custom View 1 / Active Camera on Windows |
| Return previous 3D view | `Esc` | In view context |
| Camera look at selected 3D layers | `Ctrl+Alt+Shift+\` | |
| With camera tool selected: look at selected | `F` | |
| With camera tool selected: look at all | `Ctrl+Shift+F` | |
| Turn Cast Shadows on/off on selected 3D layers | `Alt+Shift+C` | |
| Import file | `Ctrl+I` | |
| Import multiple | `Ctrl+Alt+I` | |
| Add selected project item(s) to active comp | `Ctrl+/` (main keyboard) | |
| Replace footage for selected layer(s) with selected project item | `Ctrl+Alt+/` (main keyboard) | |
| Swap one layer's footage but preserve setup | `Alt+drag` Project item onto layer | Extremely useful |
| Interpret Footage | `Ctrl+Alt+G` | Source FPS, alpha, color, fields |
| Remember footage interpretation | `Ctrl+Alt+C` | |
| Replace project footage item | `Ctrl+H` | |
| Reload project footage item | `Ctrl+Alt+L` | |
| Set footage proxy | `Ctrl+Alt+P` | Manage sources efficiently |
| Delete footage with no warning | `Ctrl+Backspace` | Dangerous; Project panel context |
| Apply effect by name quickly | `Ctrl+Enter` | **Quick Apply 26.2+** |
| Double-click effect in Effects & Presets | Double-click | Applies to selected layers |
| Apply last used effect | `Ctrl+Alt+Shift+E` | |
| Apply last used animation preset | `Ctrl+Alt+Shift+F` | |
| Remove all effects from selected layers | `Ctrl+Shift+E` | Deletes them |
| Add current comp to Render Queue | `Ctrl+Shift+/` | |
| Add current comp to AME | `Ctrl+Alt+M` | |
| Save frame | `Ctrl+Alt+S` | Add frame to Render Queue |
| Duplicate Render Queue output item | `Ctrl+Shift+D` in **Render Queue** | In Timeline it instead splits layer |

## 3.11 Guides, grids, channels and inspection

| Action | Windows | Notes |
|---|---|---|
| Show safe margins | `'` | Title/action safe zones |
| Show grid | `Ctrl+'` | Full grid |
| Show proportional grid | `Alt+'` | Large proportion reference |
| Show rulers | `Ctrl+R` | Drag rulers to create guides |
| Show guides | `Ctrl+;` | Semicolon |
| Snap to grid | `Ctrl+Shift+'` | Toggle |
| Snap to guides | `Ctrl+Shift+;` | Toggle |
| Lock/unlock guides | `Ctrl+Alt+Shift+;` | Keep guides safe |
| Hide/show mask paths, layer controls, bounding boxes | `Ctrl+Shift+H` | Doesn't alter render |
| Active Comp viewer options | `Ctrl+Alt+U` | Includes overlays |
| Show grayscale Red/Green/Blue/Alpha | `Alt+1/2/3/4` | Check matte contamination |
| Show tinted R/G/B channel | `Alt+Shift+1/2/3` | |
| Show Straight RGB | `Alt+Shift+4` | Useful for premultiplication checks |
| Alpha boundary in Layer panel | `Alt+5` | |
| Alpha overlay in Layer panel | `Alt+6` | |
| Refine Edge X-ray | `Alt+X` | Edge/matte review |

> [!important] How to discover a shortcut that isn't here
> AE has version-, OS-, extension-, and panel-specific commands. Open `Ctrl+Alt+'`, search the **exact menu command**, check the scope, and assign one if none exists. That's the authoritative way to handle **Time-Reverse Keyframes**, **Sequence Layers**, **Freeze Frame**, **Crop Comp to Selected Layer(s) Bounds**, **native null-controller commands**, and third-party commands. **Do not** assume a guessed key combo is a default.


## 3.12 Less frequent commands and contextual gestures

These complete the specialist areas of the reference. Learn them when the task arises rather than memorizing the whole table at once.

| Task and required context | Windows shortcut or gesture | What to expect |
|---|---|---|
| Extend an item selection in Project, Effect Controls or Render Queue | `Shift+Up/Down` | Adds adjacent items to the selection; Timeline layer selection uses `Ctrl+Shift+Up/Down`. |
| Change project calculation precision quickly | `Alt+click` the Project panel bit-depth indicator | Cycles project bpc; clicking without Alt opens Project Settings. |
| Reset preferences during application launch | Hold `Ctrl+Alt+Shift` while launching | Follow the reset prompt; use when diagnosing a preference problem, after preserving custom settings. |
| Activate a different view in a multi-view Comp panel | Middle-click that view | Changes active view without changing selected layers. |
| Return to the previously active related composition | `Shift+Esc` | Travels within the current nested-composition hierarchy. |
| Use the third configurable preview binding | `Shift+Num 0` | Has its own Preview-panel settings, alongside Space and Num 0. |
| Restore older RAM/standard preview configurations | `Alt+click` Reset in Preview | Changes preview bindings/settings; ordinary playback remains configurable. |
| Scrub video interactively | Drag CTI; `Alt+drag` according to Live Update setting | Preview updating depends on Live Update. Audio scrubbing uses `Ctrl+drag`. |
| Open footage itself for inspection | Double-click its Project-panel item | Opens the Footage viewer, separate from its layer instance. |
| Open a Timeline layer's source | `Alt+double-click` that layer | Footage sources open in Footage; a precomp layer opens in Layer rather than its source composition. |
| Stretch a layer to a new In/Out reference | `Ctrl+Shift+,` / `Ctrl+Alt+,` | Time-stretch boundary commands; source playback speed changes. Inspect the Time Stretch result. |
| Expand selected effects in Effect Controls | `Ctrl` + backtick | Same expansion principle as Timeline; click the intended panel first. |
| Erase only the latest paint stroke temporarily | Hold `Ctrl+Shift` with a paint tool | Temporary Eraser / Last Stroke Only behavior. |
| Reposition Clone Stamp overlay/sample relationship | `Alt+Shift+drag` using Clone Stamp | Edits Offset when aligned, Source Position when unaligned. |
| Recall one of five Clone Stamp presets | Main-keyboard `3`–`7` | Paint/Clone context; these digits have different meanings in camera or marker contexts. |
| Make a copy of a Clone Stamp preset | `Alt+click` its preset button in Paint | Duplicates settings for another paint setup. |
| Advance/rewind by the paint stroke duration | `Ctrl+Page Down/Up`, or main-keyboard `2/1` | Moves **time** by the configured Duration; it does not change Duration itself. |
| Text caret to start/end of current line | `Home` / `End` | Text editing only; Timeline uses these for time navigation. |
| Select all characters in one text layer | Double-click the text layer | Enters text editing with its text selected. |
| Vertical text: select adjacent characters | `Shift+Up/Down` | Vertical typography follows its writing direction. |
| Vertical text: select words | `Ctrl+Shift+Up/Down` | Word-sized text selection. |
| Vertical text: select adjacent lines | `Shift+Left/Right` | Extends selection across vertical text columns. |
| Vertical text: caret by character / line / word / paragraph | `Up/Down` / `Left/Right` / `Ctrl+Up/Down` / `Ctrl+Left/Right` | Direction differs from horizontal text. |
| Vertical text alignment: top / center / bottom | `Ctrl+Shift+L/C/R` | Same alignment chords as horizontal text, interpreted vertically. |
| Round a rectangle while drawing | `Up/Down` | Adjusts roundness; on polygon/star tools these keys adjust sides/points. |
| Set rectangle corners to sharp / maximum roundness while drawing | `Left/Right` | For polygons/stars, adjusts outer roundness instead. |
| Repeat a Render Queue item with the original output filename | `Ctrl+Shift+D` in Render Queue | Check destination before rendering; this duplicate keeps the same filename. |

### New guide gestures in 26.5

| During a guide drag | Windows modifier | Meaning |
|---|---|---|
| Change the measurement unit | Hold `Alt` | Switch between pixels and percent. |
| Change the anchor/reference behavior | Hold `Ctrl` | Toggle pinning; the guide stays visually in place as its reference changes. |

Right-click a guide → **Edit Guide** for exact position, unit, reference edge and color. A 100-pixel margin measured from the **right** stays 100 pixels from that edge when the comp widens. A **50%** guide remains proportional. Neither setting automatically moves your artwork. Use `Ctrl+Alt+Shift+;` to lock guides against dragging. [Adobe: guide controls](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/guide-creation-and-management.html).

### Proportional Scrubbing: numerical staggering

Select layers in the intended order, reveal the property, select the values to affect, then hold **`Ctrl+Alt` while dragging the property's hot text**. The first selected layer receives no change, the last receives the full change, and intermediate layers receive fractions. For three layers and a 120-pixel scrub, the added offsets are **0, 60, 120**. Existing differences are retained; this distributes a **change**, not necessarily equal final positions. For Scale, the last selected layer's Constrain Proportions setting governs the operation. **Quick Offset** distributes items in **time**; **Proportional Scrubbing** distributes property **values**. [Adobe: Proportional Scrubbing](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/proportional-scrubbing-in-timeline.html).

### Conflicts worth recognizing immediately

| Same keys | Meaning depends on context |
|---|---|
| `Ctrl+T` | Type tool; selected mask/shape Path can instead enter free transform. |
| `Ctrl+Shift+C` | Precompose layers; center-align selected text while editing. |
| `Ctrl+Shift+K` | Keyframe Velocity; All Caps during text editing. |
| `Ctrl+Shift+F` | Mask Feather; paragraph justification; camera framing with camera tool. |
| `Ctrl+Alt+C` | Copy with Property Links in an eligible edit context; Remember Interpretation on Project footage. |
| `Alt+Left/Right` | Nudge selected keyframes; adjust kerning when editing text; move tracker regions in tracking context. |
| `X` / `D` | Timeline selection scrolling / CTI scrolling; paint color swap / paint defaults. |
| Main `0` / `Num 0` | Numbered comp marker navigation / preview; numpad digits also select layers in the appropriate context. |
| `Ctrl+Alt+Shift+N` | Project folder or split viewer, according to focus. |

**Object Matte binding caveat:** Adobe's dedicated Object Matte page lists `Ctrl+W`, while its general shortcut reference also assigns `Ctrl+W` to closing a viewer. The documentation does not establish a dependable focus rule that resolves every case. Choose the Object Matte toolbar icon, or check/remap the command in the installed Keyboard Shortcuts editor. Treat the binding as documented but verify it locally. Do not assume the Layer panel makes Close impossible.

Source for default-key facts: [Adobe's keyboard reference](https://helpx.adobe.com/after-effects/desktop/get-started/keyboard-shortcuts/keyboard-shortcuts-reference.html). This guide includes its command families, common gestures, and separately documented 2026 additions. Plugin commands and custom presets require their own bindings.



## 3.13 Precision editing and frame capture

### Keyframes: protect the animation you already made

**Clicking an active stopwatch removes every keyframe on that property**, keeping its current value. Clicking again starts new animation; use Undo to recover the old keys. To remove one key, select its diamond and press `Delete`, or use the property's keyframe navigator. `Alt+Shift+property-key` also removes an existing key at CTI; it is a toggle. In Graph Editor, **`Ctrl+click` a key with Selection deletes it**, whereas Ctrl-click in layer-bar view changes interpolation.

An enabled stopwatch automatically records value edits at CTI. The separate **Enable Auto-keyframe** mode can turn on new stopwatches as you edit and is off by default. Keep it off until that behavior is intentional. [Adobe: keyframes and navigation](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/setting-selecting-deleting-keyframes.html).

### Graph Editor gestures

Enable **Show Transform Box** for a selected set of keys before using box gestures.

| In Graph Editor | Windows gesture | Use |
|---|---|---|
| Select every key on a property's graph | `Alt+click` a segment | Selects the property’s keys. |
| Constrain box movement | `Shift+drag` inside box | Time-only or value-only movement. |
| Scale around the box anchor | `Ctrl+drag` a box handle | Expands/contracts around its pivot. |
| Adjust only one corner | `Alt+drag` a corner handle | Changes that corner. |
| Taper values across the selection | `Ctrl+Alt+drag` a handle | Changes an animation's amplitude progressively. |
| Raise/lower one side | `Ctrl+Alt+Shift+drag` a handle | Tilts the selected value pattern. |

A value-graph box can change **time and value**; a speed-graph box moves keys in **time**. In layer-bar view, begin dragging a key, then hold `Shift` to snap it to CTI. Pasted keys place the earliest copied key at CTI. [Adobe: keyframe editing](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/editing-moving-copying-keyframes.html).

### Move footage inside its existing time slot

After trimming moving footage, drag its pale **slip-edit bar**, or use **Y** to drag it in Timeline. The layer's visible In/Out points stay fixed while different source frames play. Selected keyframes can move with a slip; unselected keys remain. Use `Shift+F2` first when you intend to deselect keys while retaining the layer. By comparison, dragging the ordinary duration bar moves the layer and its keys. [Adobe: slip editing](https://helpx.adobe.com/after-effects/desktop/work-with-layers/select-and-arrange-layers/selecting-arranging-layers.html).

### Snapshot, clipboard copy and rendered still are different

| Need | Use | Result |
|---|---|---|
| Compare two looks inside AE | `Shift+F5…F8`, then `F5…F8` | Viewer snapshots for comparison. |
| Paste a visual reference into another app | `Ctrl+Alt+Shift+F5` | Current rendered viewer frame on the Windows clipboard. |
| Save a still using output settings | `Ctrl+Alt+S` | Queues the current frame for rendering. |

For clipboard capture, activate **Composition, Layer or Footage**, wait for rendering, then copy. **Transparency Grid on** preserves transparency in destinations supporting it; **off** composites over the comp background. The copied image reflects display color management/OCIO viewing transforms. Windows receives PNG plus a compatibility bitmap; the destination decides which it uses. Use a configured still render when you need controlled production color and channels. [Adobe: clipboard frame capture](https://helpx.adobe.com/after-effects/desktop/view-and-preview/copy-frames/copy-frame-to-clipboard.html).

### Viewer gestures that save repeated clicks

Double-click **Zoom** for 100% on Windows. Hold `Shift` with the Hand tool to pan faster. Hold `Ctrl` while selecting a magnification from the viewer menu to apply it to all views in that Composition panel. Use **View → Assign Shortcut** to manage the three saved 3D view slots; check existing workspace bindings before reusing function keys. Magnifying a viewer changes inspection size; scaling a layer changes the image being composited. [Adobe: viewer controls](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/modifying-using-views.html).


## 3.14 Search and mouse alternatives

| Task | Windows gesture | Detail |
|---|---|---|
| Search for several properties at once | `Ctrl+F`, then `position, scale` | A comma is OR; search finds either term. Clear the filter afterward. |
| Center anchor in visible content using the toolbar | `Ctrl+double-click` Pan Behind icon | Equivalent to `Ctrl+Alt+Home`; preserves visual placement. |
| Reset anchor to its default location | Double-click Pan Behind icon | Reset differs from centering in visible content. |
| Reset anchor and center the layer in the comp | `Alt+double-click` Pan Behind icon | This can visibly move the layer. |
| Give a marker a duration | `Alt+drag` the marker to the right | Afterward, drag its out-point handle to change duration. |
| Reset work area to full composition | Double-click the **center** of its bar | This is the Work Area bar, not the Time Navigator. |
| Zoom time to individual frames | Double-click Time Navigator | Toggles back to composition duration on a second double-click. |
| Toggle full-duration time view | `Shift+double-click` Time Navigator | Restores the previous navigator extent on another toggle. |

Search syntax: [Adobe: interface search](https://helpx.adobe.com/after-effects/desktop/get-started/get-familiar-with-the-interface/general-user-interface-items.html). Anchor gestures: [Adobe: layer properties](https://helpx.adobe.com/after-effects/desktop/work-with-layers/layer-properties/layer-properties.html). Marker duration: [Adobe: markers](https://helpx.adobe.com/after-effects/desktop/work-with-layers/work-with-layer-and-composition-markers/layer-markers-composition-markers.html). Work-area/navigator gestures: [Adobe: previewing](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/previewing.html).

---

# 04 — The Core Workflows

## 4.1 Layers, precompositions, render order and parenting

**Layers render from the bottom up**, subject to special blending/compositing, 3D and renderer rules. The top layer generally obscures lower layers wherever it is opaque. **Effects run in the order listed in Effect Controls**: `Find Edges → Tint → Glow` is often materially different from `Glow → Find Edges → Tint`. Transforms, masks, styles, 3D settings and collapsed precomps can change the outcome; don't treat a layer as a miniature Photoshop file with a fixed universal rendering order.

**Good organization:** `BG`, `FOOTAGE`, `SUBJECT_MATTE`, `TEXT_MAIN`, `CONTROLS`, `FX_GLOBAL`, `OUTPUT`; use color labels and clear precomp names. Switch from **Layer Name** to **Source Name** in Timeline's column header when you need to identify reused footage. Right-click the Timeline column headers → **Columns** to choose specific visible columns rather than only toggling whole groups. Shy layers can disappear from timeline display without disappearing from render.

**Precompose:** select layers → `Ctrl+Shift+C` → name the nested composition. **Move all attributes** usually keeps effects and keys with the layer inside the new comp; **Leave all attributes** (when available for the selection) leaves outer-level effects/transforms in place. Inspect post-precomp size, frame rate, duration, color and motion blur. If a glow clips at precomp bounds, expand source canvas, precompose carefully, or use **Grow Bounds before the clipping effect** as appropriate.

**Parenting:** Pick-whip the child's **Parent & Link** to the parent/null. Normal parenting maintains the child's apparent current world transform. **Shift-parenting** can snap the child toward the parent's transform, so use only if desired; inspect 3D transforms. Parenting adds transforms; **Copy with Property Links** instead adds an **expression** to a specific property, which is not a substitute for full parenting in all situations.

**Null controller recipe:** `Ctrl+Alt+Shift+Y` → rename `CTRL — Motion` → parent several layers to it → animate the null with `P`, `S`, or `R`. For complex group movement, a null is usually easier to edit than repeatedly animating each layer; it does not render by itself.

### Preserve Underlying Transparency

The **Preserve Underlying Transparency** switch makes a layer's pixels respect the **composite alpha from layers beneath**, useful for restricting highlights, glows or textures to already visible artwork. It differs from a selected **Track Matte source**: it uses the underlying composited result, rather than picking one specific matte. It obeys lower-layer stack relationships. Test interactions with blending modes, nesting and 3D.

## 4.2 Track mattes: alpha, luma and inversion (modern AE)

```text
MATTE SOURCE:    PERSON CUTOUT       ← alpha silhouette of the person
FILL LAYER:      COLORFUL TEXTURE     ← this layer receives Track Matte = PERSON
VISIBLE RESULT:  TEXTURE ONLY WHERE THE PERSON'S ALPHA EXISTS
```

1. Put the **content you want revealed** on its own layer (e.g. texture).
2. Have a **matte-source layer** (e.g. roto'd person, shape, text, gradient).
3. In the **content layer's Track Matte controls** (show Modes with `F4`), use the matte **pick-whip** or layer menu to select the source.
4. Choose **Alpha Matte** (use source transparency) or **Luma Matte** (use source brightness).
5. Toggle **Invert** to reveal the complement of the matte.
6. In modern AE, **the matte source can be anywhere in the stack**, one matte can drive multiple content layers, and the matte source's **eye/visibility can be independently controlled**. There is no general rule that you must put the matte directly above the fill or turn its eyeball on again.

**Alpha**: white, black, red and blue opaque pixels all contribute alpha if opaque. **Luma**: bright pixels reveal more; dark pixels hide more; the degree of matte can vary continuously. **Invert Alpha** makes transparent holes where the matte was opaque. **Invert Luma** reverses bright/dark reveal. For a rectangle reveal, select `Text` as content and `Rectangle Shape` as source—not the other way around.

**Common problem:** Matte disappears or content is missing → confirm selected **matte source**, alpha/luma mode, inversion, content's alpha, effects on matte, collapsed nested comps and source visibility. A matte source's effect/render output may differ from the source you think you're sampling.

**Set Matte effect:** another way to extract a layer's alpha/luma into the current effect pipeline. Use if you need effect-order control or a matte not convenient through timeline track matte. It isn't unconditionally “better”; the native Track Matte is often simpler and easier to maintain.

## 4.3 Masks, roto, feather and animated outlines

A mask changes visibility or effect area on the layer; common modes are **Add, Subtract, Intersect, Difference, None**. To draw one, select an image/footage/solid, choose Pen `G` or shape `Q`, and verify **Create Mask** mode. `M` = Mask Path, `MM` = all masks, `F` = Mask Feather, `TT` = mask opacity. Masks can be keyframed.

**Per-vertex feather:** Select **Mask Feather tool** from the Pen group, add/move feather points and adjust each separately. Unlike global Mask Feather, this creates a spatially varying soft edge. Keep mask vertices and feather points logically distinct.

**Mask path editing:** Click the **Mask Path** property to expose handles; convert a corner to Bézier by manipulating tangent handles (`Ctrl+Alt+drag` on a point); right-click path/vertex for **Set First Vertex**, open/close path and RotoBézier options. Path morphing works best when path direction, first vertex and vertex counts/order correspond.

**Auto-trace outline method:** Duplicate an isolated/roto'd subject → **Layer → Auto-trace** → choose **Alpha**, current frame or work area depending on moving subject → apply **Generate → Stroke** to traced masks → animate Stroke End and apply Glow. On complex footage Auto-trace may create **many** animated masks, making this expensive; test a simplified matte first. A separate shape-stroke outline or **Find Edges** may be easier.

**Shape-stroke handwriting alternative:** Draw a shape **path** using Pen; turn Fill off, give it a Stroke; **Contents → Add → Trim Paths**; animate **End** from 0 to 100; optionally add glow or roughened/tapered edges. For predictable line reveals, this is often cleaner than a paint stroke.

## 4.4 Rotoscoping: Object Matte, Roto Brush and mask tracking

### Pick the right tool

| Need | Use | Why |
|---|---|---|
| Select/track a clear person or object across frames | **Object Matte (26.2+)** | AI-powered single-click/marquee subject isolation |
| Manually establish inside/outside of an irregular foreground | **Quick Selection / Roto Brush** | Brush-based correction, iterative propagation |
| Fine hair, fur and wispy edges | **Refine Edge** | Hair/fine-edge processing |
| Follow the geometry of a hand-drawn mask | **Mask Tracker** | Track mask vertices over time; substantially improved in 26.3 |
| Planar surfaces/occlusion | **Mocha AE** | Planar tracking/masks and surface replacement |
| Whole-screen camera motion | **3D Camera Tracker** | 3D solve of a moving camera |

### Object Matte in 26.5

1. Double-click footage in Timeline → **Layer panel**.
2. Activate the **Object Matte tool family** from Tools. Select it from the toolbar; Adobe also lists `Ctrl+W`, but that conflicts with Close and should be checked in your installed shortcut editor. Use one-click subject pick or rectangle/lasso.
3. Refine with **Quick Selection**, **Selection Brush** (add/subtract), and **Refine Edge** for hair. Review in a clear foreground/background comparison.
4. Propagate forward/backward only across the span you need; inspect frames with occlusions or subject turns.
5. Refine bad frames, then **Freeze** when confident. In **26.5, propagated frames can be cached to disk between sessions**, helping long clips and avoiding needless recomputation; cache isn't a substitute for checking bad segments.
6. Return to Comp → add corrections, masks, effects and mattes to isolated subject.

**Traditional Roto Brush:** Paint foreground (green selection), subtract undesired areas with the appropriate subtract modifier, work in the **Layer panel**, review propagation boundaries, set a sensible base frame, then Freeze. Full-resolution viewing is often beneficial when judging fine mattes, but does not imply you must render the entire project at Full preview resolution all the time. Refine matte edges using feather, shift-edge/reduce chatter and motion blur as needed.

### Point tracker and face tracker

**Point track:** Select footage → **Window → Tracker** → **Track Motion** → place the **inner feature** box over a distinctive, consistent detail, with the **outer search area** large enough for expected movement → choose Position or Position+Rotation+Scale → select **Edit Target**, ideally a null → analyze forward/backward → inspect drift → **Apply**. Parent artwork to that null. Tracking the actual subject is not the same as tracking background camera movement.

**Face Track:** Draw an elliptical **mask around the face** on footage → Tracker → Face Tracking (Detailed Features) → analyze → examine output points (eyes, nose, mouth etc.) → link a selected point/effect control with a pick-whip. It can fail on occlusion, profile views, motion blur or low resolution; manual adjustments are normal.

**Liquify following a face:** Track a stable feature and apply tracking motion to a null/point. Link **Liquify → Distortion Mesh Offset** (where exposed) to tracked motion, or precompose/stabilize first. Link tracking **before** positioning detailed deformation so your warp doesn't jump when the coordinate offset changes. Align reference coordinates, not just raw x/y numbers.

**Stabilize Motion:** A point-track can move the entire footage to keep a chosen feature still; background appears to move instead. **Warp Stabilizer** estimates broader camera motion and may warp/crop edges. These are different tools.

## 4.5 Keying green/blue screen properly

```text
SOURCE → [Garbage matte if needed] → Keylight → Key Cleaner (as needed)
      → Advanced Spill Suppressor → [Edge color match / grain / light wrap]
      → Composite on new background
```

1. Use the best available source: even lighting, shutter speed that doesn't create extreme motion blur, enough distance from screen, and low compression.
2. Garbage-matte large unneeded screen areas if useful; do **not** depend on obsolete **Color Key** as a required first step.
3. Apply **Keylight (1.2)**. Sample a representative green/blue patch; zoom and view **Screen Matte**.
4. In Screen Matte, adjust **Clip Black** so background is black and **Clip White** so subject core is white. Preserve gray translucent pixels at semi-transparent edges (hair, smoke). A fixed “Clip Black = 90” is **not** a general recipe—it often destroys detail.
5. Inspect **Status** and **Intermediate Result**. These modes are not interchangeable with **Final Result**: Intermediate is useful when using the separate **Key Cleaner + Advanced Spill Suppressor** chain, avoiding unhelpful built-in Keylight color handling for that workflow.
6. Use **Screen Shrink/Grow** sparingly. Large values can amputate hair and fine edges or create halos.
7. Use Key Cleaner / Refine Soft or Hard Matte when appropriate, then **Advanced Spill Suppressor** to remove unwanted green edge light. Adjust Standard/Ultra based on footage.
8. Judge composite at 100% on a contrasting background, inspect hair and motion edges over time, color-match subject to scene, and restore appropriate grain/shadows.

**Two-Keylight technique** can be helpful when one keyed result isolates the subject core and another isolates fine details, but **stacking multiple Keylight effects blindly on the same footage** is not a universal professional method. Use separate key/matte passes or precomps so one edge treatment does not corrupt the other. Don't solve a brightness mismatch by reducing the entire subject opacity indiscriminately—it makes the person transparent.

## 4.6 Shapes, paths, motion paths and morphing

**Add a shape:** With nothing selected, use `Q` tool and draw, or `Layer → New → Shape Layer` then draw into **Contents**. Multiple groups can share a layer. Each has its own transform, stroke and fill; the layer has global transforms. Use **Contents → Add** for Trim Paths, Repeater, Offset Paths, Merge Paths, Wiggle Paths, Zig Zag, Pucker & Bloat, Twist, etc.

**Morph square to circle:** (1) Make Rectangle and Ellipse **Bézier paths** (right-click their parametric path → Convert to Bézier Path), (2) ensure their first vertices and direction correspond, (3) put a Path keyframe for the square at t0, (4) copy the circle's **Path** value or keyframe to the square's Path at t1, (5) adjust vertices/tangents for smooth correspondence. AE interpolates the geometric points; “morphing” doesn't automatically find the semantic best match between arbitrary silhouettes.

**Move arrow along path:** Draw a mask or path → copy **Path** keyframe/value → select arrow's **Position** at appropriate time → paste to generate motion path. **Layer → Transform → Auto-Orient → Orient Along Path** (`Ctrl+Alt+O`) points the arrow tangent to its Position trajectory. For an animated editable path, **Layer → Create → Null Tracing Along Path** can drive a null with animated **Progress**; parent arrow to null or link positions and adjust orientation.

**Native path controllers:** select the actual Path property and use **Layer → Create** or **Animation → Keyframe Assistant**. **Null Controllers for Path Points** makes nulls drive vertices; **Nulls Following Path Points** makes nulls follow vertices; **Null Tracing Along Path** creates a traveling null with progress controls. The older **Create Nulls From Paths** script panel uses the names **Points Follow Nulls**, **Nulls Follow Points** and **Trace Path** for the corresponding workflows. See §4.18 for positional controllers.

**Gradient shape/arrow:** Add shape fill → gradient fill or gradient stroke → adjust gradient endpoint handles in the viewer. `Alt+click` toolbar **Fill/Stroke** cycles supported modes in appropriate contexts. For shape layers, **stroke width, taper, wave, dashes and gradient** live under shape Contents; do not confuse with **Generate → Stroke** effect.

**Repeater:** Draw one graphic → **Contents → Add → Repeater** → adjust Copies/Offset and Repeater Transform (Position, Rotation, Scale, Opacity). The Transform in the Repeater works per copy, while a group's Transform works on the whole group.

## 4.7 Paint, write-on and animated handwriting

**Paint-based “write-on”:** (1) Create a transparent-friendly **solid** or footage layer, (2) **double-click the layer** to open the **Layer panel** (paint cannot be drawn directly as identical strokes in the Comp panel), (3) select `Ctrl+B` until **Brush**, (4) Window → Paint/Brushes: set **Duration = Write On**, an appropriate RGBA mode, (5) place CTI at desired start and write with the brush at your chosen rate, (6) in layer's **Paint** effect, enable **Paint on Transparent** if you want only brush strokes, (7) inspect duration/keyframes using `U`/`UU`. Each brush stroke has temporal properties you can alter.

**When to use what:** Paint is suited to an **actual stroke recorded live**; **Trim Paths** is better for perfectly reproducible stroke reveals; **Generate → Write-on** or **Stroke** effect can produce historical/legacy-style animation; masks can reveal text or footage with cleaner edges. Don't require a solid for *all* effects—effects operate on many kinds of valid layers, but generators may need a sufficiently sized source or solid to render across the comp.

## 4.8 3D, camera, lighting, DOF and parallax

**3D basics:** Enable the 3D switch on supported layers → move on X/Y/Z; use view gizmos (V, 4, 5, 6), multi-view, cameras and lights. **Classic 3D** handles 3D layer positions but not native extrusion of arbitrary flat shape geometry. **Advanced 3D** is the modern choice for many 3D models, parametric meshes, geometry, Substance materials, shadows and in-engine depth of field. **Cinema 4D Renderer** has its own capabilities and limitations; don't assume every 2D effect behaves normally on an actual 3D model in every renderer.

**Camera focal length:** Shorter focal length (e.g. 18–24 mm) = wider field of view; longer (e.g. 85–200 mm) = tighter field of view **given the same sensor/backplate assumptions**. To change perspective, move camera position, not just Zoom. Depth of Field depends on focus distance, aperture and scene geometry.

**3D parallax (simple):** Separate subject, foreground and background in Photoshop or with roto; fill holes; place layers at varying Z; adjust Scale to maintain desired framing; animate camera slowly; inspect gaps/edges. Better than trying to fake dramatic camera motion from an unprepared flat photo.

**Depth map (advanced):** A grayscale **depth estimate** encodes relative distance, but **white=near** versus **white=far** depends on the generating tool and downstream effect settings. No universal “always invert this map” rule. Use the map as a **Displacement Map**, **Camera Lens Blur blur map**, **Gradient Wipe transition layer**, or 3D displacement texture. Pre-blur/dilate the map to avoid sharp stair-step borders. For depth-based camera movement on a single image, displacement alone cannot reveal unseen occluded background, so keep motion subtle or paint/fill missing areas.

**Advanced 3D 26.3+ depth of field:** Supports geometry and materials rendered by the Advanced 3D engine. This is distinct from the 2D **Camera Lens Blur effect** that uses a grayscale blur-map layer. The built-in 3D depth pass can also support other compositing workflows.

## 4.9 Time: remapping, posterize, freeze, loop and echo

- **Freeze Frame:** CTI on desired frame → **Layer → Time → Freeze Frame**. A still image of that chosen point persists through the layer span; set duration with trim/extend.
- **Time-Reverse Layer:** select layer → `Ctrl+Alt+R` (or Layer → Time → Time-Reverse Layer). This reverses actual footage/precomp playback, not necessarily every outside-world dependency.
- **Time Remapping:** select source layer → `Ctrl+Alt+T`, then edit source-time keyframes to slow, speed up, reverse or freeze. Extend layer duration carefully and verify end-of-source behavior. **Footage is supported**; not just precomps.
- **Loop source footage:** in **Project panel**, right-click → **Interpret Footage → Main** → set **Loop** under Other Options where supported; loop count must cover desired output. For arbitrary animation in properties, add `loopOut("cycle")` to an eligible property expression. `loopOut` affects **keyframed property values**, not the entire footage stream unless on an appropriate time-remap property.
- **Posterize Time:** Effect → Time → **Posterize Time** displays fewer distinct temporal updates per second; it does **not** change the composition's true delivery frame rate. For expressions, `posterizeTime(12);` reduces how often the *expression* value changes, not every unrelated layer.
- **Echo:** repeats earlier or later frames with adjustable blending/decay to create trails. Check source duration, work area, echo times and alpha accumulation.
- **Pixel Motion Blur:** synthesizes blur from estimated optical motion; can be expensive/artifact-prone. Native **Motion Blur switch** + comp shutter is preferable for ordinary transform animation.

## 4.10 Motion blur that actually works

Enable **motion blur switch on moving layer** and the **global Motion Blur composition switch**. Open `Ctrl+K` → **Advanced** → Motion Blur, adjust **Shutter Angle** for smear amount and **Shutter Phase** for timing. Larger angles often produce more blur, at the cost of time/clarity; this is not a universal “crank it all the way” setting. Physical footage motion blur cannot always be faithfully recreated with this switch; consider **Pixel Motion Blur** or optical-flow-aware options where appropriate.

## 4.11 Audio, waveforms and sound-driven graphics

**Show waveform:** Select audio layer → `LL` to reveal visible waveform. `L` shows audio levels in dB. For audio-reactive values, right-click layer → **Keyframe Assistant → Convert Audio to Keyframes**. AE creates an **Audio Amplitude** null-like control layer with Left Channel, Right Channel and Both Channels slider keyframes. `UU` helps reveal results. Link a visual Scale/Glow/Opacity property with the expression pick-whip and map values appropriately (`linear`, `ease`, clamping) rather than pasting raw audio values directly.

**Audio Spectrum / Audio Waveform effects:** Apply to a visible comp-sized solid or another appropriate layer; choose the source in **Audio Layer**; adjust Start/End points or set a mask path as source. Set the driving mask **Mode=None** if you want the path to guide the visualizer without restricting the layer. One effect visualizes frequency distribution; the other draws a waveform-like trace. Multiple effects on the same layer can have independently styled frequency bands.

**Audio format:** MP3 audio is supported in many workflows, but lossy compression and variable/timestamp handling can complicate demanding projects. For consistent results use 48 kHz **WAV/AIFF PCM** when practical; there is no universal ban on MP3. 2026 adds native **Compressor**, **Gate** and **Distortion** effects, useful for lightweight sound cleanup/design, though a dedicated DAW or NLE still offers better mixing facilities.

## 4.12 Green-screen highlights, outlines, glows and overlays

- **Glowing contour:** duplicate footage → Find Edges → invert/tint/levels as appropriate → Glow → blending mode Screen/Add on original. High contrast and limited threshold help prevent glow on every detail. **CC Composite** can composite current processed layer against its original input inside the same effect stack, reducing duplicated layers in some situations, but it isn't identical to a free-standing layer stack in all cases.
- **Highlight isolation:** duplicate layer → Extract/Levels/Curves to isolate highlights → Glow → blend Screen/Add; optionally add chromatic aberration and grain. Subtle is usually more convincing.
- **Expand effect bounds:** place **Grow Bounds** before blur/glow-like effects on layers where results clip at source bounds. If grow bounds doesn't help, enlarge/precompose the canvas instead.
- **Luma/matte reveal:** use textured Fractal Noise or animated gradient as a **Luma Track Matte**; or a clean depth map with **Gradient Wipe** on entering layer. A depth map isn't something AE inherently understands geometrically in Gradient Wipe—it reads grayscale brightness.

## 4.13 360°/VR workflows

Import equirectangular 360° footage/image in a suitable comp. Use **VR Sphere to Plane** to create an ordinary rectilinear view from 360 content, or **VR Plane to Sphere** to return edits into an equirectangular panorama. The **VR Comp Editor** workflow automates many setups. **CC Environment** wraps images into an environment for certain compositing/lighting workflows; it is **not restricted to stills** while VR Sphere to Plane is “for videos.” For 360 seams use **VR-aware** Blur, Glow, Sharpen, Chromatic Aberration, Denoise, Fractal Noise, Rotate Sphere, etc. Normal planar distortions may break seam continuity.

## 4.14 Rendering, formats, color and final delivery

| Deliverable | Good starting approach | Caution |
|---|---|---|
| Online review / ordinary web video | **H.264 (.mp4)** from AE Render Queue or Media Encoder | AE has supported **direct H.264 render-queue export** for years—no mandatory 2-pass ProRes route |
| Editing master / intermediates | **ProRes 422 HQ**, **DNxHR HQX** or production mezzanine according to pipeline | Big files but efficient for editing/recompression |
| Transparency / alpha master | **ProRes 4444** (with proper alpha settings), **PNG sequence**, **EXR** | Check RGB+Alpha, straight/premultiplied, codec/container support |
| High dynamic range / high-end comp | **EXR 16-bit half/32-bit float**, OCIO/ACES workflow | Explicit color-space, transfer, display transforms and metadata |
| Stills with alpha | **PNG**, TIFF, EXR as needed | Check bit depth and alpha interpretation |
| Frame-accurate VFX pipeline | Image sequence + separate WAV / consistent metadata | Better restart/recovery at cost of storage |

**Footage codecs:** MP4 is a **container**; not itself a codec. H.264/H.265 long-GOP media is often heavier to seek/decode than ProRes/DNxHR; consider transcoding only when playback, codec support, variable-frame-rate audio sync, heavy scrubbing or stability justify it. AE can use MP4, and AE can render H.264. Choose a suitable **intermediate** when needed; don't transcode every file blindly and waste disk/generation quality.

**Color workflow essentials:** `8 bpc` = limited integer calculation precision; `16 bpc` = higher precision; `32 bpc` = floating-point processing, suitable for HDR/overbright linear workflows when supported. Input *encoding* and AE *processing* precision are distinct. Respect embedded profiles, linearized compositing if the project requires it, and avoid applying incompatible LUTs twice. AE 26.5 adds **ACES 2.0 configurations through OpenColorIO 2.5.1**.

**Project hygiene:** collect files for handoff; ensure linked footage, fonts, plugins, expressions and color configs are available; use incremental versioning; check renders visually at 100% and listen to audio in the final file. For critical shots, export a short representative work-area test first.


## 4.15 Remove an unwanted object with Content-Aware Fill

1. Duplicate or preserve your original shot. Draw a mask around the unwanted object and set it to **Subtract**, making a transparent hole.
2. Track/animate that mask so the hole follows the object. Include enough room for its edges or shadow when appropriate.
3. Open **Window → Content-Aware Fill**. Choose the work area/range and a suitable method: **Object** for moving objects, **Surface** for relatively flat surfaces, or **Edge Blend** for simpler low-detail filling.
4. Generate a Fill Layer. Review the moving result for repeated textures and temporal smearing.
5. For difficult frames, create and paint a reference frame, then regenerate. A clean reference and a smaller analyzed range can improve results.

The generated fill is a separate layer. Keep source, mask and reference assets organized; inspect the fill at normal speed as well as frame by frame. [Adobe: Content-Aware Fill workflow](https://helpx.adobe.com/after-effects/desktop/remove-objects-from-your-videos/content-aware-fill.html).

## 4.16 Package a reusable template

Create a master composition and expose its intended controls in **Essential Graphics**: text, colors, sliders and suitable media replacements. Test each control and font before exporting a **Motion Graphics Template (`.mogrt`)** for Premiere. Expose only the controls an editor needs; check expression dependencies and plugin availability on the destination machine. A `.mogrt`, an `.ffx` preset and an `.aep` project serve different purposes. [Adobe: Motion Graphics templates](https://helpx.adobe.com/after-effects/desktop/motion-graphics/work-with-motion-graphics-templates/creating-motion-graphics-templates.html).

## 4.17 Understand effect order by building one test

On a small solid, draw an Add mask and apply a large Glow. On an ordinary raster layer, the masked result feeds the effects, so glow can extend beyond the mask. To crop the **finished** glow, precompose that processed layer and mask the resulting precomp, or use a track matte on the finished result. An effect mask instead restricts a particular effect's contribution.

The useful starting order for an ordinary raster layer is **source → masks → effects → transforms**, followed by compositing operations. Shape/vector layers, continuously rasterized layers, layer styles and collapsed precomps introduce exceptions. Precomposing creates a new processing boundary; it does not make rendering free. [Adobe: nesting and render order](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/precomposing-and-nesting/precomposing-nesting-pre-rendering.html).



## 4.18 Native null controllers for points and paths

For a gradient endpoint, effect point, Anchor Point or layer Position, select the **property**, then **Layer → Create → Null Controllers for Positional Points**. Selecting only a layer targets Position. Animate the generated null rather than repeatedly finding a tiny effect handle. Use the JavaScript expression engine and distinct layer names. A Layer Control identifies the controller; separating Position dimensions afterward requires relinking.

For a selected Path, the native commands are:

| Command | Direction of control |
|---|---|
| **Null Controllers for Path Points** | Nulls move the vertices. |
| **Nulls Following Path Points** | Vertices move the nulls. |
| **Null Tracing Along Path** | One null travels along the curve. |

These are available through **Layer → Create** and **Animation → Keyframe Assistant**. Shape-group skew/nonuniform scale can offset linked points. The old script panel remains useful for older tutorials. [Adobe: native null controllers](https://helpx.adobe.com/after-effects/desktop/drawing-painting-and-paths/null-controllers/create-nulls-for-positional-properties-and-paths.html).

## 4.19 Text animators: animate a selection of characters

1. Create text and expand **Text → Animate → Opacity**. Set the animator's Opacity to 0.
2. Expand its **Range Selector**. Animate **Start** from 0% to 100% to move characters out of the invisible selected range.
3. Under Advanced, choose **Based On** Characters, Words or Lines. Adjust selector Shape, Smoothness and easing to control the transition between selected and unselected characters.
4. Add Position to the same animator for coordinated movement, or make a second animator for an independently selected action.

An animator supplies property changes; a selector decides **where and how strongly** they apply. Layer Transform moves the entire text layer. Source Text keyframes change the string. Selectors and layer transforms solve different tasks. [Adobe: text animation](https://helpx.adobe.com/after-effects/desktop/animating-text/text-animation/animating-text.html).

### Animate variable-font axes

Choose a font with actual variable axes. Under **Text → Animate → Variable Font Axes**, select an available axis such as Weight or Width, then keyframe that animator's axis value. Use its Range Selector for per-character timing. The Properties panel also exposes axis controls and font filtering.

Keep values inside the installed font's supported range; a static family with several weights is not necessarily variable. Inspect **Text → More Options → Variable Font Spacing** when glyph-width changes alter spacing. Test multiline reflow and verify the same font file on the render machine. [Adobe: variable-font controls](https://helpx.adobe.com/after-effects/desktop/variable-font-axes/work-with-variable-font-axes/working-with-variable-font-axes.html).


### Typography clipboard choices

| Intention | Command while editing text |
|---|---|
| Bring words and their original styling | **Paste** |
| Bring words but retain destination styling | **Edit → Paste Text and Match Formatting** |
| Transfer styling without replacing the words | **Edit → Paste Text Formatting Only** |

For formatting only, copy highlighted source characters, then select destination characters or the whole destination text layer. To resize a paragraph text box, drag its handles with Type active; `Shift` preserves its proportions and `Ctrl` scales around its center. Changing the box changes text layout; changing layer Scale changes the rendered glyph size. [Adobe: text editing and pasting](https://helpx.adobe.com/after-effects/desktop/add-text/create-and-edit-text-layers/creating-editing-text-layers.html).

## 4.20 SVG and Illustrator: choose editable geometry or source footage

Use **File → Import → File** to import SVG as footage or a composition according to the dialog. For clipboard transfer from Illustrator, enable **Edit → Preferences → Clipboard Handling → Include SVG Code**, copy artwork, then `Ctrl+V` in AE. With a comp selected, it becomes native shape content in that comp; without one, AE can create a comp for it.

Convert SVG text to outlines before clipboard transfer when needed; SVG text is not supported by this paste workflow. Inspect gradients, strokes, transparency, group hierarchy and path operators. Editable Illustrator-text conversion is separately labeled Beta; shape conversion does not imply stable editable text. [Adobe: SVG import and paste](https://helpx.adobe.com/after-effects/desktop/import-files/import-svg-files/import-svg-files.html).

## 4.21 Puppet: deform artwork instead of moving a rigid card

Use transparent artwork with a clean silhouette. Select a Puppet tool (`Ctrl+P` cycles), add pins at stable joints, and move CTI before changing a pin's position. Start with few pins; excess pins can produce folds and hard transitions. Inspect the mesh and its Expansion/Density when edges tear or separate parts join unexpectedly.

Position pins move points; Advanced/Bend controls add local rotation/scale behavior. Starch controls stiffness, and Overlap influences which region appears in front. Reveal the result with `PP`. With an appropriate Puppet pin selected, **`Ctrl+drag` records a Puppet Sketch performance**; `Shift+click` adds pins to the selection, and `Ctrl+A` selects pins of that kind. Review and simplify recorded motion before delivery. [Adobe: Puppet animation](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animate-with-puppet-tools/animating-puppet-tools.html).

## 4.22 Solve a moving camera before placing 3D graphics

Apply **3D Camera Tracker** to a suitable shot and let analysis finish. Choose reliable points on the surface, establish a ground plane/origin when needed, and create a camera plus a null, solid or text from the tracked selection. Attach your artwork to the resulting scene reference. Check several points and the whole shot for sliding.

A camera solve estimates camera movement from scene features. A point track follows an image feature; a planar track follows a surface. Moving subjects, cuts, insufficient parallax, changing focal length, motion blur and reflections can confuse a camera solve. Treat separate shots separately. [Adobe: 3D Camera Tracker](https://helpx.adobe.com/after-effects/desktop/work-with-3d-composition/3d-camera-tracker-effect/tracking-3d-camera-movement.html).

## 4.23 Preserve intro/outro timing in reusable graphics

Set the work area around the entrance animation, right-click the work-area bar and choose **Create Protected Region from Work Area**. Repeat for the exit. Alternatively, use **Composition → Responsive Design – Time** or a duration marker with **Protected Region** enabled. Nest the comp and test a duration change: protected regions keep their animation timing while unprotected time stretches. This is useful for a title with a fixed entrance/exit and variable reading time. [Adobe: Responsive Design – Time](https://helpx.adobe.com/after-effects/desktop/motion-graphics/add-responsive-design/responsive-design.html).


## 4.24 Crop the composition canvas to artwork

Select the relevant **2D layers**, then **Composition → Crop Comp to Selected Layer(s) Bounds**, or use the selected layers' context menu. The canvas can shrink or expand to fit. This command is unavailable for 3D layers. Save a version first and inspect animated artwork, effects extending past layer edges and nested-comp alignment afterward. Canvas cropping changes composition dimensions; **Trim Comp to Work Area** changes duration; **Fit to Comp** changes a layer's transform. [Adobe: composition bounds](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/composition-settings/composition-basics.html).


## 4.25 Turn text into editable outlines

Select the text layer, then **Layer → Create → Create Shapes From Text** for shape groups, or **Create Masks From Text** for masks on a new solid. Use shapes for vertex animation, morphs and shape operators; use masks for workflows needing mask paths, such as Generate → Stroke. Retain the original text layer: the generated outlines are not live font/text content, and changing the original wording does not update them. Inspect compound letters with holes and any existing animation after conversion. [Adobe: text outlines and masks](https://helpx.adobe.com/after-effects/desktop/drawing-painting-and-paths/work-with-masks/create-masks.html).

## 4.26 Photoshop layers and editable text

Import a layered PSD as a **composition** when you need separate layers. **Retain Layer Sizes**, where offered, gives layers bounds based on their own content instead of every layer using the document canvas; choose deliberately because bounds affect anchors and effects. Saving changes to existing source layers in Photoshop updates their footage in AE, but AE's layer transforms and the imported composition structure are separate. Recheck bounds and reimport when the PSD's structure changes substantially. Newly added Photoshop layers are not automatically added to an existing imported comp. [Adobe: PSD import and refresh](https://helpx.adobe.com/after-effects/desktop/import-files/import-still-images/preparing-importing-still-images.html).

For an actual Photoshop text layer, select **Layer → Create → Convert to Editable Text**. It becomes native AE text and **stops using the Photoshop text layer as its source footage**. Keep a source version if ongoing Photoshop changes must remain linked. This differs from editable Illustrator-text conversion listed in the Beta watchlist. [Adobe: Photoshop text conversion](https://helpx.adobe.com/after-effects/desktop/add-text/create-and-edit-text-layers/creating-editing-text-layers.html).

## 4.27 Preserve intentional nested-comp timing

In the **source precomp**, open `Ctrl+K` → **Advanced**. **Preserve frame rate when nested or in render queue** keeps that comp's temporal sampling. Example: a 12-fps character animation can retain its stepped cadence inside a 24-fps master. **Preserve resolution when nested** retains that comp's own resolution setting instead of inheriting the containing comp's setting. Neither option creates missing frames or image detail. A lower-rate master still cannot display every distinct frame of a higher-rate source. [Adobe: nested-comp settings](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/precomposing-and-nesting/precomposing-nesting-pre-rendering.html).

**Canvas size from the dialog:** Leave All Attributes uses the selected layer's dimensions and is unavailable for multiple layers, text or shapes. Move All Attributes uses the original composition's frame size.

**Precomp canvas versus source quality:** Move All Attributes creates a processing boundary whose canvas can clip content or limit later enlargement. The source footage inside is still present; precomposing does not rewrite it into a lower-resolution file. Inspect canvas dimensions, the inner and outer transforms, and Collapse Transformations before deciding how to fix softness.

## 4.28 Align and distribute without eyeballing

Open **Window → Align**. Choose **Align Layers To → Composition** for the frame, or **Selection** for the selected layers' bounds. Distribution needs at least three layers and spaces them between the extremes. For chart tick labels, place the first and last labels, select all the labels, then distribute horizontally or vertically. Equal center spacing does not imply equal visible gaps when layers differ in size. [Adobe: alignment](https://helpx.adobe.com/after-effects/desktop/work-with-layers/select-and-arrange-layers/selecting-arranging-layers.html).

**Reorder by selection:** Ctrl-click layers in the desired order, Cut, then Paste to reorder them by selection order. Inspect the stack immediately, since changing it changes compositing and can affect rigs based on layer indices. Use Undo if the result is not intended.

## 4.29 Move a line of text along a curve

Draw a mask path on a text layer. Expand **Text → Path Options**, choose that mask as **Path**, then animate **First Margin** or the appropriate margin control for the text's alignment. **Reverse Path** changes direction; **Perpendicular To Path** controls character orientation; **Force Alignment** distributes characters between the two margins. This moves the text along the path; a Position animator instead offsets selected characters. [Adobe: text on paths](https://helpx.adobe.com/after-effects/desktop/animating-text/text-animation/animating-text.html).

For tracking that expands around the center, begin with **center paragraph alignment**, then animate Tracking Amount and inspect selector behavior. The layer anchor controls the layer's transform pivot; centering that anchor alone does not set typographic alignment.

## 4.30 Photoshop-style Layer Styles

Select a pixel-bearing layer and choose **Layer → Layer Styles**, or its context menu. Available styles include **Drop Shadow, Inner Shadow, Outer Glow, Inner Glow, Bevel and Emboss, Satin, Color Overlay, Gradient Overlay, and Stroke**. Expand **Layer Styles** in Timeline to edit or animate supported controls.

**Style Stroke** outlines the layer's visible content. **Generate → Stroke** follows mask paths, while a shape Stroke belongs to a shape group's geometry. They are useful for different kinds of outlines. Styles also have their own compositing behavior; test precomps and renderer changes rather than treating a style as an ordinary effect in Effect Controls. [Adobe: Layer Styles](https://helpx.adobe.com/after-effects/desktop/work-with-layers/work-with-layer-blending-modes/blending-modes-layer-styles.html).

---

# 05 — Effects Encyclopedia

> [!note] What counts as an effect?
> In **Effects & Presets**, *effects* live in categories such as Distort, Keying, Stylize, Simulation, etc. **Track Mattes**, **Roto Brush/Object Matte tool selection**, **shape modifiers**, **Layer Styles**, **Create Nulls From Paths**, **Precompose**, **scripts**, and **third-party add-ons** may appear near effects or create effects, but they are conceptually distinct. Use `Ctrl+Enter` (**Quick Apply**) and search `e:name` to find a native effect quickly. Names marked **NEW** were introduced in recent 2026 releases. The list below covers native/bundled effect families; installed third-party inventories vary.

## 5.1 Choose the right effect by problem

| I want to... | Start with | Frequently paired with |
|---|---|---|
| Make something glow | **Glow** | Levels / Find Edges / CC Light Sweep |
| Replace green screen | **Keylight (1.2)** | Key Cleaner / Advanced Spill Suppressor |
| Draw an outline on a moving subject | **Stroke** on traced masks | Roto/Object Matte / Glow |
| Make a stroke draw itself | **Trim Paths** (*shape operator*, not effect) | Taper / glow |
| Warp a still with a depth map | **Displacement Map** | Blur on map / source cleanup |
| Blur according to grayscale matte | **Camera Lens Blur** + Blur Map | Levels / feathered gradient |
| Move pixels as liquid | **Turbulent Displace** / **Liquify** | Noise / glow / masks |
| Simulate animated flowing noise | **Curl Noise (NEW 26.3)** | Gradient colorization / displacement |
| Create organic clouds/smoke textures | **Fractal Noise / Turbulent Noise** | Levels / Tint / Glow / Displacement |
| Create particles | **CC Particle World / Systems II** | Physics, masks, glow |
| Create bubbles/liquid blobs | **CC Bubbles / Foam / CC Mr. Mercury** | Blur / CC Glass |
| Fake rotating globe | **CC Sphere** | Map texture / lighting options |
| Turn footage into CRT/VHS-style | **Posterize Time, Noise, Turbulent Displace** | Channel offset, chromatic color fringe |
| Add lens distortion/fisheye | **Optics Compensation** | Scale to hide black corners |
| Stabilize footage | **Warp Stabilizer** | Crop/scale/track |
| Follow planar surface | **Mocha AE** | Corner Pin / CC Power Pin |
| Add line/gradient beam | **Beam** / **Advanced Lightning** | Glow |
| Build tiled repetition | **Motion Tile / CC RepeTile / CC Tiler** | Offset / animated scale |
| Convert light on black into transparency | **Unmult (NEW 26.0)** | Glow / compositing |
| Compress/gate/distort audio | **Compressor / Gate / Distortion (NEW 26.0)** | Parametric EQ, audio-level controls |

## 5.2 Effects introduced in 2026 — learn these first

### NEW — Curl Noise (26.3; Noise & Grain / procedural texture)

Creates **flow-like swirling patterns** suitable for smoke, ink, fluid textures and organic animation. Compared with ordinary static noise, it is designed for coherent flowing movement. Explore the evolution/animation controls and spatial scale first; use **Colorama, Tint or Tritone** for color and **Displacement Map** if you want the patterns to distort other footage. Unlike **Fractal Noise**, this is not just random turbulence with evolution cranked up. Find it by Quick Apply `e:curl noise`.

### NEW — Unmult (26.0)

A keying/compositing effect for extracting **white-on-black or black-on-white** elements into usable alpha while preserving useful foreground colors/details. Ideal for lens flares, smoke, particles or light elements shot/rasterized over an uncolored background. Configure black vs white removal and judge any color-fringing at semi-transparent edges. This is often cleaner than treating Screen blend mode as a real alpha conversion.

### NEW — Compressor (26.0, Audio)

Reduces dynamic range above a threshold. Key controls: **Threshold** (level at which compression begins), **Ratio** (strength), **Attack** (response to transient), **Release** (recovery), **Knee** (transition), **Makeup Gain** (restore loudness), **Output Limit**. Dialogue starting point: a moderate ratio (~3:1), then adjust by listening, not by copying presets.

### NEW — Gate (26.0, Audio)

Reduces/silences quiet sections under the configured threshold. Controls **Threshold, Attack, Hold, Release**. Useful to reduce pauses with room noise; too aggressive a threshold chops beginnings/endings of words. Doesn't actually remove noise *underneath active speech*.

### NEW — Distortion (26.0, Audio)

Audio overdrive/saturation and sound design (Soft Clip, Hard Clip, Tube, Fuzz and related modes). Controls Drive/Gain, dry-wet Mix and output level, plus bit reduction/downsampling for lo-fi sounds. Watch clipping and hearing comfort.

### NEW — Effect colors (26.5; not an effect)

Color labels can be assigned to **individual effect instances** in the modernized **Effect Controls** panel; group logically by purpose (`cleanup`, `keying`, `look`, `finishing`) when a layer contains many effects. Labels do not modify pixels and aren't a replacement for clear effect names/order.

### NEW — 3D displacement / depth of field (26.2–26.3; not simple 2D effects)

**Material displacement on parametric meshes** changes true 3D surface geometry according to height data (26.2). **In-engine Depth of Field** in Advanced 3D (26.3) blurs rendered scene geometry based on camera focus. They are different from **Displacement Map** and **Camera Lens Blur** 2D effects.

## 5.3 Blur & Sharpen

| Effect | What it does / choose it for |
|---|---|
| **Camera Lens Blur** | Lens-like bokeh; can use **Blur Map** for local/depth-varying blur, including highlights and focus control. Heavier than simple blur. |
| **Gaussian Blur** | Smooth, conventional uniform blur. Fast and reliable; repeat edge pixels if borders appear. |
| **Fast Box Blur** | Efficient box approximation; useful in matte softening, textures and previews. |
| **Directional Blur** | Linear blur along an angle; simulated speed smear/trails. |
| **CC Radial Blur** | Radial directional or zoom-streak blur based on a center; useful for impact effects. |
| **CC Radial Fast Blur** | Faster radial-style effect; fewer/other controls than standard. |
| **Radial Blur** | Older rotational/zoom radial blur option; still useful in some looks, not automatically obsolete. |
| **CC Vector Blur** | Uses vectors/texture for stylized flow-like warping/blurring. |
| **Compound Blur** | Uses a secondary layer's brightness to change blur amount across the image. |
| **Channel Blur** | Independently blurs R/G/B/A channels; potential color fringes and alpha softening. |
| **Bilateral Blur** | Smooths regions while retaining prominent edges. |
| **Smart Blur** | Smooths relatively uniform regions but attempts to protect edges. |
| **Sharpen** | Basic edge enhancement; halos appear if overdone. |
| **Unsharp Mask** | Controlled edge contrast sharpening, Amount/Radius/Threshold. |
| **Camera-Shake Deblur** | Attempts to mitigate motion-induced camera blur; may create artifacts. |
| **CC Cross Blur** | Cross-shaped/anamorphic-style blur direction. |

**Rule of thumb:** soften a matte with a simple blur; create cinematographic blur with Camera Lens Blur; do **not** confuse “blur the layer” with “blur the alpha matte,” because they change different data.

## 5.4 Channel and matte-related effects

| Effect | Use case |
|---|---|
| **CC Composite** | Composites the processed result against the layer's underlying original input inside one effect pipeline; try RGB-only/alpha and blend settings when nothing seems to happen. |
| **Blend** | Blends the current layer with another input layer. |
| **Set Matte** | Pull alpha or luminance from a specified source and inject it into effect pipeline. |
| **Shift Channels** | Route/copy individual channels; build simple RGB split/aberration effects. |
| **Set Channels** | Assemble output channels from multiple source channels/layers. |
| **Channel Combiner** | Converts/composes color channels, including channel-to-lightness conversions. |
| **Invert** | Invert color values or selected channels, including alpha where chosen. |
| **Minimax** | Grow/shrink bright or dark regions, useful for matte expansion/erosion. |
| **Remove Color Matting** | Correct halo caused by premultiplication against an unwanted matte color. |
| **Arithmetic / Compound Arithmetic** | Mathematical channel operations on image values. |
| **Calculations** | Combine input layer channels and mathematical/compositing operations. |
| **Solid Composite** | Composites layer over specified solid color within the layer bounds; useful for creating black/white map backgrounds. |
| **Matte Choker / Simple Choker** | Shrink/expand matte edges and clean alpha. |
| **Refine Hard Matte / Refine Soft Matte** | Edge refinement for hard/soft/semipermeable edges. |
| **Mocha Shape** | Workflow-specific Mocha-created masks/mattes if available. |

## 5.5 Keying

| Effect | Best use |
|---|---|
| **Keylight (1.2)** | General green/blue screen chroma key with detailed Screen Matte controls. |
| **Key Cleaner** | Recover/clean key edges, especially fine outlines. |
| **Advanced Spill Suppressor** | Remove undesirable green/blue color contamination on subject edges. |
| **Unmult** | **NEW**: separate luminous elements from black/white solid backgrounds; not a chroma key. |
| **Linear Color Key** | Remove specified RGB color/range; controlled simple key. |
| **Color Difference Key** | Difference-based blue/green keying. |
| **Color Range** | Target color range; sometimes useful for selections but can be noisy. |
| **Difference Matte** | Remove area resembling clean background reference. |
| **Extract** | Use luminance thresholds to isolate bright/dark regions. |
| **Inner/Outer Key** | Separate inner/outer edges for difficult mattes. |
| **CC Simple Wire Removal** | Remove linear cables/wires across footage. |
| **Color Key** | **Obsolete**. Retained for compatibility; not mandatory in modern key workflow. |

## 5.6 Color correction and color management effects

| Effect | When it helps |
|---|---|
| **Lumetri Color** | Comprehensive familiar color grading (basic, curves, color wheels, HSL secondary, vignette where available). |
| **Curves** | Precise tonal/channel mapping, matte contrast, look creation. |
| **Levels / Levels (Individual Controls)** | Set input/output black and white points, gamma/channel adjustments. |
| **Exposure** | Adjust photographic exposure and tonal offset; useful in higher-bit-depth workflows. |
| **Hue/Saturation** | Target hue, saturation and lightness globally or ranges. |
| **Tint / Tritone / CC Toner** | Duotone or three-tone palette, stylized color mapping. |
| **Black & White** | Tonal monochrome conversion with channel weights. |
| **Brightness & Contrast** | Basic global correction; limited compared with Curves. |
| **Vibrance** | Prioritize less-saturated color areas over skin tones in many cases. |
| **Selective Color** | Fine color-group adjustment. |
| **Color Balance / Color Balance (HLS)** | Adjust channel balance or HLS values. |
| **Channel Mixer** | Change RGB output from channel mixtures, make custom grayscale. |
| **Change Color / Change to Color** | Isolate a hue and shift its color; second is a selected-color replacement workflow. |
| **Leave Color** | Retain one selected hue while desaturating others. |
| **Colorama** | Map luminance/phase to customizable color palette. |
| **Color Link** | Match/link colors in layer-based workflows. |
| **Color Stabilizer** | Correct fluctuating color using sample areas. |
| **Auto Color / Auto Contrast / Auto Levels** | Automatic tonal/color balance; test temporal flicker on video. |
| **Equalize** | Redistribution of intensity histogram; stylized image contrast. |
| **Photo Filter** | Warm/cool photographic filter color. |
| **Shadow/Highlight** | Recover local dark/light detail; can flatten images. |
| **Gamma/Pedestal/Gain** | Channel/tonal control using photographic video parameters. |
| **CC Color Neutralizer / CC Color Offset / CC Kernel** | Bundled specialty color/convolution controls. |
| **Video Limiter / Broadcast Colors** | Keep signals within broadcast/video-limiting constraints; choose for deliverable standard. |
| **PS Arbitrary Map** | Photoshop arbitrary-map transfer function. |
| **Apply Color LUT** | Apply LUT transform; **must** use appropriate input color space to avoid bad look/double transform. |
| **Color Profile Converter** | Convert between color-profile spaces; distinct from simply applying a look LUT. |
| **Cineon Converter / HDR Compander / HDR Highlight Compression** | Specialized film/log/HDR handling. |

**Don't mix up:** *Viewer exposure*, *a LUT that changes your rendered pixels*, and *Project Color Management* are three different levels. Changing viewer exposure is for inspection and doesn't bake the change into file by itself.

## 5.7 Distort and transform (complete practical directory)

| Effect | What to expect |
|---|---|
| **Bezier Warp** | Warp whole image using edge/corner Bézier mesh handles; useful for cloth/smoke edges. |
| **Mesh Warp** | Fine multi-cell deformation; more localized control. |
| **Liquify** | Brush deformation/mesh for facial distortions, fire, organic visuals. |
| **Displacement Map** | Another layer's brightness/color shifts pixels horizontally/vertically; depth-map illusion, water, ripples. |
| **Transform** | Effect-order-aware extra transform; better than moving an entire layer when you need transform **between** effects. |
| **Turbulent Displace** | Organic bubbling/wavy displacement; great for smoke, fire, animated drawn art. |
| **Wave Warp** | Directional sine-wave fabric/hair/flag/river motion; Pinning controls where waves are suppressed. |
| **Warp** | Preset-type arcs, bulges, flags, fisheye and other deformable shapes. |
| **CC Bend It** | Bend along points, fast for organic/tube bends. |
| **CC Bender** | Bend based on bounding region with different behavior/controls. |
| **CC Blobbylize** | Glossy melted/liquid-like blobby shapes; useful for fluid motion design. |
| **CC Flo Motion** | Localized distortions around two control points. |
| **CC Griddler** | Grid-based segmentation/deformation. |
| **CC Lens** | Bulging/refracting lens distortion. |
| **CC Page Turn** | Flip/curl page. |
| **CC Power Pin** | Pin four corners of footage to a changing quadrilateral; screen replacement. |
| **Corner Pin** | Four-corner pinning; simpler option, still valid, not universally obsolete. |
| **CC Ripple Pulse** | Dynamic ripples that usually need animated Pulse/Amplitude values. |
| **CC Slant** | Skew/slant geometry. |
| **CC Smear / Smear** | Stretch content along control points. |
| **CC Split / CC Split 2** | Stretch/open slit or mouth-like deformation, second has additional handles. |
| **CC Tiler** | Copies a layer in tiles for infinite-space visual patterns. |
| **Mirror** | Reflect content across adjustable line. |
| **Offset** | Wrap image as it moves; seamless scrolling backgrounds. |
| **Optics Compensation** | Fisheye / undistort lens geometry; **Reverse Lens Distortion** changes direction. |
| **Polar Coordinates** | Rectangle↔polar map (tunnels/planet rings/clock-like effects). |
| **Reshape** | Morph one drawn silhouette toward another using masks. |
| **Ripple** | Water-surface-style waves. |
| **Rolling Shutter Repair** | Correct skew/wobble artifacts from rolling-shutter cameras. |
| **Spherize / Bulge / Magnify** | Distort or magnify a specified area; different controls and aesthetics. |
| **Twirl** | Twist pixels around center. |
| **Warp Stabilizer** | Automatic image stabilization; separate from intentionally creative warp. |
| **Detail-preserving Upscale** | More edge-aware scaling than simple magnification; evaluate fine details/halos. |

## 5.8 Generate — creating pixels, backgrounds, sound visuals

| Effect | Purpose |
|---|---|
| **4-Color Gradient** | Four-point color interpolation, useful backgrounds. |
| **Gradient Ramp** | Linear/radial gradient from start to end control points. |
| **Grid** | Draw a grid, useful graphical background or to diagnose warp. |
| **Checkerboard / Circle / Ellipse** | Procedural primitives, quick masks/background designs. |
| **Audio Spectrum / Audio Waveform** | Draw frequency spectrum or waveform along line/mask path. |
| **Advanced Lightning / Beam** | Electricity or colored straight beam between points. |
| **CC Light Burst 2.5 / CC Light Rays / CC Light Sweep** | Radiating streaks, shafts, animated highlight across a surface. |
| **CC Threads / CC Glue Gun** | Specialized generated strands/blobs. |
| **Cell Pattern / Fractal** | Procedural structures, tessellated/fractal patterns. |
| **Eyedropper Fill / Fill / Paint Bucket** | Fill color/regions or sampled colors. |
| **Lens Flare** | Simulated flare based on a point in the frame. |
| **Radio Waves** | Expanding rings/ripples; great for motion graphics. |
| **Scribble** | Animated scribble lines in masked regions. |
| **Stroke** | Render one or more mask paths with animated Start/End; glows/outlined drawings. |
| **Vegas** | Animated outlines/highlights using mask/contour paths. |
| **Write-on** | Frame-accumulating paint-like writing. |

**Effects typically create a result inside their source layer's effective dimensions.** A comp-sized solid is a convenient full-frame carrier for generators and effects that work on all pixels, but not required for every effect. An adjustment layer is a *processor of composited underlying layers*, not the same as a standalone visible solid.

## 5.9 Noise & Grain, including flow textures

| Effect | Use |
|---|---|
| **Curl Noise (NEW 26.3)** | Swirling directional procedural motion. |
| **Fractal Noise** | Rich coherent noise. Animate **Evolution/Offset Turbulence**, transform for scale, use Evolution Options for cycle where supported. |
| **Turbulent Noise** | Related procedural pattern with different rendering/performance/features; not “always faster” in every project. |
| **Noise** | Pixel-level random texture/dither/grain. |
| **Add Grain / Match Grain / Remove Grain** | Film/grain matching and cleanup workflows. |
| **Dust & Scratches** | Remove small specks or produce painterly smoothing at high values. |
| **Median / Median (Legacy)** | Simplify detail by neighborhood median; stylized cartoon look; legacy compatibility separate. |
| **Noise Alpha** | Randomize transparency. |
| **Noise HLS / Noise HLS Auto** | Hue/lightness/saturation based noise. |

## 5.10 Simulation and particles

| Effect | Typical appearance |
|---|---|
| **CC Particle World** | 3D-like particle generator with birth rate, producer, physics, particle type. |
| **CC Particle Systems II** | Simpler particle bursts/trails. |
| **Particle Playground** | Rich older multi-input particle system, more configuration. |
| **CC Ball Action** | Turns input into grids of balls—great for scattering/reforming graphics. |
| **CC Pixel Polly** | Shatters source pixels/regions as simulated chunks. |
| **Shatter** | Break objects apart with forces/debris/camera. |
| **CC Mr. Mercury** | Viscous liquid-like emitted blobs/droplets. |
| **Foam** | Bubble simulation from flow/birth parameters. |
| **CC Bubbles** | Floating/refractive bubbles sampling underlying image appearance. |
| **CC Rainfall / CC Snowfall** | Weather particles. |
| **CC Drizzle** | Droplet surface ripples. |
| **CC Hair** | Bundled hair/strand simulation effect. |
| **CC Scatterize** | Scatter/fragment look. |
| **CC Star Burst** | Starfield/travel visual. |
| **Card Dance** | Animate source as discrete cards driven by control map. |
| **Caustics** | Light refraction patterns through water/glass simulation. |
| **Wave World** | Generated simulated rippling water surfaces, usable as effect map. |

## 5.11 Stylize / time / transitions

| Effect | Use |
|---|---|
| **Glow** | Bloom around bright areas; threshold/radius/intensity control. |
| **Find Edges** | Outline high-contrast boundaries, optionally invert/colorize. |
| **Brush Strokes / Cartoon** | Paint-like or cartoon simplification. |
| **CC Glass / CC Plastic** | Surface relief/glassy or plastic highlights. |
| **CC Kaleida / CC HexTile** | Kaleidoscopic tessellation. |
| **CC RepeTile / Motion Tile** | Extend a layer beyond original bounds by repetition/mirroring. |
| **CC Vignette** | Dark/light edge vignette. |
| **CC Threshold / CC Threshold RGB / Threshold** | Convert to limited tonal/channel regions. |
| **Roughen Edges** | Organic irregular alpha edge, strong for paper/smoke/mattes. |
| **Mosaic** | Pixel-block mosaic. |
| **Posterize** | Reduce color levels; not frame rate. |
| **Strobe Light** | Timed flashes/strobes. |
| **Color Emboss / Emboss / Texturize** | Relief and texture simulations. |
| **Scatter / CC Block Load / CC Burn Film / CC Mr. Smoothie** | Specialty scatter/loading/burn/flow style. |
| **CC Force Motion Blur** | Extra blur samples for moving operations; cost may be high. |
| **Pixel Motion Blur** | Optical flow-like blur between frames. |
| **Echo / CC Wide Time** | Temporal accumulation of multiple frames. |
| **Posterize Time** | Lower sampling rate for stop-motion look. |
| **Time Difference** | Highlights changes between frames. |
| **Time Displacement** | Grayscale layer controls relative source time per pixel. |
| **Timewarp** | Retimes source with frame interpolation. |

**Transitions available as effects:** **Block Dissolve, Card Wipe, CC Glass Wipe, CC Grid Wipe, CC Image Wipe, CC Jaws, CC Light Wipe, CC Line Sweep, CC Radial ScaleWipe, CC Scale Wipe, CC Twister, CC WarpoMatic, Gradient Wipe, Iris Wipe, Linear Wipe, Radial Wipe, Venetian Blinds**. Most use an animated **Transition Completion** or related control. For custom reveals, a shape track matte can be lighter and more controllable than a canned transition.

## 5.12 Perspective, immersive video, 3D channel and utility

| Family | Effects to recognize |
|---|---|
| **Perspective** | **3D Camera Tracker** (reconstruct camera motion); **3D Glasses** (stereo layout); **Bevel Alpha/Bevel Edges** (faux raised edges); **CC Cylinder** (wrap layer around cylindrical surface); **CC Environment** (environment projection); **CC Sphere** (flat image → rotatable shaded sphere); **CC Spotlight** (faux lighting); **Drop Shadow / Radial Shadow**. |
| **3D Channel** | **3D Channel Extract**, **Cryptomatte**, **Depth Matte**, **Depth of Field**, **Extractor**, **Fog 3D**, **ID Matte**, **IDentifier**. Many need actual upstream 3D-channel passes; simply applying to ordinary 2D footage may not produce useful results. |
| **Immersive Video** | **VR Blur, VR Chromatic Aberrations, VR Color Gradients, VR Converter, VR De-Noise, VR Digital Glitch, VR Fractal Noise, VR Glow, VR Plane to Sphere, VR Rotate Sphere, VR Sharpen, VR Sphere to Plane**. VR-aware options account for spherical projection and seams. |
| **Utility** | **Apply Color LUT, CC Overbrights, Cineon Converter, Color Profile Converter, Grow Bounds, HDR Compander, HDR Highlight Compression**. |
| **CINEMA 4D integration** | **CINEWARE** displays imported Cinema 4D scenes and exposes relevant render/camera/pass controls. It is separate from the composition’s Cinema 4D renderer. See §5.17. |
| **Text effects** | **Numbers** and **Timecode** are older *effects*; ordinary editable text and text animators are separate. |
| **Expression Controls** | **Slider Control, Angle Control, Checkbox Control, Color Control, Point Control, 3D Point Control, Layer Control, Dropdown Menu Control**: control values for expressions, not “visual effects” on their own. |

## 5.13 Audio effects — 2026 complete working shortlist

**Backwards** (reverses audio only), **Bass & Treble**, **Compressor (NEW)**, **Delay**, **Distortion (NEW)**, **Flange & Chorus**, **Gate (NEW)**, **High-Low Pass**, **Modulator**, **Parametric EQ**, **Reverb**, **Stereo Mixer**, **Tone**. A **Compressor** can improve dialogue consistency; **Gate** can eliminate near-silent breaks; **Parametric EQ** removes unwanted frequency emphasis; **Reverb/Delay** create spatial/time effects. These are neither replacements for a proper audio mix nor automatically included in the Effects list on older installs.

## 5.14 Obsolete effects / third-party names — don't confuse with defaults

**Obsolete category (legacy project compatibility):** **Basic 3D, Basic Text, Color Key, Gaussian Blur (Legacy), Lightning, Luma Key, Path Text, Reduce Interlace Flicker, Spill Suppressor**. A legacy effect may still be present when a saved project requires it; for new work normally prefer a newer equivalent. **Mocha AE** is a bundled planar tracker; **Mocha Pro** is separately licensed and more capable. **Deep Glow**, **Deep Glow 2**, **Overlord**, **Motion 4**, **FX Console**, **Depth Scanner**, **loopFlow**, **True Comp Duplicator**, **imgPaster**, **ScaleUp**, **Crate's Godrays**, etc. are **third-party extensions/plugins/scripts**—they are **not all included in vanilla AE**. Check exact version, vendor and licensing before depending on them in a shared project.

---



## 5.15 Learn the new effects with small experiments

### Curl Noise: separate structure, motion and output

On a comp-sized solid, use `Ctrl+Enter` → `e:curl noise`. Begin with **Internal** input. **Speed/Direction** drift the source field; **Evolution** changes the swirl state; **Turbulence Speed** changes its temporal activity. **Scale/Offset/Rotation** place the pattern. **Swirl** changes curling strength, **Smoothness** softens detail, and **Density** redistributes structure. In Flow Options, **Sample Count/Radius** affect strand continuity and reach. Inspect **Input Noise**, **Curl Generation**, then **Final Render** to understand the stages. Colorize afterward with Tint/Tritone/Colorama.

For a loop, set Input Noise Speed and Turbulence Speed to zero before cycling Evolution; test the seam. Layer-driven input uses source luminance and disables internal drift controls. [Adobe: Curl Noise](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/noise-grain-effects.html).

### Unmult: create alpha from a black or white backing

Use `e:unmult`, choose **Background Color → Black/White**, and adjust its **Black Level/White Level** while inspecting alpha with `Alt+4`. Use **Softness** for the transparency transition and **Remove Color Matting** to reduce backing-color contamination. **Clip HDR Results** limits out-of-range values. Check the result over both bright and dark backgrounds. A genuinely black part of the subject may disappear with black removal; use a protected matte when needed. [Adobe: Unmult](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/work-with-keying-effects/keying-effects.html).

### Audio: judge the level-matched result

For **Compressor**, lower Threshold until loud phrases become steadier, then compare with bypass at similar loudness. Auto Release overrides manual Release. For **Gate**, set Threshold above the unwanted room level and preserve word endings using Hold/Release. For **Distortion**, compare Type choices with low Drive, blend using Mix, and compensate with Volume. Resolution and Downsample produce digital lo-fi damage. [Adobe: audio controls](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/audio-effects.html).

## 5.16 Control any effect without rebuilding your layer stack

**One effect on part of a layer:** draw a mask, set its mode to **None** if you do not want it to cut layer alpha, expand the effect in Timeline, then **Compositing Options → + → Mask Reference**. Select the mask and feather it. Effect Opacity blends that effect's contribution. This is useful for a localized Curves correction or blur. Multiple effects can use different masks. Check the effect's support and render order, especially on unusual layer types. [Adobe: effects and compositing options](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effects-animation-presets-overview.html).

**Reuse a look:** select the intended effects/properties, then **Animation → Save Animation Preset** to save an `.ffx`. A preset stores the chosen setup; it does not bundle source footage, fonts or plugins. Before applying it, put CTI at the intended start and inspect existing keyframes. A preset can overwrite or add properties and animation.

**Set Matte source mismatch:** a matte sampled inside an effect may use different coordinates or source-stage pixels from a normal track matte. Match source dimensions, precompose the matte's transforms/effects when necessary, and inspect its actual alpha/luma. Prefer a native Track Matte for straightforward moving silhouettes.



## 5.17 Integration effects and effect compatibility

**CINEWARE** is the bridge for Cinema 4D scene layers. Import a `.c4d` scene and inspect its CINEWARE settings for renderer, camera and multipass handling. Editing the scene and rendering a 2D AE layer through CINEWARE differs from selecting **Cinema 4D** in Composition Settings. Required Maxon components and their versions matter. [Adobe: Cinema 4D and CINEWARE](https://helpx.adobe.com/after-effects/desktop/work-with-other-applications/work-with-cinema-4d-and-cineware/c4d.html).

**Boris FX Mocha / Mocha AE** is bundled planar tracking integration. Launch its interface from the effect, track a suitable plane, then bring tracking/matte data back for the intended workflow. The effect can be installed yet its external interface or data export may still need attention. Mocha Pro is a separate product.

**Tool-generated effect groups** include Paint, Puppet, and roto/matte processing. Their controls appear after using the relevant tool; they do not all behave like dragging a conventional effect onto a solid.

### Read support indicators before blaming your GPU

An effect can support **Multi-Frame Rendering**, **GPU processing**, and **8/16/32-bpc** independently. CPU effects can coexist with GPU effects. An 8-bpc-only operation may constrain precision in a higher-depth project; 32-bpc project settings alone do not make every operation HDR-safe. Alpha handling, renderer support and source-layer sampling also vary. Check the effect's own documentation and installed indicators. [Adobe: effect support directory](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effect-list.html).

# 06 — Everything New and Important Through 26.5

> [!important] Release scope
> **26.5 (September 2026)** is the target stable version for this reference, as of **October 27, 2026**. Feature availability can vary with the precise point release, hardware, renderer, UI workspace, and plugin versions. Features advertised only for **After Effects (Beta)** are not assumed to exist in the stable build. Check **Help → About After Effects** for the version you actually installed.

**The new stable Effect-menu additions covered here are Unmult, Compressor, Gate and Distortion (26.0), plus Curl Noise (26.3).** The headline 26.5 additions concern guides, matte caching, effect organization and color management. Contour Paint and De-esser belong to the separate Beta watchlist below.

## 6.1 The version-by-version field guide

| Release | What changed | Why it matters / practical starting point |
|---|---|---|
| **26.5 — September 2026** | Better **guides** with percentage-based placement, pinning, and individual colors. | Set guides by percentage for portrait, square and landscape variations; anchor margins to the appropriate edge; **Lock Guides** separately prevents accidental dragging. |
| **26.5** | **Object Matte** results can use disk caching, improving reuse and reopening performance. | Cache and keep work localized, and still review matte edges after major changes. |
| **26.5** | **Effect Controls** improvements including **color labels for effects**. | Group heavy effects, color-code keyers/color/glow, and locate effect stacks faster. These are labels on controls, not a new visual effect. |
| **26.5** | Color-management updates, including **ACES 2.0** support and updated **OpenColorIO 2.5.1**. | Make project/output color settings explicit before compositing; an ACES workflow is not automatically correct for every delivery. |
| **26.3 — June 2026** | **Curl Noise** effect for swirling, organic 2D textures. | Try fluid ink, smoke and turbulence-style patterns rather than relying only on Fractal Noise. |
| **26.3** | **Depth of Field** inside the **Advanced 3D renderer**. | Adjust focus in a real 3D scene, not merely a flat blur over a 2D image. |
| **26.3** | Paste **Illustrator and SVG content** as editable shape content into AE. | Move vector paths into an animation-ready shape workflow when appropriate; inspect imported appearance and hierarchy. |
| **26.3** | Improved **Mask Tracker** with faster tracking and more accessible Timeline controls. | Track a mask rather than hand-keyframing every frame for simple moving objects. |
| **26.3** | **Copy Frame to Clipboard**. | Grab an exact rendered frame for review, painting or reference without a full sequence export. |
| **26.3** | Better discovery of **variable fonts by axis**. | Find fonts whose weight/width or other supported axes are animatable. |
| **26.2 — April 2026** | **Object Matte** for selecting/tracking foreground subjects. | Start with quick subject selection, refine on problem frames, then freeze/cache as appropriate. |
| **26.2** | **Quick Apply** search launcher for effects, presets and more. | Press **Ctrl+Enter**; type a name rather than scrolling hundreds of effects. |
| **26.2** | **3D Mesh Displacement** for modern 3D workflows. | Use geometry-based deformation; distinct from 2D Displacement Map on ordinary pixels. |
| **26.2** | **Proportional Scrubbing** distributes value changes across selected layers. | Hold `Ctrl+Alt` while dragging selected property hot text; selection order determines the distribution. |
| **26.0 — January 2026** | **Parametric 3D meshes**, expanded 3D/material capabilities and **Substance** integration. | Build basic 3D objects within AE; not a replacement for a full dedicated 3D package. |
| **26.0** | **Variable font axes** in text workflows. | Animate typography at the font-axis level if the installed font supports those axes. |
| **26.0** | **Unmult** effect. | Remove black backing from compatible light/particle/glow passes, then composite correctly with alpha. |
| **26.0** | New audio **Compressor, Gate, Distortion** effects. | Basic dynamics, noise gating and signal distortion without round-tripping for every small edit. |
| **25.4 — earlier foundation** | **Quick Offset** gesture for selected layers/keyframes. | Select in intended order → hold **Ctrl+Alt** → drag to distribute in time. |

### The most useful modern features to learn first

**Quick Apply: `Ctrl+Enter`** opens the search dialog. Type a command, effect or preset; narrow with `e:` (effects), `m:` (menu commands) or `p:` (presets). Once a result is selected **Enter** applies/runs it; **Ctrl+Enter** creates a new solid/layer with that effect where supported, and **Ctrl+Alt+Enter** creates an adjustment layer containing the effect. The same chord has a different function **while the Quick Apply dialog is already open**.

**Quick Offset: `Ctrl+Alt+drag` selected Timeline items.** It works on selected *layer bars* or sets of *keyframes*. It is not the same as pressing `Alt+Left/Right` (frame-by-frame keyframe nudging). Quick Offset spaces a selected sequence based on **selection order**, preserving per-layer animations when offsetting the layers.

**Object Matte vs Roto Brush vs mask tracking.** Object Matte is a semantic/object-selection workflow; Roto Brush grows a foreground selection across frames from guided strokes; Mask Tracker follows a defined mask. None is perfect. A clean roto often combines a rough machine-assisted selection with local corrections, holdout masks, Refine Edge, and careful checks around occlusion/motion blur.

**Advanced 3D vs Cinema 4D renderer vs Classic 3D.** Render engine choice controls supported geometry, materials, lighting, depth and extrusion options. Switching render engines can alter how the composition renders; review the output, not just the settings. AE's Advanced 3D and parametric-mesh capabilities make many basic 3D projects possible without another app, but specialized simulations and modeling may still belong in dedicated 3D software.

**Effect Controls color labels and guide pins.** Color-code related effect instances. Pinning a guide changes its reference edge when frame dimensions change; **locking** guides prevents accidental movement. They serve different purposes.

## 6.2 What “new” does *not* mean

- An **effect** has an adjustable effect instance in Effect Controls (for example **Curl Noise**, **Unmult**, **Glow**).
- A **tool** is selected from the Tools panel (for example Object Matte, Roto Brush, Puppet Pin).
- An **engine** changes how a 3D composition is rendered (for example Advanced 3D).
- A **workspace/panel feature** improves operation but is not an effect (Quick Apply, Quick Offset, Effect Controls labels).
- A **script** automates the application (the older Create Nulls From Paths panel is one example; modern equivalents are also native menu commands); a **preset** is a preconfigured group of properties/effects; a **third-party plugin** is installed separately.
- A **Beta feature** is not reliable evidence that it ships in the stable 26.5 you have installed. Beta and stable releases can differ.


## 6.3 Features still labeled Beta at the research cutoff

Adobe's Beta page, updated **October 7, 2026**, lists the following separately from the stable feature set. These are useful to recognize in tutorials; they are **not instructions to expect these commands in stable 26.5**.

| Beta feature | What it concerns |
|---|---|
| **Contour Paint** | Stylization using image contours. |
| **De-esser** | Targeted attenuation of vocal sibilance. |
| **AI Assistant** | Natural-language assistance inside AE. |
| **Layers as material sources** | Animated layers/precomps as 3D material inputs. |
| **Substance materials on 3D text/shapes** | Expanded material assignment. |
| **Editable Illustrator text conversion** | Importing text as editable AE text. |
| **Quick Set Anchor** | Faster anchor placement. |
| **Alt-drag layer duplication in the viewer** | Broader layer-duplication gesture. |
| **PNG compression setting** | Export speed/file-size control. |
| **Account / Stock panels; enhanced Auto-Save** | Account, asset and recovery workflows. |

Stable shape-selection **Alt** gestures already exist; the broader viewer layer-duplication feature above is a separate Beta item. [Adobe: Beta feature list](https://helpx.adobe.com/after-effects/desktop/what-s-new/after-effects-beta.html).



## 6.4 Changes returning users may have missed before 26.0

**25.2** added the enhanced point/null workflows, HDR monitoring and faster preview playback. **25.3** introduced smoother viewer zooming and Reveal Project in the file system. **25.4** added Quick Offset, text-formatting paste and CICP metadata support. **25.6** added the shared multi-layer 3D gizmo and default-camera controls. These capabilities remain relevant in 26.5, even though tutorials may describe them as new to an earlier release. [Adobe: feature chronology](https://github.com/AdobeDocs/after-effects-feature-history).

---

# 07 — Troubleshooting and Performance

## 7.1 Work faster without damaging final quality

**Aim for interactive previews, not for permanently lowering project quality.** Review these in order:

1. **Limit work area** to the frames you need (`B`, `N`), and preview a few seconds of the shot rather than an entire multiminute composition.
2. Reduce **Composition preview resolution** to Half or Quarter when editing; switch back to Full for pixel-edge checks and final QC. Preview resolution **does not automatically dictate final render resolution**.
3. Close unused viewer tabs, heavy applications and extra compositions. Keep reasonable storage space on the fast drive containing the disk cache.
4. In **Preferences → Memory & Performance** check RAM allocation and **Multi-Frame Rendering** settings. The best parallelism depends on RAM, effect/plugin compatibility and media decoding; “maximum cores” is not always optimum.
5. Temporarily disable the **heaviest effect** (blur, glow, particles, lens blur, denoising, motion blur), then reenable it for output. Disable an *individual* effect via its `fx` toggle, or a *whole layer's effects* using its Timeline Effects switch. `Ctrl+Shift+E` **deletes** effects—do not use it to switch them off.
6. **Precompose strategically**. Precomp when you need a repeated effect stack, isolated matte/transform space, or shared animation. Too many deeply nested precomps can make troubleshooting harder.
7. For repeating, unchanging expensive animations, consider **pre-rendering** a short alpha-preserving clip, replacing a computation-heavy comp temporarily while retaining a source version for edits.
8. Check source **codec** and timing. Long-GOP MP4/H.264/HEVC and **variable frame-rate** screen recordings can be harder to seek and composite than intra-frame ProRes, DNxHR or image sequences. Transcode **when needed**; MP4 and MP3 are not inherently forbidden.
9. **Disk Cache** and **Memory** are different. Purge memory/disk cache only to resolve a stale/corrupt/cache-related problem or reclaim space—frequent purging destroys useful preview work.
10. Before output: reenable effects, inspect alpha, color space, frame rate, motion blur, work area, and output module. Watch a **rendered** sample, not only the interactive Composition viewer.

**Composition Profiler:** When a composition remains slow after basic preview tuning, use the built-in **Composition Profiler** to identify which layers and effects are costly. Diagnose rather than assume Blur/Glow or a particular codec is always the worst offender. Separate preview improvements (lower resolution, shorter work area, proxy) from final-render optimizations (faster source decoding, pre-rendering, reduced effect stacks).

### Project safety and organization

Use **Auto-Save**, keep incremental `.aep` project copies for major creative directions, rename layers, label sections, and avoid placing project media in disposable downloads/cloud placeholders. **File → Dependencies → Collect Files** helps hand off projects, but review the collected assets, fonts, installed plugins and external scripts; merely collecting footage does not bundle all third-party licensing or fonts. Adobe Media Encoder and Premiere interchange may treat data sources differently from AE: test the intended output path early.

## 7.2 If it breaks, diagnose it like this

| Symptom | Most likely checks | Practical fix |
|---|---|---|
| **Nothing appears in the viewer** | Layer eye, Solo, enabled comp, work area, layer in/out, opacity, blend modes, track matte, guide layer, 3D view, collapsed transformations, alpha, effects | Start by switching to Active Camera/Front as appropriate and temporarily bypass suspect mattes/effects. |
| **Layer is black where it should be transparent** | Transparency Grid is a *preview aid*; comp background color isn't alpha, solid-color source may be opaque | Toggle Transparency Grid and inspect **Alpha channel**, not simply black RGB. Choose output format with alpha if needed. |
| **Background is black after export** | Codec/container doesn't preserve alpha; transparency interpreted as black in player | Use ProRes 4444 or appropriate PNG/EXR sequence with **RGB+Alpha** and correct alpha interpretation. Standard H.264 MP4 doesn't export an alpha channel. |
| **Matte isn't working** | Track Matte source assignment, Alpha vs Luma, invert, source visibility, matte layer alpha/brightness | Select the **matted layer**, choose its **matte source** with pick whip/dropdown. Matte source may be elsewhere in layer stack. |
| **Some effects appear missing** | Effects & Presets search, installed plugin, effect hidden in Obsolete group, renderer or bit-depth limitations | Confirm exact effect name/category; inspect supported 8/16/32-bpc and GPU icons, then verify plugins. |
| **Can't see keyframes** | Property is collapsed, layer not selected, Timeline search filter, expression-only animation | `U` shows keyed properties; `UU` modified properties; `EE` expressions. Clear Timeline search. |
| **`J/K` seems to skip keyframes** | Relevant properties are not exposed | Reveal keyframes with `U` and clear search/filter. |
| **Shortcut doesn't work** | Wrong panel focus; text cursor active; laptop Fn mapping; OS intercept; custom shortcut | Click correct panel; press `Esc` to leave text edit; check **Edit → Keyboard Shortcuts**. |
| **`G/Q` draws a mask instead of a shape** | Existing footage/solid layer selected vs no layer selected; Tool Creates Shape/Mask setting | Deselect layers for a new shape layer; deliberately choose Shape or Mask mode. |
| **Cannot edit parametric rectangle vertices** | Procedural Rectangle Path rather than Bézier path | Convert shape path to **Bézier Path**, then select Path and vertex. Preserve parametric form if you still need width/corner sliders. |
| **Path pasted into Position doesn't follow correctly** | Wrong path/property selection; position separation; space transforms; dimensions | Copy actual Path property, paste on Position with playhead at desired time, inspect spatial path. |
| **Auto-Orient flips or points the wrong way** | Original artwork direction, tangent, parent transform, spatial path | Use Auto-Orient Along Path and rotate source art/precomp for desired leading direction. |
| **3D object is flat / no depth** | 3D switch vs geometry extrusion, render engine, mesh type, camera/light | A 3D switch creates a plane; actual geometric depth requires supported geometry/renderer and property controls. |
| **Light has no effect** | Layer material settings, 3D renderer, light type, lighting switch or blend style | Verify compatible renderer and Accepts Lights/material properties. |
| **Motion blur doesn't show** | Layer motion-blur switch, comp master motion-blur switch, shutter angle, type of animation | Enable both switches and verify shutter angle/samples; FX blur may behave differently. |
| **Roto Brush crawls or drifts** | Propagation, segmentation boundary, view resolution, clip cuts, occlusion, strokes too broad | Work close to source/full resolution, make small corrections, propagate in short spans, refine and freeze. |
| **Object Matte has errors at edges** | Fine hair, transparent/glass objects, motion blur, similar background colors | Add precise manual correction, holdout masks and edge refinement; check many frames not only one frame. |
| **Green-screen subject looks transparent** | Too aggressive Clip Black/White, Shrink/Grow, despill, suppression | Inspect Keylight **Screen Matte**; aim for clean black background, white subject and sensible grey edges. |
| **Roto/keyer edges have a halo** | Premultiplied RGB contamination, poor despill, matte chatter, uncorrected edge colors | Check straight/premultiplied interpretation, use edge color decontamination/despill, slight blur/choke only if needed. |
| **Animation seems to speed up unpredictably** | Unequal keyframe timing, nonlinear Graph Editor easing, path distances, frame rate | Examine **motion-path dots**, timing, speed graph and roving keys. Equal time between keys ≠ constant spatial speed. |
| **Keyframes won't move off frames** | Timeline snapping / graph options | **Graph Editor → Choose Graph Type and Options → Allow Keyframes Between Frames** (when supported). Rendering still samples actual frame times. |
| **Badly lagging preview** | Large footage, many layers, effects, GPU memory, disk cache, insufficient RAM, compressed media | Start with 1/4 preview and shorter work area; disable heaviest effects; test transcoded sources; inspect cache/disk. |
| **Audio drifts out of sync** | Variable frame rate footage, interpretation mismatch, mixed sample rates, time stretching | Convert VFR footage to constant frame rate; confirm comp rate, audio 48 kHz where appropriate; inspect source timing. |
| **Type animation breaks across machines** | Missing font or font-axis support, font version mismatch, plugin effects | Package font/license details, verify exact font and open a test copy on target machine. |
| **Render different from preview** | Work area vs entire comp; proxy; guide layers; 3D/camera, output color/bit depth; motion blur/sample settings | Render a short test with the intended output module and compare RGB/alpha/color. |
| **Render fails / hangs** | Corrupt frame, GPU driver, unsupported plugin, insufficient disk space, bad source segment | Narrow to failing frame/time, disable suspected effect, render a short image sequence, review log, update plugin/driver responsibly. |

## 7.3 Color management: the 90-second decision tree

```text
What is the source?
  ├─ Ordinary display graphics / web clips
  │    → Identify likely Rec.709/sRGB source and output; avoid unnecessary conversions.
  ├─ Log camera footage
  │    → Confirm camera profile; transform with correct camera input/OCIO/LUT,
  │      grade in a chosen working space; transform to delivery space.
  ├─ Scene-linear EXR / CGI passes
  │    → Use suitable linear/HDR float workflow; preserve highlights and EXR channels.
  └─ Mixed sources and VFX pipeline
       → Define project working space, interpret each source,
         check ACES/OCIO configuration, and validate render transform.
```

**8 bpc vs 16 bpc vs 32 bpc:** This is **working precision per channel**, not simply the camera recording bit depth; it doesn't make an 8-bit file secretly contain 10-bit detail. **16-bpc** can reduce banding in many workflows; **32-bpc float** allows extended linear/HDR arithmetic where supported. It costs more processing and memory. For color-critical work check actual source interpretation, effect bit-depth support, working-space conversions and delivery transform. **A 10-bit camera recording is not “unsupported” because there is no 10-bpc comp toggle.**

**Viewer background vs alpha:** `Ctrl+K → Background Color` controls composition's preview background color; it does not make the transparency region opaque in an alpha-capable render. The checkerboard comes from the **Toggle Transparency Grid** control; its appearance is separate.

## 7.4 Pre-render/export choices

| Delivery | Usual starting point | Note |
|---|---|---|
| Web / review without transparency | **H.264 in MP4**, directly in native Render Queue or through Adobe Media Encoder | Native H.264 export is supported in modern AE; check bitrate/color/audio. |
| Intermediate editing master | **ProRes 422** / DNxHR (supported environments) | Easier to seek than long-GOP footage; no alpha in standard ProRes 422. |
| Motion graphics with transparency | **ProRes 4444 (RGB+Alpha)** or **PNG sequence** | Alpha must be enabled explicitly; check straight vs premultiplied. |
| VFX compositing / HDR | **EXR image sequence** | Preserves float precision and relevant channels; plan disk usage. |
| Backup for crash-prone/very expensive effect chains | **Image sequence** | Resumes/replaces individual bad frames more easily than a single long movie file. |

**Two-step delivery is optional:** render a high-quality intermediate then encode H.264, especially for client revisions or multiple deliverables. For many projects it is perfectly reasonable to export H.264 directly from AE's Render Queue.


## 7.5 Diagnose the exact slow operation

Click the **snail icon** in Timeline, or right-click its column headings → **Render Time**. Expand a slow layer to inspect individual effects and masks; open a nested comp to investigate its contents. Compare the same frame at the same resolution with Adaptive Resolution off. Cached timings include cache retrieval and can appear much faster than an uncached render. An asterisk identifies cache use. A single frame does not represent every frame in a particle or roto shot. [Adobe: Composition Profiler](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/composition-profiler/composition-profiler.html).

### Four things commonly called “cache”

| Mechanism | What it helps with | Practical consequence |
|---|---|---|
| RAM preview frames | Immediate playback of computed frames | Edits and memory pressure invalidate/evict results. |
| Disk cache | Reusing computed frames from storage | A fast drive and available space help; repeated purging removes useful work. |
| Lossless compressed disk frames | Storing more cached frames in the same space | This is cache storage compression, not an export quality setting. |
| Object Matte propagation cache | Reusing analyzed matte results | 26.5 persists these results to disk across sessions; cache is not project backup. |

**Compressed frames:** **Edit → Preferences → Disk → Disk Cache → Enable Compressed Frames (Lossless)** controls the storage feature. Disable it as a targeted diagnostic if a cache-related problem warrants it; it is ordinarily enabled. [Adobe: lossless playback cache](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/lossless-compressed-playback.html).

## 7.6 An installed effect can still appear missing

Open **Effect → Manage Effects**. Check **Enabled**, version, path and **Last Crashed**. An effect disabled there disappears from effect menus/search and appears Missing in projects that use it. Reenable only after addressing the relevant plugin/version issue, then restart AE. The manager's disable action applies at startup; it differs from the `fx` bypass switch on one effect instance. After a crash, AE may offer to disable a suspect effect or start in Safe Mode. [Adobe: Effect Manager](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effect-manager.html).

## 7.7 Select and inspect complex projects efficiently

In the Comp viewer, right-click an obscured object → **Select → layer name**. Use **Select Label Group** to gather related layers or **Select Children** on a parent to add its children. With **Hide Shy Layers** enabled, Timeline Select All excludes hidden shy layers; verify selection scope before bulk edits. These selection methods complement `Ctrl+Up/Down` and numbered-layer selection. [Adobe: layer selection](https://helpx.adobe.com/after-effects/desktop/work-with-layers/select-and-arrange-layers/selecting-arranging-layers.html).

If Copy seems to copy only an effect or keys, deselect internal components with `Shift+F2` while retaining the layer, then copy again. Inspect selection before deleting, duplicating or pasting. A layer selected with a selected property is a different editing context from a layer selected on its own.


## 7.8 Preview a small area at useful detail

Enable **Region of Interest** at the bottom of the viewer and draw a rectangle around the difficult edge or effect. Drag handles to resize; `Shift+drag` a corner preserves its proportions. `Alt+click` the ROI button starts a fresh selection. Toggle ROI off to inspect the full scene.

ROI limits preview work; it does **not normally crop final output**. **Composition → Crop Comp to Region of Interest** changes the canvas. The Output Module's **Crop → Use Region of Interest** crops a rendered output. These are separate decisions. [Adobe: ROI and previewing](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/previewing.html).

## 7.9 Check color with scopes

Use **Window → Lumetri Scopes** alongside Lumetri Color or Curves. Waveform helps inspect brightness levels, RGB Parade compares channels, and Vectorscope shows hue/saturation distribution. Set the scope interpretation for the intended SDR/HDR workflow and check the actual output transform. Viewer exposure is an inspection control; a correction effect changes rendered pixels. [Adobe: HDR, bit depth and scopes](https://www.adobe.com/us/learn/after-effects/web/hdr-color-depth).

---
# 08 — Practical Recipes (Copy the Method, Not Just the Shortcut)

> [!tip] The recipe format
> **Goal → Setup → Steps → Quality check.** Try each recipe with throwaway shapes and 5-second compositions before using it on a client project. A reproducible effect stack teaches more than memorizing a menu name.

## 8.1 Smooth logo or shape reveal with an Alpha Matte

**Goal:** Show a moving text/logo only inside a specific shape.

1. Make text/logo **T** and an opaque rectangle **R** as separate layers. Position **R** as the reveal region.
2. On **T** (the layer to be *revealed*), locate **Track Matte** in the Timeline. If missing, use `F4`, then inspect the panel columns.
3. Use the Track Matte pick whip on **T** to choose **R**. Set **Alpha** (use the rectangle's opacity/alpha) and leave **Invert** off.
4. Animate **T** Position from outside to inside the rectangle and add easing (`F9`); optionally animate matte **R** independently.
5. **Invert** to show text only *outside* the rectangle. **Luma** to use brightness rather than actual transparency.

**Quality check:** The matte layer need not be adjacent to the target, and can be used more than once. A matte may be hidden as an independent normal render while continuing to define alpha; toggling its visibility should be done deliberately, not by relying on old pre-2023 behavior.

## 8.2 Footage inside an animated silhouette

**Goal:** A moving person appears to be made of rainbow video/abstract texture.

1. Use **Roto Brush** or **Object Matte** to cut out the person in layer **P** (verify the person is opaque and the rest transparent).
2. Put animated texture or video on layer **F**.
3. Choose **P** as **F**'s Track Matte; choose **Alpha**. **F** is the layer being cropped, so **F** owns the Track Matte setting.
4. Keep the motion of the texture independent of the matte; precompose either result if you need to animate or glow the finished composite as one item.

**Variation:** Choose **Alpha Inverted** for a person-shaped hole through the footage. Choose **Luma** for a brightness-based silhouette, such as a smoke/gradient-driven reveal.

## 8.3 Clean green-screen composite with Keylight

**Goal:** Remove chroma screen without cutting away hair, translucent clothing or fine details.

1. **Prepare:** check camera codec and source-color interpretation. If the screen is uneven, create a loose **garbage mask** to exclude irrelevant off-screen areas.
2. Apply **Keylight (1.2)** to the foreground; choose **Screen Colour** using a representative green or blue region. The eyedropper's sampling behavior may vary by effect and tool; inspect matte rather than assuming a modifier gives an ideal average sample.
3. Set **View → Screen Matte**. Aim for **solid white inside subject**, **black outside**, with intentional grey on hair/partially transparent edges. Adjust **Clip Black / Clip White** in small increments. Don't automatically push Clip Black into the 90s; excessive thresholds destroy useful edge detail.
4. Fine-tune **Screen Pre-blur** or **Screen Shrink/Grow** sparingly; soft edges should arise from the real scene and shutter, not just an arbitrary blur.
5. Use **Key Cleaner** / **Advanced Spill Suppressor** where needed. **Intermediate Result** is useful in a multi-effect keying pipeline (e.g., Keylight → Key Cleaner → Advanced Spill Suppressor); the Keylight **View** setting can affect intermediate output, so confirm your intended pipeline's setting rather than treating it as cosmetic only.
6. Put a real background below, zoom to 100%, and check a **moving** preview. Apply color/grain/lighting integration after extracting the subject.

**Avoid:** multiple heavy Keylight instances, legacy **Color Key** and severe choke by default. Add another keyer or use inside/outside matte logic only when the actual screen needs it. Many green-screen shots key cleanly with **one** well-tuned instance and good spill removal.

## 8.4 Trace motion to an arrow, label or light

**Goal:** Have a graphic travel along a custom curved path.

1. Draw a path (mask path or shape path). Select the actual **Path** property and copy (`Ctrl+C`).
2. Select target layer's **Position** property, put CTI where motion should start, and paste (`Ctrl+V`). AE generates Position keyframes along the path.
3. Choose **Layer → Transform → Auto-Orient → Orient Along Path** (`Ctrl+Alt+O` opens Auto-Orient). Source art may need an initial rotation so the arrowhead points forward.
4. Use Graph Editor `Shift+F3`, and adjust keyframe timing or easing (`F9`). Examine motion path dots: **widely spaced dots = greater instantaneous motion**, close dots = slower motion.

**Alternative:** **Layer → Create → Null Tracing Along Path**. Animate the generated **Progress** control to move a null, then **parent** the arrow to the null. This is especially useful for ongoing edits to the path.

## 8.5 Turn static handwriting into a write-on animation

**Option A — most practical for logos/lettering:** Draw a shape stroke along the lettering, then on the stroke group **Add → Trim Paths**. Animate **End** from **0% to 100%**. To reveal a polished logo, use the animated stroke as a **track matte** over the final artwork; size the stroke to cover glyph width.

**Option B — record strokes as you draw:** Create a solid, open it in the **Layer panel** (not just Composition viewer), choose **Brush Tool** (`Ctrl+B` cycles through paint tools), open **Paint** panel and set **Duration → Write On**. Paint directly over time. Turn on **Paint on Transparent** if you want just the painted strokes, and use `UU` on the painted layer to reveal modified paint-stroke properties. Brush drawing in the Composition panel is not the same as paint in the Layer panel.

**Quality check:** Stroke order, duration and gaps are all visible at normal playback speed; re-time individual strokes if a word should look truly handwritten.

## 8.6 Animate wavy hair, flags, smoke or water using Wave Warp

1. Create a narrow solid/shape or masked strip representing the object; for **hair**, taper or distort it to an organic silhouette; for a **flag**, keep the anchored edge where the flagpole is.
2. Add **Wave Warp**; lower **Wave Height** for subtle motion and adjust **Wave Width** for wavelength. Set **Direction** and **Wave Speed**.
3. Use **Pinning** (for example pin at the flag's attached edge) to keep one edge steady. For hair, use a narrow masking profile and moderate movement.
4. For layered smoke, duplicate the strip, use different speeds/widths and small time offsets; apply **Roughen Edges**, Blur, **Turbulent Displace** or **Bezier Warp** sparingly for a less mechanical outline.
5. For a repeatable cycle, animate compatible effects with an intentional periodic speed/duration, precompose and loop the resulting pre-rendered segment if necessary. The source animation must **already be seam-free** before a loop expression or duplicated clip will hide a jump.

**Alternative:** **Puppet Pin** for anchored hair/cloth, **CC Particle World** for a particulate smoke look, **Curl Noise** for fluid background motion. Effects have different physics; pick the method that best fits the visual task.

## 8.7 Turn a static image into convincing faux-3D motion

**Layer-separation approach (usually cleanest for parallax):** Duplicate the still in Photoshop or another layer editor; isolate foreground, midground and background; **inpaint the missing background** where the subject used to be. Import as a layered PSD/composition, set cards to 3D, separate their **Z** values and animate a camera a short distance. Slight depth variation creates perspective, but too much exposes stretched edges.

**Depth-map distortion approach:** Generate a grayscale depth estimate where near/far values are consistent and documented. Add **Displacement Map** to the original image; point it to the depth layer; choose a channel and modest Max Horizontal/Vertical Displacement. Animate those values or the depth map. Apply a soft **Fast Box Blur/Gaussian Blur** to noisy depth boundaries when needed. This is **2D pixel displacement**, not true 3D geometry and cannot plausibly reveal unseen surfaces.

**Actual 3D approach:** With appropriate hardware, supported 3D source and engine, use **Advanced 3D** and supported 3D mesh/displacement/DOF capabilities. A model with geometry can handle camera parallax more convincingly than a flat still.

## 8.8 Depth-map wipe (reveal near/far features in order)

1. Put the **incoming image/video** above the outgoing image.
2. Make a grayscale depth map corresponding to the intended reveal; white vs black determines wipe timing, depending on effect configuration.
3. Add **Gradient Wipe** to the incoming layer; set **Gradient Layer** to that depth-map layer and choose the desired source/effects option.
4. Animate **Transition Completion**. Adjust **Transition Softness** to avoid brittle boundaries. Invert the map if the reveal starts in the wrong depth zone.
5. If creating a seamless reveal, match the two images' color, alignment, motion and subject contours; a depth map is not a true 3D occlusion solver.

**Tip:** A blurred depth map often makes a smoother reveal. For a graphic instead of a depth-aware wipe, use a simple animated Luma Matte—it is easier to art-direct.

## 8.9 Audio-reactive pulse, shake or scale

1. Import WAV/AIFF/audio footage. Choose **Animation → Keyframe Assistant → Convert Audio to Keyframes** on the audio layer.
2. AE creates an **Audio Amplitude** null containing left, right and **Both Channels** slider keyframes. Select it and `UU` to reveal modified sliders.
3. On a logo's **Scale** property, `Alt+click` stopwatch, then enter:

```js
amp = thisComp.layer("Audio Amplitude").effect("Both Channels")("Slider");
pulse = linear(amp, 0, 25, 95, 125);
[pulse, pulse];
```

4. Adjust the **input range `0–25`** to suit the actual slider values; they vary by recording and level. For punchier beats, consider smoothing or clamping (`clamp`) and layer separate frequency bands via proper audio processing.

**To create an audio line around a circle:** Add **Audio Spectrum** on a solid, assign **Audio Layer**, draw an ellipse **mask** set to `None`, then select that mask in the effect's **Path** control. Shape-layer paths aren't automatically usable as mask-path choices; create/copy an actual mask path.

## 8.10 Neon outline around a tracked subject

1. Extract subject alpha via roto/matte; duplicate foreground into a clearly named **Outline Source** precomp.
2. Generate perimeter path with **Layer → Auto-trace** (choose Alpha and intended work area) on material with a clean alpha. On a moving subject, check number of auto-traced masks: complex cases create many paths.
3. Apply **Stroke** effect to a selected mask or animate a dedicated shape stroke from copied path data. Add **Glow** after Stroke; optionally **Roughen Edges / Turbulent Displace** before Glow.
4. Review line path accuracy frame by frame. Auto-trace on moving noisy footage can generate unwieldy paths; a simplified manual contour or matte-edge extraction can look cleaner.

**Alternative:** Duplicate matte layer → **Find Edges** / **Minimax** / **Simple Choker** to isolate an edge, colorize then glow. Render cost and edge flicker vary by approach.

## 8.11 Tracking a graphic onto a wall or screen

**Flat, perspective-moving planar surface:** Use **Mocha AE** planar tracking. Select the wall/screen region, track translation/scale/rotation/shear/perspective, export **Corner Pin** / supported transform data to the inserted graphic. Precompose the graphic at the intended screen dimensions, then add blur/grain/reflections and occlusion to integrate it.

**Single moving point:** Use **Track Motion**, choose Position (and rotation/scale where useful), set **Edit Target** to a Null and **Apply**. Parent your graphic to the Null. This is not necessarily accurate for perspective tilts; switch to a planar/Corner Pin track.

**Moving camera through a real 3D space:** Try **3D Camera Tracker**; pick a stable plane, create tracking solid/null and camera, orient design in space. It needs enough trackable parallax and can fail with pure zoom, motion blur or moving foreground.

## 8.12 Stabilize a dancer's face while the world moves

1. Choose a readable patch around the head and **Tracker → Stabilize Motion**. Position alone is enough for basic stabilizing; add Rotation/Scale for significant camera changes.
2. Track forward and backward as needed; inspect the motion track and manually repair bad frames.
3. **Apply** and verify which layer is actually transformed. Stabilization moves the **whole shot** to hold the target still and may expose border gaps.
4. Enlarge the stabilized layer slightly, crop or fill edge regions. For subject-specific stabilization, mask/roto and independent background treatment may be needed.

**Important:** Stabilize Motion is different from **Warp Stabilizer VFX**, which estimates a broader motion field and may warp the image. Use the least destructive method that solves your shot.

## 8.13 Masks with per-vertex feathering

1. Select a layer and draw/choose a mask (`G`). `M` reveals Mask Path; `MM` expands all mask properties; `F` shows Mask Feather.
2. Choose **Mask Feather Tool** from the Pen Tool group. Add **feather points** at needed locations along the edge; pull them outward/inward to control local feather width.
3. Use broad feather at soft hair and little/no feather near a crisp object edge. Combine with global **Mask Feather** carefully rather than assuming all edges need a uniform amount.
4. Check scale and zoom: small mask deviations can look severe in final-size delivery.

## 8.14 Shape gradients, outlines, star points and morphs

- **New shape or new mask?** With **no layer selected**, `Q`/`G` typically creates shape content. With an existing compatible layer selected, shape/pen tools often create **masks**. Use the toolbar's Shape/Mask option intentionally.
- **One shape layer can contain many paths/groups:** Expand **Contents**, add multiple groups/paths/fills/strokes and use each group's **Transform** to animate independently. Master **Transform** affects the entire shape layer.
- **Parametric vs Bézier:** Rectangle/Ellipse/Polystar Path provides live size/roundness/points properties; **Convert to Bézier Path** for manual vertex manipulation. Converting sacrifices some procedural controls.
- **Filling with gradient:** Choose Gradient Fill/Stroke, alter start/end points in Composition viewer and fine-tune stops in the gradient editor. Holding **Alt** while clicking the Fill/Stroke selector cycles permitted types in supported versions.
- **Star during draw:** Hold mouse down, press **Up/Down** to change points; use **Shift** to constrain. Other drawing modifiers are in [[#03 — Complete Shortcut Reference]].
- **Morph one shape to another:** Two Bézier paths should use compatible vertex counts and vertex order. Animate **Path**; AE interpolates corresponding points. For unrelated outlines, redraw/reorder paths or use a different transition.

## 8.15 Freeze, loop and reverse correctly

| Goal | Method | Warning |
|---|---|---|
| Freeze a video at one frame | Position CTI → **Layer → Time → Freeze Frame** | It affects source time at the chosen frame. |
| Hold a frame then resume footage | **Layer → Time → Enable Time Remapping**; add/modify time-remap keys to create a hold interval | Watch for an unwanted duplicate last-frame/off-by-one key. |
| Reverse footage | **Layer → Time → Time-Reverse Layer** (`Ctrl+Alt+R`) | Reverses the media, distinct from reversing keyframes. |
| Reverse selected animation | **Time-Reverse Keyframes** (Keyframe Assistant) | Doesn't reverse source footage. |
| Paste keyframes in reversed sequence | Copy selected keys → **Paste Reversed Keyframes** (`Ctrl+Shift+V`) | Check current-time insertion point. |
| Loop imported clip at source level | Project panel → **Interpret Footage → Main → Loop** count | Source-level repeat; not an expression on the footage layer. |
| Loop a keyed property | Set at least two keys → expression `loopOut("cycle")` | Applies to properties, not to arbitrarily trimmed video playback without time remap. |
| Boomerang keyed property | `loopOut("pingpong")` | Reverses interpolated keyframe motion each cycle. |

## 8.16 360° imagery and immersive footage

**Sphere-to-plane viewing:** Put an **equirectangular** 360° still/movie in a comp and use **VR Sphere to Plane**, or the appropriate immersive-video converter. Choose field of view/yaw/pitch/roll as needed. **CC Environment** is a different approach to project the image into an environment with an appropriate AE camera; it isn't exclusively for stills. **VR Comp Editor** workflows/scripts can set up a convenient editing layout depending on installed version. For effects spanning the 360° seam, use **VR-aware effects** such as VR Glow/VR Blur rather than a 2D effect that creates a visible discontinuity.

## 8.17 Build consistent animated callouts using Essential Properties

1. Make a source comp **CALL_OUT_MASTER** containing text, shape box, accent color and entrance animation.
2. Open **Essential Graphics** and expose supported controls (Source Text, color, slider, checkbox, etc.).
3. Nest the master into individual comps, then change exposed **Essential Properties** **per instance** while keeping shared animation from the master.
4. Parent the overall callout to a tracked Null if it must follow footage.

This allows one animation to drive many variations. Essential Properties are **not** a magical substitute for expressions, parenting or individual instance-specific content; expose only the properties a downstream editor needs.

---
## 8.18 Professional AE camera basics: one-node, two-node and orbit rigs

**Goal:** orbit a 3D subject, cut between camera angles and animate a controlled rack-focus shot without fighting the camera's point of interest.

**One-node camera:** Controls Position and Rotation/Orientation directly like a camera mounted on a rig. It is great for intentional cinematography, handheld movement, saved camera animation and first-person perspectives. It does **not** have a separate Point of Interest handle. **Two-node camera:** Includes a **Point of Interest** that controls where the camera aims; useful for keeping a specific object framed. Neither option is inherently superior for every task.

**Easy orbit rig:**

1. Add 3D subject; create camera (**Ctrl+Alt+Shift+C**). Use a two-node camera if you want a visible Point of Interest.
2. Choose **Layer → Camera → Create Orbit Null** (when available in your AE version), or create a Null **Ctrl+Alt+Shift+Y**, enable its **3D** switch, place it at the object center and **parent the camera to the Null**.
3. Keep the camera some distance back on its local Z axis, then rotate the **Null** around Y to orbit the subject. Keyframe Null Y Rotation instead of needlessly keyframing both camera XYZ and Point of Interest.
4. With camera active, **C** cycles Camera tools as available; **1, 2, 3** switch camera orbit/pan/dolly controls in supported 3D navigation contexts. **Ctrl+Alt+Shift+\** moves selected camera and Point of Interest to view selected 3D layers.
5. A camera can also be **parented to multiple nested Nulls** to separate orbit, height and shake, but label them clearly and avoid needless transform complexity.

**Change viewpoints mid-shot:** Create two (or more) Camera layers with intentionally different views. AE chooses the active camera according to layer visibility and timing/stack behavior; arrange camera layer in/out points so only the intended camera governs a given moment. **Alt+]** trims selected camera's out point to CTI. Put the new camera's start at the cut time. Camera cuts are immediate unless you animate or blend via an appropriate strategy.

**Focus for depth of field:** On a camera with supported renderer/camera settings, enable DOF and adjust **Focus Distance** and **Aperture**. You can use **Layer → Camera → Link Focus Distance to Point of Interest**, **Link Focus Distance to Layer**, or **Set Focus Distance to Layer** for precise focus control. **Link** keeps expression-based tracking; **Set** calculates the current distance without persistent tracking. The modern Advanced 3D renderer supports in-engine depth of field in AE 26.3+; verify renderer-specific blur controls, quality and supported geometry.

**Physical lens intuition:** Shorter focal length = wider framing **if camera position and film size are held constant**; longer focal length = tighter framing. Moving camera position changes perspective/parallax; merely zooming focal length does not. A camera's **f-stop** series is approximately f/1.4 → f/2 → f/2.8 → f/4 → f/5.6: **one** stop from f/1.4 to f/2 means **half the light**, **two** stops from f/1.4 to f/2.8 means **one-quarter the light** (for a real camera with other settings held fixed). In AE's simulated DOF, aperture/blur controls create the visual effect; don't assume exposure automatically changes exactly like a physical camera.

**QC:** Check **Active Camera** view rather than judging final staging solely from a custom 3D view. In a shot with multiple camera layers, verify cuts at the frame before, at and after each transition. If a camera appears to look away unexpectedly, examine **Point of Interest**, orientation, parent transforms and the active camera layer.

## 8.19 Troubleshoot an effect by showing its deformation on a grid

1. Create a comp-sized solid and apply **Generate → Grid**. Set a visible grid spacing with contrasting colors.
2. Put a suspect **Distort** effect (Wave Warp, Bezier Warp, Turbulent Displace, Displacement Map, Liquify, etc.) **below the Grid effect in that layer's Effect Controls stack** so it affects the grid pixels generated above it. AE effects process in stack order.
3. Change one parameter at a time and observe which grid lines bend/shift. Switch to an ordinary image afterward, keeping effect values identical.
4. For effects with layer-space vs composition-space mapping, test the same grid at different layer sizes, precomp boundaries and collapse-transform settings. This helps isolate why the edge/bounds of a clipped glow, blur or displacement look wrong.

This 30-second diagnostic is often faster than guessing how an effect remaps the image.


## 8.20 Soft merging blobs from simple shapes

Put two same-colored circles on transparency, animate them approaching, and precompose them together. Apply **Gaussian Blur** or **Fast Box Blur**, then **Levels** targeting **Alpha**. Bring the input black/white levels closer to tighten the softened alpha into a merged silhouette. Add Fill afterward if you want one clean color. Adjust blur and thresholds together; an opaque background prevents the intended alpha-union effect. This is a stylized merging-shapes technique, not recovery of lost image detail.

## 8.21 Morph an image using Reshape

On the image being distorted, draw closed **source** and **destination** masks and optionally a larger **boundary** mask. Set mask modes to **None**. Apply **Reshape**, assign each mask explicitly, and animate **Percent** from 0 to 100. With the effect selected, **Alt-click** the mask to add correspondence points; map distinctive features to their counterparts to reduce twisting. Use the least costly Elasticity that gives a clean deformation.

To morph image A into image B, make complementary deformations on separate layers and crossfade around the matching intermediate form. A single Reshape instance warps pixels; it does not generate the second image's texture. [Adobe: Reshape](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/distort-effects.html).

## 8.22 Make a selective-focus or miniature look

Create a grayscale map with a dark focus band and lighter surrounding regions. A feathered mask plus **Solid Composite** can build a full-frame map without transparent gaps; a gradient is another option. Assign it in **Camera Lens Blur → Blur Map** and select **Effects & Masks** for the map input when the map is generated by effects. Animate **Blur Focal Distance**, adjust Blur Radius, and inspect transitions across object edges. For a miniature look, start with a suitable elevated shot and a narrow focus band; extra saturation is a creative choice, not a requirement.

## 8.23 Small effect-stack experiments

| Goal | Starting stack | What to watch |
|---|---|---|
| Graphic contour background | Fractal Noise → Blur → Posterize → Find Edges → Tint/Fill | Animate Evolution, then tune Scale/Contrast to the composition; no fixed numbers work for every size. |
| Wireframe-looking globe | Grid on a solid → CC Sphere | Rotate inside CC Sphere; it remains an effect-rendered sphere rather than an editable mesh. |
| Light sweep across a logo | CC Light Sweep → optional Glow | Animate the sweep center and preserve the logo's alpha. |
| Colored image trails | CC Wide Time → Tint → CC Composite | Inspect RGB Only/alpha and effect order; the composite brings back source detail. |
| Drips or smoke forks | Wave Warp / Liquify / CC Smear, then localized warping | Choose the tool according to the deformation; animate the controls that actually create motion. |
| Highlight bloom | Extract → Glow on a duplicate above the original | Preserve useful detail and inspect the blend over dark and bright regions. |

Try these on short test comps. Combine effects by what their inputs and outputs mean; stacking more effects does not automatically improve a shot.

## 8.24 Build a controlled RGB split

For an opaque RGB source, duplicate it into three aligned layers. Apply **Shift Channels** so each keeps one of R, G or B and sets the other two to **Full Off**. Use **Add** on the upper two to recombine the channels, then offset them slightly with Transform/Offset for the desired fringe. Inspect edges: Offset wraps source pixels and can create an obvious seam. With transparent artwork, precompose the result and restore/control alpha deliberately so additive compositing does not unintentionally expand opacity.

---

# 09 — Expressions and Data-Driven Animation

## 9.1 The minimum working model

An **expression** is a JavaScript-like formula evaluated for **one property at a given time**. It can calculate, link or modify a value, using time, other properties, audio-derived keyframes or imported data. It does **not** permanently bake motion until you convert it to keyframes.

1. Select property (Position, Scale, Opacity, effect Slider, Source Text...).
2. **Alt+click** its **stopwatch** to enable an expression; this opens the expression editor. If an expression already exists, blindly Alt+clicking can remove/toggle it—expand the property first.
3. Enter formula. Click away to commit; check warning/error messages at bottom of comp/expression field.
4. Reveal expressions on selected layers with **`EE`**. To disable temporarily, use the expression enable/disable switch rather than deleting the code.
5. To bake: choose **Animation → Keyframe Assistant → Convert Expression to Keyframes** on the expression-controlled property; inspect generated keyframes and frame rate.

**Value dimensionality matters:** Opacity/Rotation expect one number. 2D Scale expects `[x,y]`; Position can expect `[x,y]` or `[x,y,z]`. An expression returning a single number on a 2D Position property is an error.

## 9.2 Expression mini-reference (copyable)

| Task | Expression | Explanation |
|---|---|---|
| Spin continuously | `time * 90` | 90 degrees per second; put on Rotation. |
| Offset a property by 50 | `value + 50` | Add to the property's existing pre-expression value; scalar properties only. |
| Make an object float | `wiggle(1, 35)` | Approx. one wiggle per second; magnitude around 35 property units. |
| Loop between keyframes | `loopOut("cycle")` | Repeat animated property after last keyframe. |
| Ping-pong animation | `loopOut("pingpong")` | Run forward, then back. |
| Hold-style random jitter | `posterizeTime(8); wiggle(10, 20);` | Sample expression only 8 times per second. |
| Replace values via a range | `linear(value, 0, 100, 20, 200)` | Maps the range and clamps to its endpoint values outside 0–100. |
| Bound a parameter | `clamp(value, 0, 100)` | Restricts the value to the stated minimum and maximum. |
| Ease parameter across range | `ease(time, 0, 2, 0, 100)` | Smoothly progress 0 to 100 over first two seconds. |
| Freeze value at one second | `valueAtTime(1)` | Samples that property at one second. |
| Get comp center | `[thisComp.width/2, thisComp.height/2]` | Useful on Position of unparented 2D layer. |
| Get another layer's Position | `thisComp.layer("Controller").transform.position` | Directly uses Controller's Position property; check coordinate spaces. |

**Bouncing without an expression:** Add three manually keyed Scale values (e.g., 100 → 120 → 100), apply `F9` and reshape graph. This is easier to maintain than a complex spring expression for a single bounce.

### Example: follow another layer with a local offset

On a **2D Position** property:

```js
leader = thisComp.layer("MAIN_LOGO");
p = leader.transform.position;
[p[0] + 80, p[1] - 20];
```

**Warning:** Simple Position links assume matching coordinate/parent spaces. If layers have different parents or one is 3D, use explicit coordinate conversion (`toComp`, `fromComp`, etc.) or parent to a shared Null. **Copy With Property Links** is convenient but creates actual dependency expressions that depend on their source layers remaining available. AE can update many name references during renaming; deleting sources or moving a rig between projects still needs care.

### Example: auto-size a rectangular callout behind text

On a rectangle **Size** property inside a shape layer, if both graphics live in the same simple untransformed context:

```js
textLayer = thisComp.layer("CAPTION");
r = textLayer.sourceRectAtTime(time, false);
padX = 50;
padY = 24;
[r.width + padX, r.height + padY];
```

For complex text alignment, track the text rectangle's **left/top** offset and adjust the shape group's **Position**; `sourceRectAtTime()` sizes from glyph bounds, not always the text-layer anchor. Put background and text in the same precomp or carefully convert coordinates when sharing across comps.

### Example: automatic sequential delay across multiple layers

On **Opacity** of numbered/ordered layers, with 4-frame steps in a 25-fps comp:

```js
delay = (index - 1) * framesToTime(4);
t = time - delay;
ease(t, 0, 0.35, 0, 100);
```

This is for a simple opacity entrance; the *layer index* changes when layers are reordered. Prefer a Slider or `marker`-driven design for robust commercial templates, or use **Quick Offset** if you just need keyframe-based staggering once.

### Example: looping a keyed property

On a property with keyframes defining a full animated cycle:

```js
loopOut("cycle");
```

To loop footage instead, enable **Time Remapping**, make the last usable frame and first frame match properly, and loop the **Time Remap** property. A common off-by-one issue occurs because the default last Time Remap key can correspond to the source duration **after** the last visible frame—remove/adjust it before looping. For continuous repeating textures, an **animated seamless source** is still the foundation.

## 9.3 Expression controls as a clean interface

A **Null** with **Effect → Expression Controls → Slider Control** acts as an animator-friendly master control. Give effects explicit names like `Reveal Duration`, `Orbit Speed`, `Background Strength`, `Highlight Color`. Then other layers can reference them with the expression pick whip. This is easier to debug than burying random constants across multiple effects.

**Dropdown text in 2026:** an eligible dropdown property exposes `.text`, `.items` and `.textAtTime(t)`. Use these to display labels without maintaining a duplicate array. Numeric menu values begin at **1**; `.items` is a JavaScript array beginning at **0**. Drive portable rig logic with numeric values when localized labels could differ.

**Cross-comp linkage:** `thisComp.layer("...")` addresses the current composition. `comp("NAME").layer("...")` explicitly addresses another composition. Explicit comp names make cross-comp references clear; when duplicating a rig, inspect whether expressions still target the original comp. For portable templates, minimize unnecessary cross-comp dependencies and use **Essential Properties** for per-instance controls where possible.

## 9.4 CSV / TSV: a workable mini-dataset

Create a text file `characters.csv`:

```csv
name,color,score
Dusk,red,4
Kim,purple,2
Bryan,blue,1
Susan,green,5
```

Or create `characters.tsv` with exactly the same grid but **TABs** instead of commas. Import via **File → Import → File**, not as a video. Supported data items can be exposed via AE's **Data** properties and linked with the expression pick whip.

To read imported table data, use `dataValue(dataPath)`. The path contains zero-based indices into AE's imported data hierarchy. Start with the first data item:

```js
footage("characters.csv").dataValue([0, 0]);
```

For any other item, add the CSV/TSV to a test composition, expand **Data**, and **pick-whip the exact cell property**. Use its generated index path as the reference for your file. Check the returned value against the spreadsheet before building a rig around it; header handling and path interpretation should be verified from the imported tree. On Source Text, wrap a numeric result with `String(...)` if needed:

```js
String(footage("characters.csv").dataValue([0, 0]));
```

Data can update when the source data file changes on disk. Ensure relinking and packaging workflows collect external data files (or place the data source as a composition layer when compatible with your delivery pipeline).

## 9.5 JSON: nested names, valid syntax, real-world use

A valid standard JSON file `planet.json`:

```json
{
  "planet": {
    "earth": {
      "land": {
        "soil": {
          "tree": "leaves"
        }
      }
    }
  }
}
```

This is **data**, so you do not declare `const`, use `=`, or add a JavaScript semicolon outside the JSON object. JSON requires double quotes around keys and string values and prohibits trailing commas. Import the file in AE, then on a text layer **Source Text** use:

```js
data = footage("planet.json").sourceData;
data.planet.earth.land.soil.tree.toUpperCase();
```

Result: `LEAVES`.

**Distinction:** For ordinary **JSON**, use `.sourceData` to access nested properties (or `.sourceText` when you explicitly need raw text). The `.dataValue(dataPath)` function is used for AE's data streams such as imported **CSV/TSV** and **MGJSON**. Do **not** assume a deeply nested regular JSON object's keys can be fetched by blindly supplying `[0,0,0,...]` as if it were a table.

**Data project tip:** If a filename changes, an expression referring to `footage("OldName.json")` may fail. Keep filenames predictable. In reusable MOGRT or Media Encoder workflows, Adobe advises including data sources as composition layers when necessary for reliable packaging and rendering rather than depending solely on Project-panel references.

## 9.6 Errors and gotchas in expressions

| Error / symptom | Think of this first |
|---|---|
| `undefined` or unknown property | Incorrect effect control name, missing referenced layer/file, case-sensitive JSON property, renamed group. |
| Dimension mismatch | `[x,y]` used on one-value property, scalar used on 2D position, invalid 3D mismatch. |
| Circular dependency | A layer's expression points to another property that eventually points back. |
| Expression works in this comp only | `thisComp` refers to a different comp after a precomp/copy; explicit comp refs may be appropriate. |
| Result jumps after parenting | Coordinates were evaluated in different layer/parent spaces; use coordinate-space functions or a shared parent. |
| Value unexpectedly ignores original keyframes | Expression explicitly replaces value rather than using `value` or sampled keyframes. |
| Unstable loop | First/last animated states differ, extra endpoint keyframe, missing keyframes, wrong work area. |
| Terrible render performance | Deep dependency chains, many `sampleImage()` expressions, per-character expression stacks, heavy nested precomps. |
| Imported data seems stale | Source not reloaded/relinked, file path changed, cache, changed name/schema, media interchange difference. |

**Make expressions readable.** Use clear variable names, keep references at the top, avoid copying unnamed magic numbers everywhere, and save complicated rigs as a labeled precomp/preset. For reusability, prefer slider controls and exposed Essential Properties over fragile hard-coded names.


## 9.7 Range mapping and newer key lookup

AE's **`linear()` clamps at its input limits**: `linear(150, 0, 100, 20, 200)` returns 200. For deliberate extrapolation, use arithmetic such as `20 + (value/100)*180` on a scalar property. `ease()` also holds its endpoint values outside the range.

Recent versions expose **`nextKey(t)`** and **`previousKey(t)`** on a property/marker stream. `nextKey` finds a later key and falls back to the last; `previousKey` finds one at or before the requested time and falls back to the first. Check `numKeys` before lookup on a potentially empty stream. They are expression methods, unrelated to pressing `J/K` in Timeline. [Adobe: interpolation and key lookup](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-language-reference/expression-language-reference.html).


## 9.8 Small expression building blocks

An expression **returns the value of its own property**. It does not assign values to other properties as an application script would. Use the expression language menu and pick whip to build references, then inspect what AE generated.

| Building block | Meaning and safe use |
|---|---|
| `Math.round(x)` / `Math.floor(x)` / `Math.ceil(x)` | Nearest integer / toward negative infinity / toward positive infinity. Negative numbers make the distinction visible. |
| `Math.abs(x)` | Magnitude: removes a negative sign. |
| `Math.min(a,b)` / `Math.max(a,b)` | Smaller / larger value. |
| `x % n` | JavaScript remainder; for positive values it can create a repeating ramp. Its result can be negative when x is negative. |
| `framesToTime(12)` | Converts 12 frames to seconds using the relevant frame rate. |
| `timeToFrames(time)` | Converts seconds to a frame count. |
| `inPoint` / `outPoint` | The layer's timeline boundaries, in seconds. |
| `numKeys` / `key(1)` | Number of keys / first key. AE key indices begin at 1; JavaScript array indices begin at 0. |
| `random(10)` | Random scalar from 0 to 10; its lower bound is not 1. |
| `seedRandom(index, true); random(-15,15);` | Stable, layer-dependent random scalar; useful on Rotation. Reordering layers changes index-based seeds. |
| `length(p1,p2)` | Distance between two points in the **same coordinate space**. |
| `hasParent` | Check before using `parent`; a layer need not have a parent. |
| `loopIn("cycle")` | Repeats eligible keyed animation before its first key. |
| `loopOut("offset")` | Repeats a cycle while adding the cycle's net value change. |
| `loopOut("continue")` | Continues the ending rate of change. |

### Sample a video color at a controller's location

On a **Color** property, with 2D source `COLOR_SOURCE` and null `SAMPLE_POINT`:

```js
src = thisComp.layer("COLOR_SOURCE");
ctl = thisComp.layer("SAMPLE_POINT");
pt = src.fromComp(ctl.toComp(ctl.anchorPoint));
src.sampleImage([pt[0], pt[1]], [2, 2], true, time);
```

The point is converted into the sampled layer's coordinates; `[2,2]` is the sampling radius. The result is RGBA; append `[3]` only when you want alpha as a scalar. Sampling another layer's raw Position without converting coordinates fails when its transform/parent differs. Inspect effect order and avoid heavy sampling across hundreds of expressions. [Adobe: expression examples](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-examples/expression-examples.html).


## 9.9 Copy animation, expressions and links intentionally

| Command | What the destination receives |
|---|---|
| Copy / Paste selected keyframes | Independent keys; earliest copied key goes at CTI. |
| **Copy Expression Only** | The expression on the corresponding property, without copying its keyframe animation. |
| **Copy with Property Links** | Expressions referencing the named source composition/layer. |
| **Copy with Relative Property Links** | Expressions referencing a layer in `thisComp`; useful for reusable rigs. |

For example, `comp("MASTER").layer("CTRL").transform.scale` keeps targeting MASTER, whereas `thisComp.layer("CTRL").transform.scale` targets CTRL in whichever comp contains the expression. Relative links are useful when each duplicated comp should have its own controller. Check the generated reference after pasting. [Adobe: property links](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/edit-expressions/edit-expressions.html).

Use the **Enable Expression** switch to bypass an expression temporarily. To bake an expression, select its property → **Animation → Keyframe Assistant → Convert Expression to Keyframes**. The command disables the expression and creates sampled keys. Preserve an editable copy first: baking samples at frame times and creates many keys, which may behave differently from a continuously evaluated expression during retiming/subframe sampling. [Adobe: expression basics](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-basics/expression-basics.html).

---

# 10 — Practice Curriculum (Rebuild Muscle Memory)

> [!note] The efficient learning rule
> Work in an empty practice project. **Do each task once while reading, then immediately once without looking.** The second repetition is what develops shortcut recall. Use only a short, 5–10-second comp so experiments are instant.

| Session | Build this | Shortcuts/actions to make automatic |
|---|---|---|
| **1 — Navigate fast** | Ten colored squares with different in/out points; jump between them and reorganize the layers. | `Ctrl+N`, `Ctrl+Y`, `I/O`, `J/K`, `Page Up/Down`, `B/N`, `Alt+[/]`, `[/]`, `Ctrl+Alt+Up/Down`, `Ctrl+Shift+D`. |
| **2 — Animate with intent** | Bouncing ball with slow-in and rapid exit; stagger five balls. | `P/S/R/T/A`, `Alt+Shift+P/S/R/T`, `U/UU`, `F9`, `Shift+F3`, `Alt+Left/Right`, `Ctrl+Alt+drag` Quick Offset. |
| **3 — Masks and track mattes** | Animated title revealed through a window; person-shaped video fill. | `G`, `Q`, `M/MM/F`, `F4`, Alpha/Luma Track Matte, Invert, `Ctrl+Shift+C`. |
| **4 — Shapes and type** | Animated badge with editable headline, filled rectangle and circular stroke. | `Ctrl+T`, `Q`, `G`, Shape Contents/Trim Paths, text animators, Essential Properties. |
| **5 — Track and key** | Remove screen color and pin a label onto moving video. | Keylight/Screen Matte, track target Null, Roto Brush/Object Matte, parenting. |
| **6 — Effects and 3D** | Make neon smoke over a 3D scene with camera movement and DOF. | `Ctrl+Enter` Quick Apply, Wave Warp/Curl Noise, Glow, 3D Layer/Cameras, render-engine selection. |
| **7 — Expressions and delivery** | Music-reactive lower third using JSON name/color input; export review plus alpha master. | `EE`, expression pick whip, `Ctrl+Alt+C` property links, `sourceData`, `dataValue()`, `Ctrl+Shift+/`, output codecs/alpha. |

### Weekly 5-minute warmup

Set a timer outside the app, open a blank comp, create a Null and three shapes, animate their Position/Scale, add easy ease, show only animated properties, stagger keys, switch the Graph Editor, create a track matte, search/add an effect with Quick Apply, then export a brief review. Try to avoid clicking any menu that has a memorable keyboard shortcut. When a shortcut fails, **check the active panel** before assuming the key is wrong.

### Recall checklist

- [ ] I can explain when **Layer panel vs Composition panel** matters.
- [ ] I know **what P, S, R, T, A, U, UU, EE, LL, M, MM, E, F, F4, B, N, I, O, J, K** reveal or move.
- [ ] I know the difference between **playhead step, keyframe nudge, moving a whole layer, trimming and slipping footage**.
- [ ] I can create **an alpha track matte** without rearranging the two layers next to one another.
- [ ] I can explain **Linear vs Bézier vs Easy Ease vs Hold** and when speed graph matters.
- [ ] I can tell **which actions are effects, tools, expressions, scripts, plugins or renderer features**.
- [ ] I know the **3D/2D, color-management and alpha-channel limitations** of my export.
- [ ] I can **turn off effects temporarily without deleting them**.
- [ ] I can use **Ctrl+Enter** and **Ctrl+Alt+drag** and know why neither replaces `Ctrl+Shift+E` or `Alt+Left/Right`.
- [ ] I can find the exact command in **Edit → Keyboard Shortcuts** even when I haven't memorized it.

---

# 11 — Glossary and Reference Sources

## 11.1 Useful terms, explained without mystery

| Term | Meaning |
|---|---|
| **CTI / playhead** | Current Time Indicator; the time whose frame is displayed/evaluated. |
| **In / Out point** | Beginning and end of a layer's visible span. Do not confuse with the Work Area limits `B`/`N`. |
| **Work Area** | Selected time range for preview/export, independent of composition duration until trimmed. |
| **Precomp / nested comp** | Composition inside another composition, with its own pixels, properties and timeline. |
| **Parent** | A layer whose transforms another layer inherits; shifts spatial relationship. |
| **Anchor Point** | Local pivot point for scaling/rotation. |
| **Alpha channel** | Transparency; 0 = fully transparent; 1.0 as normalized alpha (or 255 in 8-bit alpha) = fully opaque. |
| **Luma** | Perceived image brightness; used for brightness-dependent mattes/effects. |
| **Track Matte** | Another layer drives the alpha/brightness mask of a target layer. |
| **Garbage matte** | Roughly exclude unwanted region before precise keying/rotoscoping. |
| **Hold / linear / Bézier** | Keyframe interpolation choices: jump, constant interval interpolation, or curved interpolation. |
| **Roving keyframe** | An intermediate spatial-motion key whose time can adjust to maintain a more consistent motion-speed relationship. |
| **Temporal interpolation** | How a value moves between times/keys. |
| **Spatial interpolation** | How a Position path curves between locations. |
| **Graph Editor** | Speed or value graph for keyframes and interpolation. |
| **Expression** | Scripted formula linked to a property, evaluated at render/preview time. |
| **Essential Properties** | Exposed properties of a precomp that can be overridden per instance. |
| **Collapse Transformations** | Change how transformations are propagated through nested comps; can alter appearance and layer ordering behavior. |
| **Continuously Rasterize** | Re-render vector layers at the required effective resolution for sharp results when scaled; cannot restore detail to an arbitrary raster photo. |
| **Draft quality** | Faster approximate viewer/sampling quality when supported; verify final output. |
| **Frame sampling / frame blending** | Methods to choose/interpolate frames for different playback time; Pixel Motion can introduce warped artifacts. |
| **Bit depth (bpc)** | Processing precision per color channel; not equivalent to source codec's bit depth. |
| **Scene-linear** | Working space in which numeric light intensity is approximately linear; useful for physically sensible CGI compositing. |
| **Color transform / LUT** | Map between encodings or looks; creative LUT ≠ necessarily a correct camera input transform. |
| **ACES / OCIO** | Managed color pipeline standards/configuration used in VFX and color-critical work. |
| **Alpha premultiplication** | Whether RGB values already have transparency mathematically baked into the colors. |
| **ProRes / DNxHR** | Common higher-quality intraframe editing/intermediate codecs. |
| **H.264 / H.265 (HEVC)** | Common compressed delivery codecs; strong distribution options, not ideal for every compositing source. |
| **Render Queue** | AE's native output queue, including native H.264 output support in recent versions. |
| **MOGRT** | Motion Graphics template for standardized motion elements in supporting apps. |
| **ROI** | Region of Interest; viewing/rendering only a limited image region during preview when supported. |
| **Motion path dots** | Visual samples of successive frame positions, indicating relative frame-to-frame travel spacing. |
| **Guide layer** | Visible for internal composition work but generally excluded from its rendered parent-comp output (subject to context). |
| **Mocha AE** | Bundled planar tracking integration, with features distinct from separately licensed Mocha Pro. |
| **Quick Apply** | Search-and-apply UI for effects/presets/commands, launched via `Ctrl+Enter`. |
| **Quick Offset** | `Ctrl+Alt+drag` selected layers/keys to distribute across time. |

## 11.2 Offline-findable reference links

These links are **documentation sources**, not prerequisites for reading this guide offline. The guide was researched against publicly available Adobe documentation through **2026-10-27**; you can use the in-app **Edit → Keyboard Shortcuts** editor as the final authority for your locally installed preset, platform and remappings.

1. **[Adobe: After Effects Keyboard Shortcuts — complete command reference](https://helpx.adobe.com/after-effects/desktop/get-started/keyboard-shortcuts/keyboard-shortcuts-reference.html)** — Windows defaults and special tool/panel shortcuts.
2. **[Adobe: What's New in After Effects](https://helpx.adobe.com/after-effects/desktop/what-s-new/whats-new.html)** — 26.5 and earlier release features.
3. **[Adobe: Full Effects List](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effect-list.html)** — effects grouped by category; note that recently introduced effects can appear on their own new-feature help pages before the general directory is revised.
4. **[Adobe: Quick Apply](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/quick-apply.html)** — Ctrl+Enter and result modifiers.
5. **[Adobe: Select and Arrange Layers / Quick Offset](https://helpx.adobe.com/after-effects/desktop/work-with-layers/select-and-arrange-layers/selecting-arranging-layers.html)** — Ctrl+Alt+drag timeline gesture.
6. **[Adobe: Track Mattes](https://helpx.adobe.com/after-effects/desktop/work-with-transparency-and-compositing/work-with-track-mattes-and-traveling-mattes/track-mattes-and-traveling-mattes.html)** — alpha, luma, inversion and modern layer assignment.
7. **[Adobe: Object Matte](https://helpx.adobe.com/after-effects/desktop/roto-brush-and-refine-matte/roto-brush/object-matte.html)** — newer automatic selections and propagation.
8. **[Adobe: Data-Driven Animation](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/data-driven-animation/data-driven-animations.html)** — CSV, TSV, JSON, MGJSON, `sourceData`, `dataValue()`.
9. **[Adobe: Expression Language Reference](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-language-reference/expression-language-reference.html)** — functions and correct property syntax.
10. **[Adobe: OpenColorIO and ACES](https://helpx.adobe.com/after-effects/desktop/adjust-colors/opencolorio-and-aces-color-management/opencolorio-aces-color-management.html)** — color pipeline guidance.
11. **[Adobe: Render H.264 from AE Render Queue](https://helpx.adobe.com/after-effects/desktop/render-and-export/export-h-264-from-the-after-effects/exporting-h264-from-the-after-effects-render-queue.html)** — native MP4 workflow.
12. **[Adobe: Create and Manage Guides](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/guide-creation-and-management.html)** — 26.5 positioning/color/pin features.
13. **[Adobe: Parametric 3D Meshes](https://helpx.adobe.com/after-effects/desktop/work-with-3d-composition/create-parametric-meshes/working-with-parametric-meshes.html)** — newer native 3D capabilities.
14. **[Adobe: Audio Effects](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/audio-effects.html)** — detailed behavior of audio effects.
15. **[Adobe: After Effects (Beta)](https://helpx.adobe.com/after-effects/desktop/what-s-new/after-effects-beta.html)** — intentionally separate from this 26.5 stable guide.


16. **[Adobe: feature history](https://github.com/AdobeDocs/after-effects-feature-history)** — useful for separating original introduction dates from cumulative What's New entries.
17. **[Adobe: 26.0 launch announcement](https://blog.adobe.com/en/publish/2026/01/20/new-ai-powered-video-editing-tools-premiere-major-motion-design-upgrades-after-effects)** — confirms the initial 2026 effect additions.
18. **[Adobe: Release Notes](https://helpx.adobe.com/after-effects/desktop/what-s-new/release-notes-after-effects.html)** — stable release identification.
19. **[Adobe: Noise and Grain / Curl Noise](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/noise-grain-effects.html)** — procedural texture controls.
20. **[Adobe: Keying / Unmult](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/work-with-keying-effects/keying-effects.html)** — black/white background removal and keying tools.
21. **[Adobe: Proportional Scrubbing](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/proportional-scrubbing-in-timeline.html)** — selection order and modifier gesture.
22. **[Adobe: effects, presets and effect masks](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effects-animation-presets-overview.html)** — application and compositing controls.
23. **[Adobe: precomposing and nesting](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/precomposing-and-nesting/precomposing-nesting-pre-rendering.html)** — processing boundaries and render-order implications.
24. **[Adobe: Content-Aware Fill](https://helpx.adobe.com/after-effects/desktop/remove-objects-from-your-videos/content-aware-fill.html)** — fill layers and reference-frame workflow.
25. **[Adobe: Motion Graphics templates](https://helpx.adobe.com/after-effects/desktop/motion-graphics/work-with-motion-graphics-templates/creating-motion-graphics-templates.html)** — packaging editable graphics.
26. **[Adobe: known issues](https://helpx.adobe.com/after-effects/desktop/troubleshooting/fixed-and-known-issues/known-issues-after-effects.html)** — release-specific limitations; check before adopting a workaround.
27. **[Adobe: fixed issues](https://helpx.adobe.com/after-effects/desktop/troubleshooting/fixed-and-known-issues/fixed-issues.html)** — distinguishes resolved bugs from current limitations.


28. **[Adobe: masks and mask-mode gestures](https://helpx.adobe.com/after-effects/desktop/work-with-transparency-and-compositing/work-with-alpha-channels-and-masks/alpha-channels-masks-mattes.html)** — explains the held-mouse context for mode letters.

29. **[Adobe: Keyframe creation, navigation and deletion](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/setting-selecting-deleting-keyframes.html)**.
30. **[Adobe: Advanced keyframe editing](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animation-keyframes/editing-moving-copying-keyframes.html)**.
31. **[Adobe: Copy Frame to Clipboard](https://helpx.adobe.com/after-effects/desktop/view-and-preview/copy-frames/copy-frame-to-clipboard.html)**.
32. **[Adobe: Native null controllers](https://helpx.adobe.com/after-effects/desktop/drawing-painting-and-paths/null-controllers/create-nulls-for-positional-properties-and-paths.html)**.
33. **[Adobe: Variable-font axes](https://helpx.adobe.com/after-effects/desktop/variable-font-axes/work-with-variable-font-axes/working-with-variable-font-axes.html)**.
34. **[Adobe: SVG import and clipboard transfer](https://helpx.adobe.com/after-effects/desktop/import-files/import-svg-files/import-svg-files.html)**.
35. **[Adobe: Text animators](https://helpx.adobe.com/after-effects/desktop/animating-text/text-animation/animating-text.html)**.
36. **[Adobe: Puppet tools](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/animate-with-puppet-tools/animating-puppet-tools.html)**.
37. **[Adobe: 3D Camera Tracker](https://helpx.adobe.com/after-effects/desktop/work-with-3d-composition/3d-camera-tracker-effect/tracking-3d-camera-movement.html)**.
38. **[Adobe: Responsive Design – Time](https://helpx.adobe.com/after-effects/desktop/motion-graphics/add-responsive-design/responsive-design.html)**.
39. **[Adobe: Viewer controls](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/modifying-using-views.html)**.
40. **[Adobe: Composition Profiler](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/composition-profiler/composition-profiler.html)**.
41. **[Adobe: Lossless compressed disk frames](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/lossless-compressed-playback.html)**.
42. **[Adobe: Effect Manager](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effect-manager.html)**.
43. **[Adobe: Cinema 4D and CINEWARE](https://helpx.adobe.com/after-effects/desktop/work-with-other-applications/work-with-cinema-4d-and-cineware/c4d.html)**.

44. **[Adobe: text clipboard and bounding-box editing](https://helpx.adobe.com/after-effects/desktop/add-text/create-and-edit-text-layers/creating-editing-text-layers.html)**.
45. **[Adobe: composition bounds](https://helpx.adobe.com/after-effects/desktop/work-with-compositions/composition-settings/composition-basics.html)**.
46. **[Adobe: expression examples](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-examples/expression-examples.html)**.
47. **[Adobe: expression controls](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-controls/expression-controls.html)**.

48. **[Adobe: Text outlines and masks](https://helpx.adobe.com/after-effects/desktop/drawing-painting-and-paths/work-with-masks/create-masks.html)**.
49. **[Adobe: Layer Styles and blending](https://helpx.adobe.com/after-effects/desktop/work-with-layers/work-with-layer-blending-modes/blending-modes-layer-styles.html)**.
50. **[Adobe: Expression editing and property links](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/edit-expressions/edit-expressions.html)**.
51. **[Adobe: Expression basics and baking](https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-basics/expression-basics.html)**.
52. **[Adobe: Previewing and ROI](https://helpx.adobe.com/after-effects/desktop/view-and-preview/preview-video-and-audio/previewing.html)**.
53. **[Adobe: Interface search syntax](https://helpx.adobe.com/after-effects/desktop/get-started/get-familiar-with-the-interface/general-user-interface-items.html)**.
54. **[Adobe: Markers and duration handles](https://helpx.adobe.com/after-effects/desktop/work-with-layers/work-with-layer-and-composition-markers/layer-markers-composition-markers.html)**.
55. **[Adobe: Reshape and distortion](https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/distort-effects.html)**.
56. **[Adobe: HDR and scopes](https://www.adobe.com/us/learn/after-effects/web/hdr-color-depth)**.

57. **[Adobe: PSD import and refresh](https://helpx.adobe.com/after-effects/desktop/import-files/import-still-images/preparing-importing-still-images.html)**.
58. **[Adobe: archived full reference](https://helpx.adobe.com/pdf/after_effects_reference.pdf)** — additional historical shortcut documentation; check older bindings in the installed editor.

## 11.3 Design your own shortcut overlay

You can remap almost any exposed menu command via **Edit → Keyboard Shortcuts** (`Ctrl+Alt+'`). However, not literally *every* UI control, plugin function or script action has a built-in assignable command. Unused keys are context-sensitive; check collisions with typing, 3D cameras, other panels and the operating system.

| Command worth assigning if not already mapped | Suggested thought process |
|---|---|
| **Time-Reverse Keyframes** | Useful if you routinely animate transitions; pick a chord that doesn't interfere with reversing source footage. |
| **Copy Expression Only** | Useful when linking expression rigs to many layers; prioritize it if you work with template systems. |
| **Rove Across Time** | Helpful for path animation with multiple intermediate positions. |
| **Null Controllers for Path Points / Null Tracing Along Path** | Search the native menu command first; the older ScriptUI panel is a separate way to access related operations. |
| **Frequently used custom presets/effects** | Prefer **Quick Apply** before consuming dozens of permanent custom key bindings. |

> [!summary] The reference rule
> **Understand the operation → use the shortcut → verify the result.** A shortcut is only fast when you know what layer/property is selected, which panel has focus, and whether you are editing values, time or pixels. If you remember just one fallback, remember **`Ctrl+Alt+'`** to open the keyboard-shortcut editor and search the exact command.
