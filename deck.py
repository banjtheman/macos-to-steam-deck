#!/usr/bin/env python3
"""Deck Delivery: send a game build from this Mac to a Steam Deck on the same
network and put it in the Deck's library, with no trip to Desktop Mode. Made
for macOS, and made to be driven by a coding agent (SKILL.md).

The Deck already knows how to take builds from a development machine: with
Developer Mode on it runs Valve's devkit service, which pairs with a host,
takes files over ssh, and tells the running Steam client to add the game
("Devkit Game: <id>" in the library, launchable from Gaming Mode). Valve's own
client for that is Windows and Linux only. This is a small Mac client for the
same protocol (read from the open-source client, MIT): standard library only,
with the ssh, ssh-keygen and rsync that macOS ships.

    deck-delivery ui                 # a page: pick a game, Send to Deck
    deck-delivery send <game>        # build it, send it, put it in the library
    deck-delivery send <game> --no-build
    deck-delivery games              # what this Mac can send
    deck-delivery add <file>         # list a project's games (its deck_delivery.json)
    deck-delivery forget <file>      # stop listing them
    deck-delivery status             # is the Deck there, are we paired, what's on it
    deck-delivery pair [address]     # once per Deck (it asks on the Deck's screen)
    deck-delivery guide              # the reference: fields, setup, what goes wrong
    deck-delivery ui --port 8791     # the page on another port
    deck-delivery install            # put the command on the PATH and the skill where agents look
    deck-delivery uninstall          # take them away again (the pairing stays)
    deck-delivery --version

README.md beside this file is the reference (`guide` prints it).

Pairing puts an ssh key for this Mac on the Deck, which is full control of
it, the same as Valve's tool. The key stays in ~/.config/deck-delivery/.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__version__ = "1.0.0"

HERE = Path(__file__).resolve().parent
GAMES_FILE = HERE / "games.json"
# DECK_DELIVERY_HOME moves the pairing somewhere else (the tests use a scratch folder).
CONFIG = Path(os.environ.get("DECK_DELIVERY_HOME") or Path.home() / ".config" / "deck-delivery")
KEY = CONFIG / "devkit_rsa"
DECK_FILE = CONFIG / "deck.json"
# Other projects' game files, by path: each project keeps its own.
SOURCES_FILE = CONFIG / "sources.json"
# Valve's client takes only these names for a devkit title.
GAME_ID = r"[A-Za-z_][A-Za-z0-9_.]+"
DEVKIT_PORT = 32000
SERVICE = "_steamos-devkit._tcp"
# What Valve's client appends to the key it registers; the service checks it.
MAGIC_PHRASE = "900b919520e4cf601998a71eec318fec"
UI_PORT = 8790

# Runs on the Deck, fed over ssh: the same three things Valve's
# `steam-client-create-shortcut` does. Save how the game starts where the
# Steam client looks for it, ask the running client to add it, wait for its
# answer.
REMOTE_REGISTER = r'''
import json, os, sys, tempfile, time, urllib.parse
parms = json.loads(sys.argv[1])
home = os.path.expanduser("~")
game = parms["gameid"]
root = os.path.join(home, "devkit-game")
out = {}
try:
    assert os.path.isdir(os.path.join(root, game)), "the game's folder is not on the Deck"
    with open(os.path.join(root, game + "-argv.json"), "w") as f:
        json.dump(parms["argv"], f)
    settings_path = os.path.join(root, game + "-settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            settings = json.load(open(settings_path))
        except ValueError:
            settings = {}
    settings.update(parms.get("settings", {}))
    with open(settings_path, "w") as f:
        json.dump(settings, f)
    try:
        os.kill(int(open(os.path.join(home, ".steam", "steam.pid")).read()), 0)
    except Exception:
        raise RuntimeError("Steam is not running on the Deck, so it could not be added to the library. The files are in place.")
    folder = tempfile.mkdtemp(prefix="create-shortcut")
    response = os.path.join(folder, "registered")
    token = open(os.path.join(home, ".steam", "steam.token")).read()
    command = "create-shortcut?response=%s&gameid=%s" % (urllib.parse.quote_plus(response), game)
    with open(os.path.realpath(os.path.join(home, ".steam", "steam.pipe")), "wb+", 0) as pipe:
        pipe.write(("devkit-1 steam://devkit-1/%s/%s\n" % (token, command)).encode("utf-8"))
    for _ in range(int(parms.get("wait", 10))):
        time.sleep(1)
        if os.path.exists(response + ".error"):
            raise RuntimeError("Steam refused: " + open(response + ".error").read().strip())
        if os.path.exists(response) and not os.path.exists(response + ".lock"):
            out["success"] = open(response).read().strip() or "added"
            break
    else:
        raise RuntimeError("Steam did not answer. Is Developer Mode still on?")
except Exception as error:
    out["error"] = str(error)
print(json.dumps(out))
'''


class Problem(Exception):
    """Something the person at the keyboard can act on."""


def say(line: str = "") -> None:
    print(line, flush=True)


# --- What this Mac knows ---

def game_files() -> list[Path]:
    files = [GAMES_FILE] if GAMES_FILE.exists() else []
    if SOURCES_FILE.exists():
        files += [Path(path) for path in json.loads(SOURCES_FILE.read_text()) if Path(path).exists()]
    return files


def read_games(file: Path) -> list[dict]:
    try:
        games = json.loads(file.read_text()).get("games", [])
    except ValueError as error:
        raise Problem(f"{file} is not valid JSON: {error}") from error
    for game in games:
        if not re.fullmatch(GAME_ID, str(game.get("id", ""))) or str(game.get("id", "")).lower() == "steam":
            raise Problem(f"{file}: a game's id is its name on the Deck: letters, digits, _ and ., starting with a letter (got {game.get('id')!r}).")
        for key in ("folder", "run"):
            if not game.get(key):
                raise Problem(f"{file}: {game['id']} needs a \"{key}\".")
        game["_file"] = str(file)
    return games


def load_games() -> list[dict]:
    games: list[dict] = []
    for file in game_files():
        for game in read_games(file):
            twin = next((other for other in games if other["id"].lower() == game["id"].lower()), None)
            if twin:
                raise Problem(f"Two games are called {game['id']}: one in {twin['_file']}, one in {file}. Ids are the names on the Deck, so each needs its own.")
            games.append(game)
    if not games:
        raise Problem("No games listed yet. Give a project a deck_delivery.json and list it: deck-delivery add <file> (deck-delivery guide shows the form).")
    return games


def add_source(given: str, forget: bool = False) -> None:
    file = Path(given).expanduser().resolve()
    sources = json.loads(SOURCES_FILE.read_text()) if SOURCES_FILE.exists() else []
    if forget:
        sources = [path for path in sources if Path(path) != file]
        say(f"No longer listing {file}.")
    else:
        if not file.is_file():
            raise Problem(f"{file} is not a file.")
        listed = read_games(file)
        if not listed:
            raise Problem(f"{file} lists no games (it needs a \"games\" list).")
        # A name already taken by another file is refused before anything is written.
        for game in listed:
            for other in game_files():
                if other != file and any(theirs["id"].lower() == game["id"].lower() for theirs in read_games(other)):
                    raise Problem(f"{game['id']} is already the name of a game in {other}. Ids are the names on the Deck, so each needs its own.")
        if str(file) not in sources and file != GAMES_FILE:
            sources.append(str(file))
        say(f"Listing {', '.join(game['id'] for game in listed)} from {file}.")
    CONFIG.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(sources, indent=1) + "\n")


def game_named(name: str) -> dict:
    games = load_games()
    for game in games:
        if name.lower() in (game["id"].lower(), str(game.get("name", "")).lower()):
            return game
    raise Problem(f"No game called {name!r}. Listed: {', '.join(g['id'] for g in games) or 'none'}.")


def game_root(game: dict) -> Path:
    """A game's paths start from the folder its own file is in."""
    return (Path(game["_file"]).parent / game.get("root", ".")).resolve()


def game_folder(game: dict) -> Path:
    return (game_root(game) / game["folder"]).resolve()


def load_deck() -> dict:
    return json.loads(DECK_FILE.read_text()) if DECK_FILE.exists() else {}


def save_deck(deck: dict) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    DECK_FILE.write_text(json.dumps(deck, indent=1) + "\n")


# --- Finding the Deck ---

def _dns_sd(arguments: list[str], seconds: float) -> str:
    """macOS's Bonjour browser runs until stopped; give it a moment and read what it found."""
    try:
        process = subprocess.Popen(["dns-sd", *arguments], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        return ""
    time.sleep(seconds)
    process.terminate()
    return process.communicate()[0]


def find_decks() -> list[dict]:
    """Decks in Developer Mode announce their devkit service on the network."""
    found = []
    for line in _dns_sd(["-B", SERVICE, "local."], 3.0).splitlines():
        match = re.search(r"\sAdd\s+\d+\s+\d+\s+\S+\s+" + re.escape(SERVICE) + r"\.\s+(.+)$", line)
        if not match:
            continue
        name = match.group(1).strip()
        where = re.search(r"can be reached at (\S+?)\.?:(\d+)", _dns_sd(["-L", name, SERVICE, "local."], 2.0))
        if not where:
            continue
        host = where.group(1)
        try:
            address = socket.gethostbyname(host)
        except OSError:
            address = host
        if not any(deck["address"] == address for deck in found):
            found.append({"name": name, "host": host, "address": address})
    return found


def port_open(address: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def deck_login(address: str) -> str:
    try:
        with urllib.request.urlopen(f"http://{address}:{DEVKIT_PORT}/properties.json", timeout=4) as reply:
            return str(json.loads(reply.read()).get("login") or "deck")
    except (OSError, ValueError):
        return "deck"


# --- Talking to it ---

def ssh_options() -> list[str]:
    return ["-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={CONFIG / 'known_hosts'}"]


def ssh(deck: dict, command: str, feed: str | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", *ssh_options(), f"{deck['login']}@{deck['address']}", command],
                          input=feed, capture_output=True, text=True, timeout=timeout)


def paired_deck() -> dict:
    deck = load_deck()
    if not deck or not KEY.exists():
        raise Problem("Not paired with a Deck yet. On the Deck: Settings > Developer > Pair new host. Then run: deck-delivery pair")
    return deck


def can_log_in(deck: dict) -> bool:
    try:
        return ssh(deck, "true", timeout=12).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def reach(deck: dict) -> bool:
    """True when this Mac can log in. The router may have given the Deck a new
    address since pairing: if the old one is silent, look for the Deck again
    and remember where it is now."""
    if can_log_in(deck):
        return True
    for found in find_decks():
        moved = {**deck, "address": found["address"]}
        if found["address"] != deck["address"] and can_log_in(moved):
            deck["address"] = found["address"]
            save_deck(deck)
            say(f"The Deck is at {deck['address']} now.")
            return True
    return False


def pair(given: str | None) -> dict:
    address = given or ""
    if not address:
        say("Looking for a Deck in Developer Mode...")
        decks = find_decks()
        if not decks:
            raise Problem("No Deck answered. Is it awake, on this network, with Developer Mode on (Settings > System)? "
                          "Its address is under Settings > Internet: give it here (the address box, or deck-delivery pair <address>).")
        address = str(decks[0]["address"])
        say(f"Found {decks[0]['name']} at {address}.")
    if not port_open(address, DEVKIT_PORT):
        raise Problem(f"{address} is not taking devkit connections (port {DEVKIT_PORT}). Turn on Developer Mode: Settings > System > Enable Developer Mode.")
    CONFIG.mkdir(parents=True, exist_ok=True)
    if not KEY.exists():
        comment = f"devkit-client:{getpass.getuser()}@{socket.gethostname()}"
        subprocess.run(["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-N", "", "-C", comment, "-f", str(KEY)], check=True)
    public = KEY.with_suffix(".pub").read_text().strip()
    body = (public + " " + MAGIC_PHRASE + "\n").encode("ascii")
    say("Asking the Deck to pair. Approve it on the Deck's screen (Settings > Developer > Pair new host must be showing).")
    request = urllib.request.Request(f"http://{address}:{DEVKIT_PORT}/register", data=body, headers={"Content-Type": "text/plain"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            reply.read()
    except urllib.error.HTTPError as error:
        raise Problem(f"The Deck turned the pairing down ({error.code}). Open Settings > Developer > Pair new host on the Deck and try again.") from error
    except OSError as error:
        raise Problem(f"The Deck did not answer the pairing request ({error}).") from error
    deck = {"address": address, "login": deck_login(address)}
    save_deck(deck)
    if not can_log_in(deck):
        raise Problem("The Deck took the request but this Mac still cannot log in. Was it approved on the Deck's screen?")
    say(f"Paired with the Deck at {address} (user {deck['login']}).")
    return deck


def status() -> dict:
    deck = load_deck()
    state = {"paired": bool(deck) and KEY.exists(), "address": deck.get("address", ""), "reachable": False, "login": False, "on_deck": []}
    if not state["paired"]:
        state["found"] = find_decks()
        return state
    state["login"] = reach(deck)
    state["address"] = deck["address"]
    state["reachable"] = state["login"] or port_open(deck["address"], 22) or port_open(deck["address"], DEVKIT_PORT)
    if state["login"]:
        listing = ssh(deck, "ls -1 devkit-game 2>/dev/null")
        state["on_deck"] = sorted(line for line in listing.stdout.splitlines() if line and not line.endswith(".json"))
    return state


def send(game: dict, build: bool = True) -> None:
    deck = paired_deck()
    started = time.time()
    if build and game.get("build"):
        say(f"Building {game['id']}: {game['build']}")
        built = subprocess.run(game["build"], shell=True, cwd=game_root(game))
        if built.returncode != 0:
            raise Problem(f"The build failed (exit {built.returncode}); nothing was sent.")
    folder = game_folder(game)
    if not folder.is_dir() or not any(folder.iterdir()):
        raise Problem(f"Nothing to send: {folder} is missing or empty. Build it first.")
    argv = shlex.split(game["run"])
    if not (folder / argv[0]).exists():
        raise Problem(f"{argv[0]} is not in {folder}; check the game's \"run\" in {game['_file']}.")
    if not reach(deck):
        raise Problem(f"Cannot reach the Deck (last seen at {deck['address']}). Is it awake and on this network?")
    remote = f"devkit-game/{game['id']}"
    made = ssh(deck, f"mkdir -p {shlex.quote(remote)}")
    if made.returncode != 0:
        raise Problem("Could not make the game's folder on the Deck: " + made.stderr.strip())
    size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    say(f"Sending {game['id']} ({size / 1048576:.0f} MB on disk; only what changed is sent).")
    rsh = " ".join(shlex.quote(part) for part in ["ssh", *ssh_options()])
    sent = subprocess.run(["rsync", "-a", "--delete", "--progress", "-e", rsh, f"{folder}/", f"{deck['login']}@{deck['address']}:{remote}/"])
    if sent.returncode != 0:
        raise Problem(f"The transfer failed (rsync exit {sent.returncode}). The Deck's old copy may be part updated; send again.")
    ssh(deck, f"chmod +x {shlex.quote(remote + '/' + argv[0])}")
    # As Valve's client sets it: Steam Play (Proton) is for a Windows build, off for a Linux one.
    settings = {"steam_play": "1" if game.get("proton") else "0", **game.get("settings", {})}
    parms = {"gameid": game["id"], "directory": f"/home/{deck['login']}/{remote}", "argv": argv, "settings": settings}
    answer = ssh(deck, "python3 - " + shlex.quote(json.dumps(parms)), feed=REMOTE_REGISTER, timeout=40)
    try:
        result = json.loads(answer.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise Problem("The files are on the Deck, but adding it to the library gave no answer: " + (answer.stderr.strip() or answer.stdout.strip() or "nothing"))
    if "error" in result:
        raise Problem("The files are on the Deck, but: " + result["error"])
    say(f"Done in {time.time() - started:.0f}s. On the Deck: Library > Non-Steam > Devkit Game: {game['id']}")


# --- Being a command on this Mac ---

SKILL_HOMES = [Path.home() / ".claude" / "skills", Path.home() / ".codex" / "skills", Path.home() / ".cursor" / "skills"]


def uninstall() -> None:
    """Takes away what install put down. The pairing in ~/.config/deck-delivery
    stays: delete that folder to forget the Deck on this Mac too."""
    command = Path.home() / ".local" / "bin" / "deck-delivery"
    if command.exists() and "deck.py" in command.read_text():
        command.unlink()
        say(f"Removed {command}")
    for home in SKILL_HOMES:
        skill = home / "deck-delivery" / "SKILL.md"
        if skill.exists():
            skill.unlink()
            try:
                skill.parent.rmdir()
            except OSError:
                pass
            say(f"Removed {skill}")
    say(f"The pairing is still in {CONFIG}: delete that folder to forget the Deck on this Mac.")


def install() -> None:
    """A `deck-delivery` command on the PATH, and the skill wherever an agent
    on this Mac looks for skills (Claude Code, Codex and Cursor share the form)."""
    command = Path.home() / ".local" / "bin" / "deck-delivery"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text(f'#!/bin/sh\nexec python3 {shlex.quote(str(Path(__file__).resolve()))} "$@"\n')
    command.chmod(0o755)
    say(f"Command: {command}")
    if str(command.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        say(f"  {command.parent} is not on your PATH yet: add it to your shell profile.")
    skill = (HERE / "SKILL.md").read_text()
    for home in SKILL_HOMES:
        if home.is_dir():
            (home / "deck-delivery").mkdir(exist_ok=True)
            (home / "deck-delivery" / "SKILL.md").write_text(skill)
            say(f"Skill:   {home / 'deck-delivery' / 'SKILL.md'}")


# --- The page ---

class Job:
    """One send at a time, with what it printed."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.running = False
        self.title = ""
        self.ok: bool | None = None

    def start(self, title: str, arguments: list[str]) -> bool:
        if self.running:
            return False
        self.lines, self.running, self.title, self.ok = [], True, title, None
        threading.Thread(target=self._run, args=(arguments,), daemon=True).start()
        return True

    def _run(self, arguments: list[str]) -> None:
        process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), *arguments],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:  # rsync redraws its progress with carriage returns: keep the last of each
            self.lines.append(line.rstrip("\n").split("\r")[-1])
            del self.lines[:-400]
        self.ok = process.wait() == 0
        self.running = False


JOB = Job()
STATUS = {"at": 0.0, "value": {}}


def cached_status(fresh: bool = False) -> dict:
    if fresh or time.time() - STATUS["at"] > 20:
        STATUS["value"], STATUS["at"] = status(), time.time()
    return STATUS["value"]


class Page(BaseHTTPRequestHandler):
    def _from_this_mac(self) -> bool:
        """Only the page itself may use the page. A request has to name this
        server as its host, so no other site can reach it under a name of its
        own; and anything that sends or pairs has to carry the page's own
        header, which a browser will not add for another site without asking
        first (this server never says yes)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("localhost", "127.0.0.1", "::1")

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._from_this_mac():
            self._json({"error": "forbidden"}, 403)
            return
        if self.path.startswith("/api/state"):
            try:
                games = [{"id": g["id"], "name": g.get("name", g["id"]), "builds": bool(g.get("build")),
                          "ready": game_folder(g).is_dir() and any(game_folder(g).iterdir()),
                          "built": max((f.stat().st_mtime for f in game_folder(g).rglob("*") if f.is_file()), default=0)} for g in load_games()]
                problem = ""
            except Problem as error:
                games, problem = [], str(error)
            self._json({"games": games, "problem": problem, "deck": cached_status("fresh" in self.path),
                        "job": {"running": JOB.running, "title": JOB.title, "ok": JOB.ok, "lines": JOB.lines[-120:]}})
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._from_this_mac() or self.headers.get("X-Deck-Delivery") != "1":
            self._json({"error": "forbidden"}, 403)
            return
        try:
            asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            asked = {}
        if self.path == "/api/send":
            arguments = ["send", str(asked.get("game", ""))] + ([] if asked.get("build", True) else ["--no-build"])
            self._json({"started": JOB.start("Sending " + str(asked.get("game", "")), arguments)})
        elif self.path == "/api/pair":
            STATUS["at"] = 0.0
            self._json({"started": JOB.start("Pairing", ["pair"] + ([str(asked["address"])] if asked.get("address") else []))})
        else:
            self._json({"error": "unknown"}, 404)

    def log_message(self, format: str, *args) -> None:  # quiet: the page asks every second
        del format, args


def ui(port: int = UI_PORT) -> None:
    say(f"Deck Delivery: http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Page).serve_forever()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deck Delivery</title>
<style>
  :root { --bg: #14121c; --card: #1e1b2a; --line: #2e2a40; --text: #ece6d6; --soft: #a49eb4; --gold: #e2c27a; --good: #7fc8a0; --bad: #e08a78; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.45 system-ui, -apple-system, sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 28px 16px 64px; }
  h1 { font-size: 24px; margin: 0 0 14px; color: var(--gold); }
  .deck { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; margin-bottom: 18px; display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--bad); flex: none; }
  .dot.on { background: var(--good); }
  .deck .what { flex: 1; min-width: 200px; }
  .deck small { color: var(--soft); display: block; }
  .game { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; margin: 10px 0; display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
  .game .name { flex: 1; min-width: 200px; font-size: 18px; }
  .game small { color: var(--soft); display: block; font-size: 13.5px; }
  button { font: inherit; font-weight: 700; font-size: 15px; border-radius: 9px; padding: 10px 16px; cursor: pointer; border: 0; background: var(--gold); color: #1a1622; }
  button.ghost { background: transparent; color: var(--gold); border: 1px solid var(--gold); font-weight: 600; }
  button:disabled { opacity: .4; cursor: default; }
  input { font: inherit; background: #15131f; color: var(--text); border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; width: 170px; }
  pre { background: #0e0d14; border: 1px solid var(--line); border-radius: 10px; padding: 12px; font: 13px/1.5 ui-monospace, Menlo, monospace; color: #cfc8dc; white-space: pre-wrap; max-height: 320px; overflow: auto; margin: 8px 0 0; }
  h2 { font-size: 16px; margin: 22px 0 0; color: var(--soft); font-weight: 600; }
  h2.ok { color: var(--good); } h2.bad { color: var(--bad); }
  ol { color: var(--soft); margin: 6px 0 0; padding-left: 20px; }
  .problem { color: var(--bad); }
</style>
</head>
<body>
<main>
  <h1>Deck Delivery</h1>
  <div class="deck" id="deck"></div>
  <div id="games"></div>
  <h2 id="jobtitle" hidden></h2>
  <pre id="log" hidden></pre>
</main>
<script>
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function post(path, body) { return fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Deck-Delivery": "1"}, body: JSON.stringify(body)}); }
function ago(seconds) { if (!seconds) return "not built yet"; const m = (Date.now() / 1000 - seconds) / 60; return m < 2 ? "built just now" : m < 90 ? `built ${Math.round(m)} min ago` : m < 2880 ? `built ${Math.round(m / 60)} h ago` : `built ${Math.round(m / 1440)} days ago`; }
let lastKey = "";
function draw(state) {
  const key = JSON.stringify([state.games, state.deck, state.job.running, state.problem]);
  if (key !== lastKey) {
    lastKey = key;
    const deck = document.getElementById("deck"); deck.replaceChildren();
    const d = state.deck, ready = d.paired && d.login;
    deck.append(el("span", "dot" + (ready ? " on" : "")));
    const what = el("div", "what");
    if (ready) { what.append(document.createTextNode("Deck ready"), el("small", null, d.address + (d.on_deck.length ? " · on it: " + d.on_deck.join(", ") : ""))); }
    else if (d.paired) { what.append(document.createTextNode("Deck not answering"), el("small", null, "Paired with " + d.address + ". Is it awake and on this network?")); }
    else {
      what.append(document.createTextNode("Not paired with a Deck yet"));
      const steps = el("ol");
      ["Settings > System > Enable Developer Mode", "Settings > Developer > Pair new host", "Press Pair here, then approve it on the Deck"].forEach(s => steps.append(el("li", null, s)));
      what.append(steps);
      if ((d.found || []).length) what.append(el("small", null, "Found on the network: " + d.found.map(f => f.name + " (" + f.address + ")").join(", ")));
    }
    deck.append(what);
    if (!d.paired) {
      const address = el("input"); address.placeholder = "address (optional)";
      const go = el("button", null, "Pair"); go.disabled = state.job.running;
      go.onclick = () => post("/api/pair", {address: address.value.trim()});
      deck.append(address, go);
    } else {
      const again = el("button", "ghost", "Check again"); again.onclick = () => refresh(true); deck.append(again);
      if (!ready) { const redo = el("button", "ghost", "Pair again"); redo.disabled = state.job.running; redo.onclick = () => post("/api/pair", {}); deck.append(redo); }
    }
    const games = document.getElementById("games"); games.replaceChildren();
    if (state.problem) games.append(el("p", "problem", state.problem));
    state.games.forEach(g => {
      const row = el("div", "game");
      const name = el("div", "name", g.name); name.append(el("small", null, g.id + " · " + ago(g.built)));
      row.append(name);
      if (g.builds) {
        const full = el("button", null, "Build and send"); full.disabled = state.job.running || !ready;
        full.onclick = () => post("/api/send", {game: g.id, build: true}); row.append(full);
      }
      const quick = el("button", g.builds ? "ghost" : null, g.builds ? "Send as built" : "Send to Deck"); quick.disabled = state.job.running || !ready || !g.ready;
      quick.onclick = () => post("/api/send", {game: g.id, build: false}); row.append(quick);
      games.append(row);
    });
  }
  const title = document.getElementById("jobtitle"), log = document.getElementById("log");
  const shown = state.job.title !== "";
  title.hidden = log.hidden = !shown;
  if (shown) {
    title.textContent = state.job.title + (state.job.running ? "..." : state.job.ok ? ": done" : ": stopped");
    title.className = state.job.running ? "" : state.job.ok ? "ok" : "bad";
    const text = state.job.lines.join("\n");
    if (log.textContent !== text) { log.textContent = text; log.scrollTop = log.scrollHeight; }
  }
}
let wasRunning = false;
function refresh(fresh) {
  fetch("/api/state" + (fresh ? "?fresh" : "")).then(r => r.json()).then(state => {
    draw(state);
    if (wasRunning && !state.job.running) refresh(true);
    wasRunning = state.job.running;
  }).catch(() => {});
}
refresh(true);
setInterval(() => refresh(false), 1000);
</script>
</body>
</html>
"""


def main(arguments: list[str]) -> int:
    command = arguments[0] if arguments else ""
    try:
        if command in ("--version", "version"):
            say(f"deck-delivery {__version__}")
        elif command == "ui":
            port = int(arguments[arguments.index("--port") + 1]) if "--port" in arguments[:-1] else UI_PORT
            ui(port)
        elif command == "pair":
            pair(arguments[1] if len(arguments) > 1 else None)
        elif command == "send" and len(arguments) > 1:
            send(game_named(arguments[1]), build="--no-build" not in arguments)
        elif command in ("add", "forget") and len(arguments) > 1:
            add_source(arguments[1], forget=command == "forget")
        elif command == "guide":
            say((HERE / "README.md").read_text())
        elif command == "install":
            install()
        elif command == "uninstall":
            uninstall()
        elif command == "games":
            for game in load_games():
                say(f"{game['id']:<24} {game.get('name', '')}  ({game_folder(game)}; listed in {game['_file']})")
        elif command == "status":
            state = status()
            if not state["paired"]:
                say("Not paired. Decks in Developer Mode on this network: " + (", ".join(f"{d['name']} ({d['address']})" for d in state.get("found", [])) or "none found"))
            else:
                say(f"Paired with {state['address']}: " + ("ready" if state["login"] else "reachable, but login failed" if state["reachable"] else "not answering"))
                if state["on_deck"]:
                    say("On the Deck: " + ", ".join(state["on_deck"]))
        else:
            say(__doc__)
            return 1
    except Problem as problem:
        say(str(problem))
        return 1
    return 0


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit("Deck Delivery needs Python 3.9 or newer (macOS's own python3 is fine).")
    sys.exit(main(sys.argv[1:]))
