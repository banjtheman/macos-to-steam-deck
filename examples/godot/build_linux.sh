#!/bin/bash
# Makes the Linux build the Steam Deck runs, in build/linux and nothing else
# (Deck Delivery sends that folder as it is).
#
# Copy to tools/build_linux.sh in a Godot 4 project. Needs a Linux export
# preset (Project > Export > Add... > Linux) and Godot's export templates.
#
#   bash tools/build_linux.sh
#   GODOT=/Applications/Godot.app/Contents/MacOS/Godot PRESET=Linux GAME=MyGame bash tools/build_linux.sh
set -uo pipefail
P="$(cd "$(dirname "$0")/.." && pwd)"
G="${GODOT:-godot}"
PRESET="${PRESET:-Linux}"
GAME="${GAME:-MyGame}"
OUT="$P/build/linux"
LOG="$P/build/build_linux.log"
mkdir -p "$OUT"
started=$(date +%s)
# New files are imported first: an export does not do it.
"$G" --headless --path "$P" --import > "$LOG" 2>&1 </dev/null
"$G" --headless --path "$P" --export-release "$PRESET" "$OUT/$GAME.x86_64" >> "$LOG" 2>&1 </dev/null
# An export can say it worked and make nothing: look for what it should have made.
if [ ! -s "$OUT/$GAME.x86_64" ] || [ "$(stat -f %m "$OUT/$GAME.x86_64")" -lt "$started" ]; then
	echo "build_linux: $GAME.x86_64 was not made (see build/build_linux.log)"
	exit 1
fi
echo "build_linux: $(du -sh "$OUT" | cut -f1) in build/linux"
