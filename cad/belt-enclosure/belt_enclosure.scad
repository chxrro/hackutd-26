// HackUTD 26 haptic belt: control-box enclosure (base + lid)
// ----------------------------------------------------------------------------
// Units: mm. Open in OpenSCAD (2021+; a 2025+ snapshot renders much faster).
//
// Coordinate frame (used for every part):
//   origin = outer corner of the base at the USB "port end", on the body-side face
//   x = along the box length (0 = port end, where the power-bank cable is)
//   y = across the box
//   z = away from the body (0 = body-side face, lid on top, power bank above that)
//
// Every value marked MEASURE is an estimate. Replace it with a caliper reading
// and re-export. The README lists all of them.
// ----------------------------------------------------------------------------

/* [Render] */
// What to show/export. base/lid/plate are print-ready; the others are previews.
part = "assembly"; // [assembly, exploded, section, inside, base, lid, clip, plate, check]
// Ghost the electronics and power bank in preview views
show_parts = true;
// Interference check (part = "check"): shapes to intersect
check_a = "base";   // [base, lid, clip, clip_hw, esp32, uln_a, uln_b, cap, plug, motor_cables, bank, ring]
check_b = "esp32";  // [base, lid, clip, clip_hw, esp32, uln_a, uln_b, cap, plug, motor_cables, bank, ring]

/* [Power bank: Veektomx VT053C (all MEASURE)] */
bank_l = 100;              // MEASURE length (cable edge to far edge)
bank_w = 65;               // MEASURE width
bank_t = 12;               // MEASURE thickness
bank_r = 8;                // MEASURE corner radius (plan view)
bank_cable_exit_h = 6;     // MEASURE cable centre height above the magnet face
bank_cable_exit_dy = 0;    // MEASURE cable offset from the bank centreline (+y)
bank_cable_len = 60;       // MEASURE free cable: bank edge to back of the plug body
bank_cable_w = 6;          // MEASURE flat cable width
bank_cable_t = 1.8;        // MEASURE flat cable thickness
bank_ring_x = 69;          // MEASURE magnet-ring centre distance from the cable edge
bank_ring_dy = 0;          // MEASURE magnet-ring offset from the bank centreline
bank_clr = 2.5;            // clearance per side in the locating pocket (generous: unmeasured)
bank_overhang = 0;         // >0 moves the bank's cable edge past the port end
bank_y_offset = 0;         // shift the bank (and therefore the port) across the box

/* [Steel MagSafe ring sticker] */
ring_od = 56;              // MEASURE sticker outer diameter
ring_id = 46;              // MEASURE sticker inner diameter (display only)
ring_t = 0.5;              // MEASURE sticker thickness = pocket depth
ring_clr = 1.0;            // radial clearance in the pocket

/* [USB-C plug on the bank cable] */
plug_w = 12.5;             // MEASURE plug overmold width
plug_h = 7.0;              // MEASURE plug overmold thickness
plug_len = 22;             // MEASURE plug length, metal tip to back of overmold
plug_metal_len = 6.6;      // USB-C metal shell length (spec 6.65)
plug_clr = 1.2;            // clearance around the plug in the wall notch (generous)
// Breakout swap: set >0 to place the port centre at a fixed height instead
port_z_override = 0;

/* [ESP32 DevKit V1 30-pin, USB-C (MEASURE)] */
esp_l = 53;                // MEASURE board length; room allowance (nominal 51.5)
esp_w = 29.5;              // MEASURE board width; room allowance (nominal 28.3)
esp_pcb_t = 1.6;
esp_hole_d = 3.0;          // MEASURE mounting hole diameter
esp_hole_ix = 2.5;         // MEASURE hole centre from the short edges
esp_hole_iy = 2.5;         // MEASURE hole centre from the long edges
esp_row_pitch = 25.4;      // distance between the two header rows
esp_pin_offset = 0;        // shift of the 15-pin rows from centred, toward antenna
esp_btn_x = 3.5;           // MEASURE EN/BOOT button centre from the USB edge
esp_btn_dy = 8.5;          // MEASURE EN/BOOT offset either side of the centreline
esp_btn_h = 2.0;           // MEASURE button height above PCB
usbc_z = 1.6;              // receptacle centre height above the PCB top
usbc_overhang = 0.5;       // MEASURE receptacle mouth past the board edge
esp_port_gap = 0.8;        // receptacle mouth to the wall's inner face
esp_top_clear = 7;         // PCB top to lid underside (module, buttons, plug)
esp_header_spacer = 2.5;   // plastic header strip under the board
esp_pillar_d = 6;          // standoff diameter
esp_pilot_d = 2.2;         // pilot for M2.5 self-tapping screws (M2: 1.7)

/* [Jumper wiring] */
dupont_len = 15;           // female Dupont housing length
wire_bend = 4.5;           // room for wires to turn 90 degrees after the housing

/* [ULN2003 boards: Inland/Keyestudio (MEASURE)] */
uln_l = 38;                // MEASURE board size along the box (x); allowance (nominal 35)
uln_w = 34;                // MEASURE board size across the box (y); allowance (nominal 32)
uln_pcb_t = 1.6;
uln_hole_ix = 2.5;         // MEASURE hole centre from the x edges
uln_hole_iy = 2.5;         // MEASURE hole centre from the y edges
uln_standoff_h = 4;        // rail height: clears through-hole solder tails
uln_rails = true;          // support rails (any board size) instead of screw standoffs
uln_standoff_d = 6;
uln_pilot_d = 2.6;         // pilot for M3 self-tapping (or M2.5 machine screws)
uln_header_spacer = 2.5;
jst_mated_h = 13;          // JST-XH socket + plug height above the PCB
uln_gap_x = 5;             // gap between the ESP32 antenna end and the boards
uln_gap_y = 2;             // gap between the boards and the side walls

/* [Box] */
wall = 2;
floor_t = 2.4;
lid_t = 2.5;
corner_r = 4;
motor_zone = 11;           // room at the far end for cable bends + tie anchors
extra_l = 0;               // add internal length
extra_w = 0;               // add internal width
extra_h = 0;               // add internal height

/* [Lid screws: M3 countersunk, self-tapping into the bosses] */
boss_inset = 4.5;          // boss centre from the outer faces
boss_d = 8;
insert_d = 2.7;            // M3 self-tapping pilot (heat-set insert M3x5.7: 4.0)
insert_len = 9;            // pilot depth
screw_d = 3.4;             // clearance hole in the lid
csk_d = 6.6;               // countersink top diameter (90 degrees)

/* [Bank locating fence on the lid] */
fence_h = 2.5;
fence_min_t = 1.6;
fence_well_d = 8;          // opening in the fence above each lid screw

/* [Motor harness exits: far end wall] */
motor_cable_w = 14;        // wire exit width (generous)
motor_cable_h = 8;         // wire exit depth from the rim
motor_pinch = 0;           // >0 makes the lid squeeze the wires
motor_exit_dy = [0, 0];    // shift each exit from its board's centreline
tie_w = 4;                 // zip-tie width
tie_t = 1.6;               // zip-tie thickness
tie_roof = 1.6;            // plastic above the tie tunnel

/* [Belt ears: webbing passes behind the box] */
belt_w = 38;               // ASK webbing width (38 mm = 1.5 in)
belt_t = 2.5;              // MEASURE webbing thickness
belt_clr = 2;              // extra slot length
ear_t = 4;                 // ear plate thickness
ear_bar = 6;               // outer bar width
ear_arm = 6;               // side arms either side of the slot
gusset_h = 10;
gusset_t = 3;

/* [Pants / belt clip (separate part, screws to the back)] */
clip_enable = true;
clip_w = 25;               // clip width (along the box length)
clip_plate_t = 4;          // plate against the box
clip_t = 3;                // spring tongue thickness
clip_gap = 5;              // opening at the bend (waistband thickness)
clip_tip_gap = 0.8;        // gap at the tongue tip (preload)
clip_hole_inset = 16;      // screw holes from the box's long outer faces

/* [Capacitor cradle] */
cap_d = 10;                // 470 uF can diameter + allowance
cap_l = 14;                // can length + allowance
cradle_len = 8;
cradle_floor = 3.5;        // plastic under the groove (holds the tie tunnel)

/* [Lid pinholes for EN / BOOT] */
pinholes = true;
pinhole_d = 3.0;
pinhole_funnel_d = 6;

/* [Hidden] */
$fn = 48;
eps = 0.01;

// ----------------------------------------------------------------------------
// Derived layout
// ----------------------------------------------------------------------------
esp_under_clear = esp_header_spacer + dupont_len + wire_bend;
uln_top_clear = uln_header_spacer + dupont_len + wire_bend;
H_in = max(esp_top_clear + esp_pcb_t + esp_under_clear,
           uln_standoff_h + uln_pcb_t + max(uln_top_clear, jst_mated_h + wire_bend))
       + extra_h;

overmold_len = plug_len - plug_metal_len;
mouth_x = wall + esp_port_gap;                 // receptacle mouth
esp_x0 = mouth_x + usbc_overhang;              // board USB edge
uln_x0 = esp_x0 + esp_l + uln_gap_x;
tb_x = tie_w + 0.6 + 2 * 1.5;                  // tie buttress length (x)

L_layout = (uln_x0 + uln_l + motor_zone) + wall + extra_l;
L_bank = -bank_overhang + bank_l + 2 * bank_clr + fence_min_t;
L = max(L_layout, L_bank);

W_layout = 2 * uln_w + 3 * uln_gap_y + 2 * wall + extra_w;
W_bank = bank_w + 2 * bank_clr + 2 * fence_min_t + 2 * abs(bank_y_offset);
W = max(W_layout, W_bank);

z_top = floor_t + H_in;                        // base rim = lid underside
z_lid_top = z_top + lid_t;
z_esp_bot = z_top - esp_top_clear - esp_pcb_t;
z_esp_top = z_esp_bot + esp_pcb_t;
z_port = port_z_override > 0 ? port_z_override : z_esp_top + usbc_z;
z_uln_bot = floor_t + uln_standoff_h;
z_cable_bot = z_top - (motor_cable_h - motor_pinch);

bank_x0 = -bank_overhang;                      // bank cable edge
bank_y = W / 2 + bank_y_offset;                // bank centreline
port_y = bank_y + bank_cable_exit_dy;          // port follows the cable
esp_y0 = port_y - esp_w / 2;
ring_x = bank_x0 + bank_ring_x;
ring_y = bank_y + bank_ring_dy;

uln_y = [wall + uln_gap_y, W - wall - uln_gap_y - uln_w];
motor_y = [for (i = [0:1]) uln_y[i] + uln_w / 2 + motor_exit_dy[i]];

cradle_x = esp_x0 + esp_l / 2 - cradle_len / 2;
cradle_y = (wall + esp_y0) / 2;

clip_x = esp_x0 + esp_l - 10;                  // clear of pillars and cradle
clip_hole_y = [clip_hole_inset, W - clip_hole_inset];
clip_y0 = 4;                                   // clip plate starts here (box y)
clip_len = W - clip_y0 - 4;

boss_pts = [[boss_inset, boss_inset], [L - boss_inset, boss_inset],
            [boss_inset, W - boss_inset], [L - boss_inset, W - boss_inset]];

esp_holes = [for (ix = [esp_hole_ix, esp_l - esp_hole_ix])
             for (iy = [esp_hole_iy, esp_w - esp_hole_iy])
             [esp_x0 + ix, esp_y0 + iy]];
uln_holes = [for (b = [0:1]) for (ix = [uln_hole_ix, uln_l - uln_hole_ix])
             for (iy = [uln_hole_iy, uln_w - uln_hole_iy])
             [uln_x0 + ix, uln_y[b] + iy]];

ear_slot = belt_t + 1.5;
ear_ext = ear_slot + ear_bar;
ear_wy = belt_w + belt_clr + 2 * ear_arm;

// Bank cable hairpin: out of the bank edge, 180-degree turn, into the plug.
plug_end_x = mouth_x - overmold_len;
cab_z_bank = z_lid_top + bank_cable_exit_h;
cab_R = (cab_z_bank - z_port) / 2;
cab_xc = min(bank_x0, plug_end_x) - 1;
cable_needed = (bank_x0 - cab_xc) + (plug_end_x - cab_xc) + PI * cab_R;

// ----------------------------------------------------------------------------
// Report (shows in the OpenSCAD console)
// ----------------------------------------------------------------------------
echo(str("BOX outer L x W x H = ", L, " x ", W, " x ", z_lid_top,
         " mm (", z_lid_top + fence_h, " incl. fence; ", L + 2 * ear_ext,
         " long incl. belt ears)"));
echo(str("BOX internal height = ", H_in, "; ESP32 PCB bottom at z = ", z_esp_bot,
         "; port centre z = ", z_port));
echo(str("BANK cable: need ~", round(cable_needed), " mm, have ", bank_cable_len,
         " mm -> ", cable_needed <= bank_cable_len ? "OK" : "TOO SHORT"));
echo(str("ANTENNA: ESP32 antenna edge at x = ", esp_x0 + esp_l,
         "; steel ring spans x = ", ring_x - ring_od / 2, "..", ring_x + ring_od / 2,
         " (hole ", ring_x - ring_id / 2, "..", ring_x + ring_id / 2, ")"));
echo(str("LID SCREWS: M3 countersunk x ", ceil(lid_t + insert_len - 1), " (max ",
         floor(lid_t + insert_len), ")"));
echo(str("LAYOUT drives L=", L_layout, " W=", W_layout, "; BANK drives L=", L_bank,
         " W=", W_bank));

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------
module rr2(l, w, r) {
    r2 = max(min(r, l / 2 - eps, w / 2 - eps), eps);
    translate([r2, r2]) offset(r = r2) square([l - 2 * r2, w - 2 * r2]);
}
module rr2_at(x, y, l, w, r) translate([x, y]) rr2(l, w, r);
module box_at(x, y, z, l, w, h) translate([x, y, z]) cube([l, w, h]);

// ----------------------------------------------------------------------------
// Base (body side): floor, walls, standoffs, belt ears
// ----------------------------------------------------------------------------
module base() {
    difference() {
        union() {
            intersection() {
                linear_extrude(z_top) rr2(L, W, corner_r);
                union() {
                    difference() {
                        linear_extrude(z_top) rr2(L, W, corner_r);
                        translate([0, 0, floor_t]) linear_extrude(z_top)
                            rr2_at(wall, wall, L - 2 * wall, W - 2 * wall,
                                   max(corner_r - wall, 0.5));
                    }
                    for (p = boss_pts) translate(p) cylinder(d = boss_d, h = z_top);
                    // motor-harness tie buttresses against the far wall
                    for (y = motor_y)
                        box_at(L - wall - tb_x, y - (motor_cable_w + 3) / 2, 0,
                               tb_x + eps, motor_cable_w + 3, z_cable_bot);
                }
            }
            for (p = esp_holes) translate(p) cylinder(d = esp_pillar_d, h = z_esp_bot);
            if (uln_rails) uln_support_rails();
            else for (p = uln_holes) translate(p) cylinder(d = uln_standoff_d, h = z_uln_bot);
            cap_cradle();
            belt_ear(0);
            belt_ear(1);
        }
        // heat-set insert holes
        for (p = boss_pts) translate([p[0], p[1], z_top - insert_len])
            cylinder(d = insert_d, h = insert_len + 1);
        for (p = boss_pts) translate([p[0], p[1], z_top - insert_len - 3])
            cylinder(d = 2.6, h = 4);
        // pilot holes
        for (p = esp_holes) translate([p[0], p[1], z_esp_bot - 8])
            cylinder(d = esp_pilot_d, h = 9);
        if (!uln_rails) for (p = uln_holes) translate([p[0], p[1], 0.8])
            cylinder(d = uln_pilot_d, h = 12);
        // belt-clip screw holes through the floor
        if (clip_enable) for (y = clip_hole_y) translate([clip_x, y, -1]) cylinder(d = 3.4, h = floor_t + 2);
        // USB-C port: U-notch from the rim, closed by the lid
        box_at(-1, port_y - plug_w / 2 - plug_clr, z_port - plug_h / 2 - plug_clr,
               wall + 2, plug_w + 2 * plug_clr, z_top);
        // motor harness: U-notches pinched by the lid
        for (y = motor_y) {
            box_at(L - wall - tb_x - 1, y - motor_cable_w / 2 - 0.3, z_cable_bot,
                   tb_x + wall + 2, motor_cable_w + 0.6, z_top);
            // zip-tie tunnel through the buttress (runs across y)
            box_at(L - wall - tb_x / 2 - (tie_w + 0.6) / 2, y - motor_cable_w,
                   z_cable_bot - tie_roof - (tie_t + 0.6),
                   tie_w + 0.6, 2 * motor_cable_w, tie_t + 0.6);
        }
    }
}

module uln_support_rails() {
    for (b = [0:1]) for (f = [0.18, 0.5, 0.82])
        box_at(uln_x0 + 1, uln_y[b] + f * uln_w - 1, 0, uln_l - 2, 2, z_uln_bot);
}

module cap_cradle() {
    zc = floor_t + cradle_floor + cap_d / 2;
    wy = cap_d + 4;
    difference() {
        box_at(cradle_x, cradle_y - wy / 2, 0, cradle_len, wy, zc);
        translate([cradle_x - 1, cradle_y, zc]) rotate([0, 90, 0])
            cylinder(d = cap_d + 0.6, h = cradle_len + 2);
        box_at(cradle_x + (cradle_len - tie_w - 0.6) / 2, cradle_y - wy,
               floor_t + 0.6, tie_w + 0.6, 2 * wy, tie_t + 0.6);
    }
}

module belt_ear(side) {
    // side 0 = port end (x < 0), side 1 = far end (x > L)
    x0 = side == 0 ? -ear_ext : L - corner_r;
    xs = side == 0 ? -ear_slot : L;
    difference() {
        union() {
            linear_extrude(ear_t)
                rr2_at(x0, W / 2 - ear_wy / 2, ear_ext + corner_r, ear_wy, 3);
            // gussets tying the side arms into the end wall
            for (s = [-1, 1]) {
                yg = W / 2 + s * (ear_wy / 2 - ear_arm / 2) - gusset_t / 2;
                translate([side == 0 ? 0 : L, yg, 0])
                    mirror([side == 0 ? 0 : 1, 0, 0])
                    rotate([90, 0, 0]) translate([0, 0, -gusset_t])
                    linear_extrude(gusset_t)
                    polygon([[0.5, 0], [-ear_ext + 1, 0], [-ear_ext + 1, ear_t],
                             [0.5, gusset_h]]);
            }
        }
        box_at(xs, W / 2 - (belt_w + belt_clr) / 2, -1, ear_slot,
               belt_w + belt_clr, ear_t + 2);
    }
}

// ----------------------------------------------------------------------------
// Lid (outer face): bank pocket fence, ring pocket, countersunk screws
// ----------------------------------------------------------------------------
module bank_pocket_2d() {
    rr2_at(bank_x0 - 30, bank_y - bank_w / 2 - bank_clr,
           bank_l + 2 * bank_clr + 30, bank_w + 2 * bank_clr, bank_r + bank_clr);
}

module lid() {
    difference() {
        union() {
            translate([0, 0, z_top]) linear_extrude(lid_t) rr2(L, W, corner_r);
            translate([0, 0, z_lid_top - eps]) linear_extrude(fence_h + eps)
                difference() {
                    rr2(L, W, corner_r);
                    bank_pocket_2d();
                    for (p = boss_pts) translate(p) circle(d = fence_well_d);
                }
        }
        for (p = boss_pts) translate([p[0], p[1], z_top - 1]) {
            cylinder(d = screw_d, h = lid_t + 2);
            translate([0, 0, 1 + lid_t - (csk_d - screw_d) / 2])
                cylinder(d1 = screw_d, d2 = csk_d, h = (csk_d - screw_d) / 2 + eps);
            translate([0, 0, 1 + lid_t]) cylinder(d = fence_well_d, h = fence_h + 1);
        }
        translate([ring_x, ring_y, z_lid_top - ring_t])
            cylinder(d = ring_od + 2 * ring_clr, h = ring_t + 1, $fn = 120);
        if (pinholes) for (s = [-1, 1])
            translate([esp_x0 + esp_btn_x, port_y + s * esp_btn_dy, z_top - 1]) {
                cylinder(d = pinhole_d, h = lid_t + 2);
                translate([0, 0, 1 + lid_t - (pinhole_funnel_d - pinhole_d) / 2])
                    cylinder(d1 = pinhole_d, d2 = pinhole_funnel_d,
                             h = (pinhole_funnel_d - pinhole_d) / 2 + eps);
            }
    }
}

// ----------------------------------------------------------------------------
// Pants/belt clip: printed on its side (profile flat on the bed), no supports.
// Profile coordinates: u along the clip (box y), v away from the box back.
// ----------------------------------------------------------------------------
clip_tongue_end = 20;      // where the tongue tip is (u)
module clip_profile() {
    r = clip_t / 2;
    A = [clip_len, clip_plate_t + clip_gap + r];
    B = [clip_tongue_end, clip_plate_t + clip_tip_gap + r];
    C = [clip_tongue_end - 7, clip_plate_t + clip_tip_gap + r + 4];
    square([clip_len, clip_plate_t]);
    // bend
    bc = [clip_len, (clip_plate_t / 2 + A[1]) / 2];
    br = (A[1] - clip_plate_t / 2) / 2;
    intersection() {
        translate(bc) difference() {
            circle(r = br + max(clip_plate_t, clip_t) / 2);
            circle(r = br - min(clip_plate_t, clip_t) / 2);
        }
        translate([clip_len, -50]) square([50, 100]);
    }
    hull() { translate(A) circle(r = r); translate(B) circle(r = r); }
    hull() { translate(B) circle(r = r); translate(C) circle(r = r); }
}

module clip_part() {
    // as printed: profile in XY, extruded along Z
    difference() {
        linear_extrude(clip_w) clip_profile();
        for (y = clip_hole_y) translate([y - clip_y0, 0, clip_w / 2]) rotate([-90, 0, 0]) {
            translate([0, 0, -1]) cylinder(d = 3.4, h = clip_plate_t + 2);
            // countersink on the gap side so the head sits flush
            translate([0, 0, clip_plate_t - 1.8]) cylinder(d1 = 3.4, d2 = 7, h = 1.8 + eps);
            // driver access through the tongue
            translate([0, 0, clip_plate_t + 0.4]) cylinder(d = 8, h = 20);
        }
    }
}

module clip_on_box() {
    // map (u, v, w) -> box (y, -z, x)
    multmatrix([[0, 0, 1, clip_x - clip_w / 2], [1, 0, 0, clip_y0], [0, -1, 0, 0], [0, 0, 0, 1]])
        clip_part();
}

module clip_hw() {
    // M3 nuts inside on the floor + screw shanks (for interference checks)
    for (y = clip_hole_y) translate([clip_x, y, floor_t]) {
        cylinder(d = 6.4, h = 2.6, $fn = 6);
        cylinder(d = 3, h = 4);
    }
}

// ----------------------------------------------------------------------------
// Reference components (preview + interference checks only, never printed)
// ----------------------------------------------------------------------------
module esp32() {
    pin_x0 = (esp_l - 15 * 2.54) / 2 + esp_pin_offset;
    translate([esp_x0, esp_y0, z_esp_bot]) {
        color("#1f3a8a") difference() {
            cube([esp_l, esp_w, esp_pcb_t]);
            for (ix = [esp_hole_ix, esp_l - esp_hole_ix])
                for (iy = [esp_hole_iy, esp_w - esp_hole_iy])
                    translate([ix, iy, -1]) cylinder(d = esp_hole_d, h = 5);
        }
        color("#c0c0c0") {
            translate([esp_l - 25.5, (esp_w - 18) / 2, esp_pcb_t]) cube([25.5, 18, 3.2]);
            translate([-usbc_overhang, esp_w / 2 - 4.47, esp_pcb_t]) cube([7.35, 8.94, 3.2]);
        }
        color("#222") for (s = [-1, 1])
            translate([esp_btn_x - 2, esp_w / 2 + s * esp_btn_dy - 1.5, esp_pcb_t])
                cube([4, 3, esp_btn_h]);
        // header strips and Dupont housings under the board
        for (s = [-1, 1]) translate([pin_x0, esp_w / 2 + s * esp_row_pitch / 2 - 1.27, 0]) {
            color("#111") translate([0, 0, -esp_header_spacer]) cube([15 * 2.54, 2.54, esp_header_spacer]);
            color("#333") translate([0, 0, -esp_header_spacer - dupont_len])
                cube([15 * 2.54, 2.54, dupont_len]);
        }
    }
}

module uln(b) {
    translate([uln_x0, uln_y[b], z_uln_bot]) {
        color("#1d6b3a") difference() {
            cube([uln_l, uln_w, uln_pcb_t]);
            for (ix = [uln_hole_ix, uln_l - uln_hole_ix])
                for (iy = [uln_hole_iy, uln_w - uln_hole_iy])
                    translate([ix, iy, -1]) cylinder(d = 3, h = 5);
        }
        translate([0, 0, uln_pcb_t]) {
            color("#111") translate([uln_l / 2 - 3.25, (uln_w - 19.5) / 2, 0]) cube([6.5, 19.5, 4]);
            // IN1-4 + power pins (assumed on the edge facing the ESP32)
            translate([2.2, (uln_w - 6 * 2.54) / 2, 0]) {
                color("#111") cube([2.54, 6 * 2.54, uln_header_spacer]);
                color("#333") translate([0, 0, uln_header_spacer]) cube([2.54, 6 * 2.54, dupont_len]);
            }
            // JST-XH 5-pin, top entry, with plug (assumed on the far edge)
            color("#eeeeee") translate([uln_l - 1 - 5.75, (uln_w - 12.4) / 2, 0])
                cube([5.75, 12.4, jst_mated_h]);
            color("#d33") for (i = [0:3]) translate([uln_l / 2 + 7, 8 + i * 5, 0])
                cylinder(d = 3, h = 5, $fn = 16);
        }
    }
}

module cap() {
    color("#2a2a8a") translate([cradle_x + cradle_len / 2 - cap_l / 2, cradle_y,
                                floor_t + cradle_floor + cap_d / 2])
        rotate([0, 90, 0]) cylinder(d = cap_d, h = cap_l);
}

module plug() {
    color("#444") {
        translate([mouth_x - overmold_len, port_y - plug_w / 2, z_port - plug_h / 2])
            cube([overmold_len, plug_w, plug_h]);
        translate([mouth_x, port_y - 4.15, z_port - 1.25]) cube([plug_metal_len, 8.3, 2.5]);
    }
}

module motor_cables() {
    color("#e0a020") for (y = motor_y)
        box_at(L - wall - tb_x - 6, y - motor_cable_w / 2, z_cable_bot,
               tb_x + wall + 26, motor_cable_w, motor_cable_h - motor_pinch - eps);
}

module bank() {
    color("#2b2f36") translate([0, 0, z_lid_top + eps]) linear_extrude(bank_t)
        rr2_at(bank_x0, bank_y - bank_w / 2, bank_l, bank_w, bank_r);
    bank_cable();
}

module cable_slab(x, z, a) {
    translate([x, port_y, z]) rotate([0, a, 0])
        cube([0.05, bank_cable_w, bank_cable_t], center = true);
}

module bank_cable() {
    zc = (cab_z_bank + z_port) / 2;
    color("#111") {
        hull() { cable_slab(bank_x0, cab_z_bank, 0); cable_slab(cab_xc, cab_z_bank, 0); }
        hull() { cable_slab(plug_end_x, z_port, 0); cable_slab(cab_xc, z_port, 0); }
        for (a = [0:15:165]) hull() {
            cable_slab(cab_xc - cab_R * sin(a), zc + cab_R * cos(a), a);
            cable_slab(cab_xc - cab_R * sin(a + 15), zc + cab_R * cos(a + 15), a + 15);
        }
    }
}

module ring() {
    color("#9aa0a6") translate([ring_x, ring_y, z_lid_top - ring_t])
        difference() {
            cylinder(d = ring_od, h = ring_t - eps, $fn = 120);
            translate([0, 0, -1]) cylinder(d = ring_id, h = ring_t + 2, $fn = 120);
        }
}

module components() {
    esp32(); uln(0); uln(1); cap(); plug(); motor_cables(); ring(); bank();
}

module shape(name) {
    if (name == "base") base();
    else if (name == "lid") lid();
    else if (name == "clip") { if (clip_enable) clip_on_box(); }
    else if (name == "clip_hw") { if (clip_enable) clip_hw(); }
    else if (name == "esp32") esp32();
    else if (name == "uln_a") uln(0);
    else if (name == "uln_b") uln(1);
    else if (name == "cap") cap();
    else if (name == "plug") plug();
    else if (name == "motor_cables") motor_cables();
    else if (name == "bank") bank();
    else if (name == "ring") ring();
}

// ----------------------------------------------------------------------------
// Dispatch
// ----------------------------------------------------------------------------
c_base = "#4a7fc1";
c_lid = "#e8833a";

if (part == "base") base();
else if (part == "lid") translate([0, 0, -z_top]) lid();
else if (part == "clip") clip_part();
else if (part == "plate") {
    // Both parts as printed: base floor-down, lid with the outer face up.
    base();
    translate([0, W + 12, -z_top]) lid();
    if (clip_enable) translate([L + 2 * ear_ext + 6, 0, 0]) rotate([0, 0, 90]) translate([0, -clip_len - 15, 0]) clip_part();
}
else if (part == "assembly") {
    color(c_base) base();
    color(c_lid) lid();
    if (clip_enable) color("#5a5a5a") clip_on_box();
    if (show_parts) { components(); if (clip_enable) color("#999") clip_hw(); }
}
else if (part == "inside") {
    color(c_base) base();
    if (show_parts) { esp32(); uln(0); uln(1); cap(); plug(); motor_cables(); if (clip_enable) color("#999") clip_hw(); }
}
else if (part == "exploded") {
    color(c_base) base();
    translate([0, 0, 30]) { color(c_lid) lid(); if (show_parts) translate([0, 0, 0.6]) ring(); }
    if (show_parts) {
        esp32(); uln(0); uln(1); cap(); motor_cables();
        translate([0, 0, 60]) color("#2b2f36") translate([0, 0, z_lid_top])
            linear_extrude(bank_t) rr2_at(bank_x0, bank_y - bank_w / 2, bank_l, bank_w, bank_r);
    }
}
else if (part == "section") {
    // Cut along the USB / ESP32 centreline to show the stack and cable hairpin.
    difference() {
        union() {
            color(c_base) base();
            color(c_lid) lid();
            if (show_parts) components();
        }
        translate([-60, port_y, -10]) cube([L + 120, W, 80]);
    }
}
else if (part == "check") {
    intersection() { shape(check_a); shape(check_b); }
}
