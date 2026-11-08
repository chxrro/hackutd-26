# Belt control-box enclosure

A two-part printed box (base and lid) for the haptic belt's controller: one ESP32 DevKit, two Inland/Keyestudio ULN2003 boards and a 470 µF capacitor. A Veektomx VT053C MagSafe power bank sticks magnetically to the outside of the lid. The ESP32's USB-C port is the only electrical opening, and the bank's built-in cable plugs into it.

> **2026-11-07 rush version:** this was printed before anyone could measure the parts. Every fit has extra room: the bank pocket, the USB-C notch and the ring pocket are oversized, and the board spaces are larger than the photo estimates. The ULN2003 boards rest on rails instead of screw standoffs, the wire exits are 14 × 8 mm open-top notches, and the lid uses self-tapping screws. A clip part lets the box hook onto a waistband.

> **Status: not yet printed or test-fitted.** Most dimensions are estimates from photos (marked `MEASURE` in the source). So far the only checks are CAD interference checks against simplified models of the parts. Measure the real parts, update the parameters and re-export before you print.

| File | What it is |
|---|---|
| `belt_enclosure.scad` | Parametric OpenSCAD source. All dimensions are named parameters at the top. |
| `build.sh` | Re-exports the STL/3MF files, re-renders the previews and runs the interference checks. |

Generated files are not committed. To get the print file, run `./build.sh` to produce `stl/belt_box_all_parts.stl`. That single file is the one to submit: the base, lid and clip laid out on one 233 × 168 mm plate, already in print orientation. The script also writes:

- the separate parts: `stl/belt_box_base.stl` (body side), `stl/belt_box_lid.stl` (bank side) and `stl/belt_box_clip.stl` (printed on its side);
- `stl/belt_box_plate.3mf`, the same plate with each part as its own object;
- preview images in `renders/`.

## Layout

Coordinates: `x` runs along the box (0 = the port end), `y` across it, and `z` away from the body.

- **Stack-up, body to outside:** belt webbing → base floor → ULN2003 boards on 4 mm standoffs and the ESP32 on 20 mm pillars → lid → steel ring sticker → power bank.
- **ESP32:** mounted high, components up, header pins down. The female Dupont jumpers hang below it, and the wires turn under the board. The USB-C port is centred on the port-end wall, in line with the bank's cable exit.
- **Bank cable:** leaves the bank's short edge, makes a 180° hairpin outside the box and plugs straight into the ESP32. The model reports the cable length this needs (about 35 mm with the defaults) and compares it with `bank_cable_len`.
- **ULN2003 boards:** side by side at the far end. The model assumes the IN1–4 and +/− pins are on the edge facing the ESP32 and the JST-XH socket is on the far edge. Swap this if your boards differ.
- **Motor harnesses:** two U-notches in the top of the far-end wall. The lid clamps each cable (`motor_pinch` = 0.5 mm), and a zip tie through the buttress just inside the wall anchors it.
- **Belt:** ears with webbing slots at both ends. The webbing goes in through one slot, runs behind the box and comes out through the other, so it never passes over the bank. Webbing width is `belt_w` (default 38 mm).
- **Bank locating:** the lid has a 0.5 mm pocket for the steel ring sticker, positioned under the bank's magnet ring. A 3 mm fence on three sides keeps the bank from sliding. The cable end is left open so the cable can get out.
- **EN/BOOT:** 2 mm pinholes in the lid above the buttons. They sit under the bank, so remove the bank to use them. You remove it anyway to flash over USB.
- **Antenna:** the antenna has to point away from the port, which aims it toward the magnet ring. As placed, it sits under the ring's open centre (the model reports the positions) and about 8 mm below the lid. The bank still covers the whole box, so **check BLE range with the bank attached** before relying on it.

## Parameters to measure first

| Parameter | Default | Measure |
|---|---|---|
| `bank_l`, `bank_w`, `bank_t`, `bank_r` | 100, 65, 12, 8 | Bank length, width, thickness, plan-view corner radius |
| `bank_cable_exit_h`, `bank_cable_exit_dy` | 6, 0 | Cable centre height above the magnet face, and its offset from centre |
| `bank_cable_len` | 60 | Free cable from the bank edge to the back of the plug body |
| `plug_w`, `plug_h`, `plug_len` | 12.5, 7, 22 | Plug overmold width and thickness, and length from metal tip to back |
| `bank_ring_x`, `bank_ring_dy` | 69, 0 | Magnet-ring centre from the cable edge, and its offset from centre |
| `ring_od`, `ring_t` | 56, 0.5 | Steel sticker diameter and thickness (sets pocket depth) |
| `esp_l`, `esp_w`, `esp_hole_*`, `esp_hole_d` | 51.5, 28.3, 2.5, 3.0 | ESP32 board size, hole positions and hole diameter |
| `esp_btn_x`, `esp_btn_dy` | 3.5, 8.5 | Button centres from the USB edge and from the centreline |
| `usbc_overhang` | 0.5 | How far the USB-C receptacle sticks past the board edge |
| `uln_l`, `uln_w`, `uln_hole_*` | 35, 32, 2.5 | ULN2003 board size and hole positions |
| `dupont_len`, `wire_bend` | 14, 3.5 | Jumper housing length and bend room. Together these set the box height. |
| `motor_cable_w`, `motor_cable_h` | 7, 4 | Harness cross-section where it leaves the box |
| `belt_w`, `belt_t` | 38, 2.5 | Webbing width and thickness |

Box size is derived from the layout and the bank, so you don't set it directly. With the defaults the box is 112.3 × 78 × 35.5 mm (38 mm including the fence) and 132.3 mm long including the belt ears. Use `extra_l`, `extra_w` and `extra_h` to add room.

The height comes almost entirely from straight Dupont housings under the ESP32 (2.5 + 14 + 3.5 mm). With soldered wires or right-angle jumpers you could reduce `dupont_len` and `esp_header_spacer` and lose about 12 mm.

**Power-test fallback:** if the ESP32's PWR LED stays off on the bank's cable (no USB-C CC resistors), a USB-C sink breakout wired to VIN becomes the port. `bank_y_offset`, `bank_cable_exit_dy` and `port_z_override` move the notch. The breakout will need its own holder, which is not modelled yet.

## Regenerate

Requires OpenSCAD. This was built with the 2026.10 snapshot (`brew install --cask openscad@snapshot`); the Manifold backend makes it render in seconds.

```sh
cd cad/belt-enclosure
./build.sh                                  # defaults
./build.sh -D bank_l=98.4 -D bank_w=64.2    # override measured values
```

`build.sh` exits non-zero if any interference check fails. A check fails when two shapes overlap by 0.02 mm or more. Parts that only touch, like a board resting on its standoffs, pass. The check uses boxy models of the boards, so it can't catch every real-world collision. You can also open the `.scad` file in the OpenSCAD GUI and use the Customizer panel.

## Printing (Bambu Lab, 256 mm bed)

- **Material:** PETG is preferred for something worn on the body (it creeps less and tolerates a hot car better). PLA works for fit tests.
- **Orientation:** as exported. The base goes floor-down and the lid goes outer-face-up, so the ring pocket and fence face up. **No supports needed.** The only bridges are the zip-tie tunnels, about 4.6 mm.
- **Settings:** 0.2 mm layers, 4 walls (the insert bosses, pillars and belt ears need the strength), 20–25% infill, and the default textured or smooth plate. A brim isn't needed.
- **Print the lid first.** It takes about an hour. Use it to check the bank fit, the ring position and the pinholes against the real parts before you print the base.

## Hardware

| Qty | Part |
|---|---|
| 4 | M3 × 10 countersunk screws, threaded straight into the plastic bosses (2.7 mm pilot), for the lid |
| 2 | M3 × 10 countersunk screws and 2 M3 hex nuts (5.5 mm across flats), for the clip. The nuts drop into hex pockets on the floor. |
| 4 | M2.5 × 8 self-tapping screws for the ESP32, or hot glue / double-sided foam tape if its holes don't line up with the pillars |
| — | Double-sided foam tape or hot glue for the ULN2003 boards (they sit on rails, no screws) |
| 3 | Zip ties, up to 4 mm wide (2 for the harnesses, 1 for the capacitor) |
| 1 | Adhesive MagSafe steel ring sticker, about 56 mm outside diameter |

## Assembly

1. Optional: screw the clip to the back. Drop an M3 nut into each hex pocket on the inside floor. Then feed an M3 × 10 countersunk screw from the clip side, through the access hole in the tongue, and tighten it into the nut. The pocket stops the nut from turning. Do this before you fit the ESP32, which sits over the pockets. Skip this if you're taping the box or threading a belt.
2. Stick the steel ring into the lid's pocket and press it flat.
3. Stick the ULN2003 boards onto their rails with foam tape or hot glue. Plug in both motor harnesses and lay each one into its notch. Zip-tie each harness to its buttress through the tunnel, with the tie head inside the box. The lid closes over the notches but doesn't clamp the wires.
4. Connect the jumpers to the ESP32 header pins following the two-board wiring: GPIO 32/33/25/26 to board A IN1–4, GPIO 27/14/13/4 to board B IN1–4, and VIN/GND to both boards' + and −. As of 2026-11-07, `esp32/include/config.h` and `esp32/README.md` still describe the older 5-motor ULN2803 wiring, so the firmware pin map has to be updated before this harness will work. Lower the ESP32 onto its pillars with the USB-C port in the wall notch. Screw it down if the holes line up; otherwise glue or tape it to the pillar tops.
5. Lay the capacitor in its cradle and zip-tie it. Check its polarity.
6. Fit the lid and drive the four M3 × 10 screws into the bosses. Stop when they're snug, or they'll strip the plastic.
7. Attach it one of three ways: hook the clip over a waistband, thread a belt through the ear slots, or tape the flat back to the belt. Attach the bank with its cable end at the port end, then plug the cable in.

## Open items

1. Caliper measurements of the bank, its cable and plug, and the magnet-ring position (see the table above).
2. ESP32 board size, holes, button positions and height. ULN2003 board size, holes and connector edges.
3. Belt webbing width.
4. The bank-to-ESP32 power test (whether the PWR LED lights).
5. BLE range with the bank attached, measured on the bench.

This is an experimental prototype. Bench-test it and try it in a supervised setting before any walking demo.
