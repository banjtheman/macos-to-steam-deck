#!/bin/bash
# Makes the Linux build the Steam Deck runs, in build/linux and nothing else
# (Deck Delivery sends that folder as it is).
#
# Copy to tools/build_linux.sh in a Unity project. Needs the Linux Build
# Support module for the project's Unity version (Unity Hub > Installs > Add
# modules), and the Editor closed: batch mode cannot open a project that is
# already open.
#
#   bash tools/build_linux.sh
#   UNITY=/Applications/Unity/Hub/Editor/<version>/Unity.app/Contents/MacOS/Unity GAME=MyGame bash tools/build_linux.sh
set -uo pipefail
P="$(cd "$(dirname "$0")/.." && pwd)"
GAME="${GAME:-MyGame}"
U="${UNITY:-}"
if [ -z "$U" ]; then
	# The Editor the project was made with, as Unity Hub installs it.
	version=$(sed -n 's/^m_EditorVersion: //p' "$P/ProjectSettings/ProjectVersion.txt")
	U="/Applications/Unity/Hub/Editor/$version/Unity.app/Contents/MacOS/Unity"
fi
OUT="$P/build/linux"
LOG="$P/build/build_linux.log"
rm -rf "$OUT"
mkdir -p "$OUT"
"$U" -batchmode -quit -nographics -projectPath "$P" -buildTarget Linux64 \
	-buildLinux64Player "$OUT/$GAME.x86_64" -logFile "$LOG"
if [ $? -ne 0 ] || [ ! -s "$OUT/$GAME.x86_64" ]; then
	echo "build_linux: the build failed (see build/build_linux.log)"
	exit 1
fi
# Debug folders Unity leaves beside the player: the Deck does not need them.
rm -rf "$OUT"/*_BurstDebugInformation_DoNotShip "$OUT"/*_BackUpThisFolder_ButDontShipIt
echo "build_linux: $(du -sh "$OUT" | cut -f1) in build/linux"
