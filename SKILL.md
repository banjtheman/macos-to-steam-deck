---
name: deck-delivery
description: Send a game build from this Mac to the user's Steam Deck over the local network and add it to the Deck's library with the `deck-delivery` command (no Desktop Mode, no manual copying). Use when the user asks to put, send, deploy, install or test a build on the Steam Deck or "the Deck", to set a project up for Deck testing, to pair a Deck, or when a Deck send fails.
---

# Deck Delivery

`deck-delivery` builds a game, sends it to the user's Steam Deck over the network, and adds it to the Deck's library as "Devkit Game: <id>", launchable from Gaming Mode. Only what changed is sent, so repeat sends take seconds.

## If the command is missing

```bash
git clone https://github.com/banjtheman/macos-to-steam-deck ~/.deck-delivery
python3 ~/.deck-delivery/deck.py install
```

`install` puts the command in `~/.local/bin`. If that folder is not on the PATH, run `~/.local/bin/deck-delivery` directly and tell the user to add it to their shell profile.

## Send a game that is already set up

```bash
deck-delivery games                 # what can be sent, and which file lists it
deck-delivery send <id>             # build (if the game has a build command), send, add to the library
deck-delivery send <id> --no-build  # send what is already built
```

Success ends with `Done in Ns. On the Deck: Library > Non-Steam > Devkit Game: <id>`. Report that line, or the message printed instead.

## Pair a Deck (once per Mac, needs the user)

Run `deck-delivery status` first. If it is not paired, the user has to act on the Deck, so ask them to:

1. Settings > System > Enable Developer Mode.
2. Settings > Developer > Pair new host, and leave that screen open.

When they say it is showing, run `deck-delivery pair` (or `deck-delivery pair <address>` with the address from the Deck's Settings > Internet if it is not found) and tell them to approve the request on the Deck. Never pair without the user knowing: pairing gives this Mac full control of the Deck.

## Set a project up (once)

1. Make one command that produces a Linux x86-64 build the Deck can run, in a folder of its own:
   - Godot: a Linux export preset, then `godot --headless --path . --export-release "<preset>" build/linux/<Game>.x86_64`. Run `godot --headless --path . --import` first so new files are imported, and check the files were really made: an export can report success and make nothing. The repo's `examples/godot/build_linux.sh` does all of this.
   - Unity: Linux Build Support installed in Unity Hub, then a batch-mode build with `-buildTarget Linux64 -buildLinux64Player build/linux/<Game>.x86_64` (`examples/unity/build_linux.sh`).
   - Anything else: whatever makes a Linux x86-64 build in one folder.
2. Write `deck_delivery.json` in the project's root:

```json
{
  "games": [
    {"id": "MyGame", "name": "My Game", "build": "bash tools/build_linux.sh", "folder": "build/linux", "run": "./MyGame.x86_64"}
  ]
}
```

- `id`: the name in the Deck's library. Letters, digits, `_` and `.`, starting with a letter. Unique across every project on this Mac (`deck-delivery games` lists the ones taken).
- `folder`: what is sent, mirrored: a file removed here is removed on the Deck. Keep it to the build alone, never the project root.
- `run`: the start command, run inside that folder.
- `build` (optional): a command run in `root` before sending; if it fails nothing is sent. `root` (optional): the project folder relative to this file; the default is the file's own folder.
- `proton: true` for a Windows build, run through Proton. The Linux build is the tested path.

3. List it, then send:

```bash
deck-delivery add /absolute/path/to/deck_delivery.json
deck-delivery send MyGame
```

## Rules

- Do not touch `~/.config/deck-delivery/`: the pairing is the user's. Do not pair again unless the user asks or `status` says the pairing is gone.
- Edit only this project's `deck_delivery.json`. Do not change the tool or another project's file.
- A send replaces the Deck's copy of that game. Saves live in the game's own user folder on the Deck and are untouched.
- "Cannot reach the Deck" means it is asleep or off the network: tell the user to wake it instead of retrying in a loop.
- `deck-delivery status` shows whether the Deck answers and what is on it. `deck-delivery guide` prints the full reference, including what each failure message means.
