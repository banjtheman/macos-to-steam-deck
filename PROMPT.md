# Prompts

Paste one of these into Claude Code, Codex, Cursor or any coding agent that can run commands on your Mac, with your game's project open.

## Set everything up (first time)

```text
Set up Deck Delivery (https://github.com/banjtheman/macos-to-steam-deck) so you can put builds of this project on my Steam Deck.

1. If the `deck-delivery` command is missing, clone the repo to ~/.deck-delivery and run `python3 ~/.deck-delivery/deck.py install`. Then read SKILL.md in that folder and follow it from here on.
2. Run `deck-delivery status`. If it is not paired, walk me through it: on the Deck, Settings > System > Enable Developer Mode, then Settings > Developer > Pair new host. Run `deck-delivery pair` once I say the Deck is showing that screen, and wait while I approve it there.
3. Give this project one command that makes a Linux x86-64 build in a folder of its own (for Godot, a Linux export preset and a build script; for Unity, a Linux Standalone build in batch mode). Check the build really produced its files.
4. Write deck_delivery.json in the project root, list it with `deck-delivery add`, and send it with `deck-delivery send <id>`.
5. Tell me where it is on the Deck.
```

## Add another project (the Deck is already paired)

```text
Use deck-delivery (run `deck-delivery guide` if you don't know it) to set this project up for my Steam Deck: one command that makes a Linux x86-64 build in its own folder, a deck_delivery.json in the project root with an id no other project uses (`deck-delivery games` lists them), `deck-delivery add` on it, then send it and tell me where it is on the Deck.
```

## Send the latest build

```text
Send the latest build of this project to my Steam Deck with deck-delivery and tell me what it said.
```

## Something went wrong

```text
The last deck-delivery send failed. Run `deck-delivery status`, read what the failure message means in `deck-delivery guide`, fix what is on this side, and tell me if there is something I need to do on the Deck.
```
