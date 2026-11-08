#!/usr/bin/env bash
# Export printable parts, render previews, and run interference checks.
# Usage: ./build.sh [extra openscad -D overrides...]
#   e.g. ./build.sh -D bank_l=98.5 -D bank_w=64
set -euo pipefail
cd "$(dirname "$0")"

SCAD=belt_enclosure.scad
OSC=(openscad --backend=manifold "$@")
mkdir -p stl renders

echo "== Export"
"${OSC[@]}" -D 'part="base"' -o stl/belt_box_base.stl "$SCAD" 2>&1 | grep -E "ECHO|WARNING|ERROR" || true
"${OSC[@]}" -D 'part="lid"'  -o stl/belt_box_lid.stl  "$SCAD" 2>&1 | grep -E "WARNING|ERROR" || true
"${OSC[@]}" -D 'part="clip"' -o stl/belt_box_clip.stl "$SCAD" 2>&1 | grep -E "WARNING|ERROR" || true
"${OSC[@]}" -D 'part="plate"' -o stl/belt_box_all_parts.stl "$SCAD" 2>&1 | grep -E "WARNING|ERROR" || true
"${OSC[@]}" --enable=lazy-union -D 'part="plate"' -o stl/belt_box_plate.3mf "$SCAD" 2>&1 | grep -E "WARNING|ERROR" || true

echo "== Renders"
R=(--imgsize=1600,1100 --colorscheme=Tomorrow --viewall --autocenter)
"${OSC[@]}" "${R[@]}" -D 'part="assembly"' --camera=0,0,0,62,0,35,0 --projection=p -o renders/assembly.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" -D 'part="assembly"' --camera=0,0,0,62,0,215,0 --projection=p -o renders/assembly_port_end.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" -D 'part="exploded"' --camera=0,0,0,65,0,30,0 --projection=p -o renders/exploded.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" -D 'part="inside"' --camera=0,0,0,40,0,20,0 --projection=p -o renders/inside.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" -D 'part="inside"' -D show_parts=false --camera=0,0,0,45,0,200,0 --projection=p -o renders/base_only.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" -D 'part="section"' --camera=0,0,0,90,0,180,0 --projection=o -o renders/section.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" --render -D 'part="lid"' --camera=0,0,0,55,0,-35,0 --projection=p -o renders/lid_print.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" --render -D 'part="plate"' --camera=0,0,0,40,0,20,0 --projection=p -o renders/print_plate.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" -D 'part="assembly"' --camera=0,0,0,105,0,200,0 --projection=p -o renders/clip_side.png "$SCAD" 2>/dev/null
"${OSC[@]}" "${R[@]}" --render -D 'part="base"' --camera=0,0,0,180,0,0,0 --projection=o -o renders/base_back.png "$SCAD" 2>/dev/null

echo "== Interference checks (zero-volume touching = contact, anything thicker = FAIL)"
PAIRS="base:esp32 base:uln_a base:uln_b base:cap base:plug base:motor_cables base:lid
lid:esp32 lid:uln_a lid:uln_b lid:cap lid:plug lid:motor_cables lid:bank lid:ring
esp32:uln_a esp32:uln_b esp32:cap uln_a:uln_b uln_a:cap uln_b:cap
uln_a:motor_cables uln_b:motor_cables plug:cap
clip:base clip_hw:base clip_hw:esp32 clip_hw:uln_a clip_hw:uln_b clip_hw:cap"
# esp32:plug is skipped on purpose: the plug sits inside the receptacle.
fail=0; tmp=$(mktemp -d)
for p in $PAIRS; do
  a=${p%%:*}; b=${p##*:}
  rm -f "$tmp/sum.json"
  "${OSC[@]}" --summary all --summary-file "$tmp/sum.json" -D 'part="check"' \
      -D "check_a=\"$a\"" -D "check_b=\"$b\"" -o "$tmp/check.stl" "$SCAD" >/dev/null 2>&1 || true
  verdict=$(python3 - "$tmp/sum.json" <<'PY'
import json, sys
try:
    g = json.load(open(sys.argv[1])).get("geometry", {})
except Exception:
    print("pass  (no overlap)"); sys.exit()
size = (g.get("bounding_box") or {}).get("size")
if not size:
    print("pass  (no overlap)")
elif min(size) < 0.02:
    print("pass  (surface contact)")
else:
    print("FAIL  overlap %.2f x %.2f x %.2f mm" % tuple(size))
PY
)
  printf "  %-14s %s\n" "$a vs $b" "$verdict"
  [[ $verdict == FAIL* ]] && fail=1
done
rm -rf "$tmp"
exit $fail
