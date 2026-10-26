# Equalization with PipeWire

A parametric equalizer (PEQ) changes the level of selected frequency ranges. PipeWire can apply one to audio sent to a **virtual sink**, then forward the processed signal to a physical output. This is useful for headphone correction and personal tuning without changing every application separately.

```text
Application → Equalizer Sink → equalizer processing → physical sink → headphones / speakers
```

Only streams routed through the virtual sink are processed. An application explicitly routed to another output, a program using a separate audio server, or audio bypassing PipeWire will not automatically be equalized. A headphone preset also affects speakers if the equalizer forwards to speakers: use the correct preset and output together.

## What was checked

On 2026-10-27 this system had PipeWire and pipewire-pulse 1.6.10 and WirePlumber 0.5.18. The built-in filter-chain module and `param_eq` filter were available. A temporary standalone filter-chain using the configuration structure below created stereo nodes, and WirePlumber linked its playback node to the existing physical sink. A sample preset file loaded without reported filter errors. In a separate temporary test, a 1 kHz stereo tone passed through the sample preset to a virtual null sink: both channels measured -9.0 dB relative to the input with the band ON and -6.0 dB with it OFF, consistent with the -6 dB preamp and -3 dB band. This checks one frequency and preset, not the full frequency response. The tests did **not** restart the user service, exercise the persistent server drop-in or default selection, test every preset format, or verify a particular headphone's sound. The final ISO package versions and features still need verification.

Upstream documents the [filter-chain module](https://pipewire.pages.freedesktop.org/pipewire/page_module_filter_chain.html), including `param_eq`, its supported preset syntax and channel-specific options. The [PipeWire configuration manual](https://pipewire.pages.freedesktop.org/pipewire/page_man_pipewire_conf_5.html) explains configuration lookup and drop-ins. Installed manuals and shipped examples describe the version actually in use.

## Concepts

- **Frequency** (`Fc`, Hz): the center of a bell filter or the transition region of a shelf.
- **Gain** (dB): how much a band boosts or cuts. Positive values boost; negative values cut. 0 dB leaves that band unchanged.
- **Q**: controls the filter's shape. For a bell, a larger Q generally makes the affected region narrower. Shelf Q controls its transition shape; preserve the preset's value.
- **Peaking / bell (`PK`)**: boosts or cuts around a center frequency. **Low shelf (`LSC`)** affects the low-frequency side; **high shelf (`HSC`)** affects the high-frequency side. These are the file-format names documented for `param_eq`.
- **Preamp**: a gain applied to the signal as a whole. A negative value creates headroom for boosts. It reduces level; it is not a separate EQ band.

The curves of several filters combine. Their maximum combined boost can exceed any one band's gain, so simply subtracting the largest individual boost is not always sufficient. Start with the preamp supplied with a preset; if distortion occurs, reduce it further and check other gain stages. A preamp does not guarantee freedom from clipping in every stage, especially with downstream boosts, mixing or intersample peaks. Equalization cannot repair distortion already present in a recording.

A measurement-based preset is a starting point, not a guarantee of a particular sound: headphone unit variation, fit, seal, pads and measurement conditions matter. Presets for speakers are also affected by the room and listening position. Avoid comparing two settings solely by loudness; louder often appears better even when the tonal balance is not preferable.

## Choose a preset

Find the exact headphone or IEM model and, where relevant, the correct variant and pads. Sources include [AutoEQ](https://github.com/jaakkopasanen/AutoEq), [oratory1990's measurements and EQ presets](https://old.reddit.com/r/oratory1990/wiki/index), and [Squiglink](https://squig.link/). Prefer a **parametric EQ text export** in the AutoEQ/Squiglink format documented by PipeWire. A graphic EQ list or an impulse-response file is a different format and cannot simply be substituted for this text file. Check filter types and values when transcribing a PDF or another application's preset; do not assume every application's shelf and bandwidth conventions are identical.

The following is a small **demonstration**, not a correction for any device:

```text
Preamp: -6.0 dB
Filter 1: ON PK Fc 1000 Hz Gain -3.0 dB Q 1.000
```

`ON` enables the band. Use the full preset, including its preamp, rather than treating this example as a recommendation. PipeWire documents `PK`, `LSC`, and `HSC` for this format. Check the export and the documentation when it contains other filter types or syntax.

## Create a stereo equalizer sink

This example loads a preset file with the built-in `param_eq` filter. Upstream describes it as more efficient than a graph made from individually linked biquads; no performance comparison was measured here. It needs no separate LADSPA equalizer plugin.

### 1. Save the preset

Choose a stable location readable by your user, for example `~/.config/pipewire/eq/headphones.txt`. Create its directory and edit the file with your preferred editor:

```bash
mkdir -p "$HOME/.config/pipewire/eq"
```

Put the exported preset text in `headphones.txt`. The configuration below requires the **absolute path**: replace `/absolute/path/to/headphones.txt` with the actual path, for example the value printed by:

```bash
printf '%s\n' "$HOME/.config/pipewire/eq/headphones.txt"
```

PipeWire configuration is not a shell script: do not expect `~` or `$HOME` in the quoted filename to be expanded. Keep the file present at that path when PipeWire starts. If your user service uses a non-default `XDG_CONFIG_HOME`, its configuration directory may differ from `~/.config`.

### 2. Add the configuration

Create `~/.config/pipewire/pipewire.conf.d/` if needed:

```bash
mkdir -p "$HOME/.config/pipewire/pipewire.conf.d"
```

Create `~/.config/pipewire/pipewire.conf.d/30-headphone-eq.conf` with the following content, replacing the filename with your actual absolute path:

```ini
context.modules = [
    { name = libpipewire-module-filter-chain
        args = {
            node.description = "Equalizer Sink"
            media.name = "Equalizer Sink"

            filter.graph = {
                nodes = [
                    { type = builtin
                      name = eq
                      label = param_eq
                      config = {
                          filename = "/absolute/path/to/headphones.txt"
                      }
                    }
                ]
                inputs  = [ "eq:In 1"  "eq:In 2" ]
                outputs = [ "eq:Out 1" "eq:Out 2" ]
            }

            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {
                node.name = "effect_input.headphone_eq"
                media.class = Audio/Sink
            }
            playback.props = {
                node.name = "effect_output.headphone_eq"
                node.passive = true
            }
        }
    }
]
```

This is a stereo configuration: `FL` and `FR` are front left and right, mapped explicitly to the first two `param_eq` ports. The same preset applies to both channels. The capture side receives audio as a virtual sink; the playback side sends the processed audio onward. `node.passive` allows the output link to remain passive when no audio is using the chain. These are PipeWire's names for the two sides of the filter, not microphone capture.

A `pipewire.conf.d` drop-in runs the module inside the user's PipeWire server. Drop-in arrays are appended, so keep only the intended copy of the configuration: two copies can create duplicate filters. A file in `/etc/pipewire/pipewire.conf.d/` applies to user PipeWire instances system-wide, but any referenced preset must be readable by each user. Configuration fragments use PipeWire's relaxed SPA JSON syntax, not shell syntax. The system's packaged examples are in `/usr/share/pipewire/filter-chain/`; the shipped `sink-eq6.conf` illustrates individually linked biquads.

### 3. Apply and select it

Restarting the user PipeWire service interrupts active audio and may interrupt other PipeWire clients. After saving both files, run:

```bash
systemctl --user restart pipewire.service
wpctl status
```

Find `Equalizer Sink` in the output, including under **Filters**. IDs are assigned at runtime and can change. Select its current numeric ID:

```bash
wpctl set-default ID
```

Replace `ID` with the actual number, without angle brackets. This selects the default for **new auto-connected streams**; it does not promise to move every already-running stream. Restart playback or move an existing stream with an audio routing application if needed. Applications with their own explicit output selection can continue to bypass the default. WirePlumber remembers an explicitly selected default.

WirePlumber chooses a target for the filter output according to its routing policy. The output can remain attached to its original device when the default changes, so inspect the actual graph after switching or reconnecting devices. Selecting the virtual sink alone does not establish which physical output receives its signal. See the device-targeting option below.

### 4. Verify routing

```bash
wpctl status
wpctl status -n
pactl info
pactl list short sinks
pw-link -l
```

`wpctl status` shows devices, streams and the current default; `-n` shows node names. `pactl info` reports the PulseAudio-compatible server and default sink. `pw-link -l` lists port connections. While test audio is playing, follow both channels from the application to `effect_input.headphone_eq`, and from `effect_output.headphone_eq` to the intended physical sink. The virtual sink can be grouped under Filters rather than the Sinks heading in `wpctl status`.

A connected graph establishes the route, not that the selected preset is acoustically correct. Compare playback with and without EQ at matched perceived loudness. For objective verification of a curve or clipping, capture and analyze the processed signal with suitable measurement tools; listening and node creation alone do not measure it.

## Target a specific physical output

If the preset is only for one headphone output, you can request that output explicitly. First find its **node name** using `wpctl status -n`, and inspect its properties with `wpctl inspect ID` using that physical sink's current ID. Add the following inside `playback.props`, substituting the actual `node.name`:

```ini
target.object = "actual.physical.sink.node.name"
```

Restart PipeWire and inspect the links as above. The node name is preferable to copying a numeric runtime ID; the exact name depends on the device and profile. This property requests a target; it does not guarantee that an unplugged device remains available or that policy will never choose another route. Check behavior on disconnect and reconnection before relying on it. The temporary sample-level test used an explicit target and confirmed both output links to a null sink; it did not test physical-device hotplug. See the installed `man pipewire-props` and [WirePlumber linking policy](https://pipewire.pages.freedesktop.org/wireplumber/policies/linking.html).

## Change, bypass, or remove the EQ

- **Change preset:** edit the preset file, then restart `pipewire.service` and verify the route again. A file edit alone is not documented here as a live reload.
- **Bypass for new streams:** use `wpctl status` and `wpctl set-default ID` with the physical sink's current ID. Existing or explicitly routed streams may need to be moved or restarted. The EQ nodes still exist.
- **Remove:** select the physical sink as the default, remove or rename the EQ drop-in so it no longer ends in `.conf`, then restart `pipewire.service`. The preset file can be retained for later use.
- **Undo a remembered default:** `wpctl clear-default` clears all configured default nodes, including capture defaults; use `wpctl clear-default 0` to clear only the configured audio sink selection on the checked WirePlumber version. The `0` is a settings ID from the “Default Configured Devices” section, not a runtime sink node ID. See `man wpctl`.

## Troubleshooting

1. **No virtual sink:** check the file is in the configuration directory used by the user service, ends in `.conf`, has valid syntax, and has the correct absolute preset path. Check the installed module and service logs:

   ```bash
   systemctl --user status pipewire.service --no-pager
   journalctl --user -u pipewire.service -b --no-pager
   ```

   Inspect messages from the restart time. If a bad drop-in prevents startup, rename or remove it and restart the service. Avoid editing the packaged main configuration under `/usr/share`.

2. **Sink exists but no sound:** check both sides of the graph with `wpctl status` and `pw-link -l`, the selected physical output, mute and volume. Check the device is available. A preset path or filter error may also be visible in the journal.
3. **Some applications are unaffected:** check their actual stream links and output selection. Change or move their streams to the equalizer sink; a default applies to new streams requiring automatic routing.
4. **Wrong device receives the equalized signal:** inspect the playback link after plugging or unplugging devices. This general virtual sink is not inherently bound to a specific headphone, and its output may remain linked to the previous device after the default changes. Automatic insertion of a filter for streams targeting a particular device requires additional WirePlumber policy; see its [smart filters](https://pipewire.pages.freedesktop.org/wireplumber/policies/smart_filters.html) and [automatic software DSP](https://pipewire.pages.freedesktop.org/wireplumber/policies/software_dsp.html) documentation.
5. **Distortion or unexpected level:** check the preset preamp, application and sink volumes, other effects, device gain, and the source recording. Reduce gain and compare with the EQ bypassed. An incorrectly transcribed Q, gain, frequency or filter type can change the result substantially.

## Further details

`param_eq` also supports inline `filters` arrays and per-channel `filename1`, `filename2`, and so on, through channel 8. See the upstream filter-chain documentation for syntax and supported biquad labels. Per-channel correction needs appropriate measurements; a common headphone preset normally applies to both channels. For a more complex processing graph, the module also supports separately linked built-in filters and plugin formats such as LADSPA and LV2. Those plugins must actually be installed and their names and controls inspected; they are not required for the setup above.

Useful local references:

```bash
pipewire --version
wpctl --help
man wpctl
man pipewire.conf
man pipewire-props
man pipewire-filter-chain.conf
ls /usr/share/pipewire/filter-chain/
```

Documentation can describe a newer release than the installed system. Check the installed build and its available features before relying on a newer option.
