"""Deck Delivery's own checks: no Deck needed, standard library only.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = tempfile.TemporaryDirectory()
# The pairing and the list of projects go to a scratch folder, never ~/.config.
os.environ["DECK_DELIVERY_HOME"] = SCRATCH.name
_spec = importlib.util.spec_from_file_location("deck", ROOT / "deck.py")
assert _spec is not None and _spec.loader is not None
deck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deck)


def write_project(folder: Path, games: list[dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "deck_delivery.json"
    file.write_text(json.dumps({"games": games}))
    return file


class Config(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp())
        if deck.SOURCES_FILE.exists():
            deck.SOURCES_FILE.unlink()

    def test_a_game_needs_a_deck_name_a_folder_and_a_start(self) -> None:
        for bad in ({"id": "9Lives", "folder": "b", "run": "./x"}, {"id": "My Game", "folder": "b", "run": "./x"},
                    {"id": "Steam", "folder": "b", "run": "./x"}, {"id": "Good", "run": "./x"}, {"id": "Good", "folder": "b"}):
            with self.subTest(game=bad), self.assertRaises(deck.Problem):
                deck.read_games(write_project(self.work / "p", [bad]))

    def test_projects_are_listed_found_and_forgotten(self) -> None:
        file = write_project(self.work / "game", [{"id": "MyGame", "name": "My Game", "folder": "build", "run": "./MyGame.x86_64"}])
        deck.add_source(str(file))
        self.assertEqual(deck.game_named("mygame")["id"], "MyGame")
        self.assertEqual(deck.game_named("My Game")["id"], "MyGame")
        self.assertEqual(deck.game_folder(deck.game_named("MyGame")), (self.work / "game" / "build").resolve())
        deck.add_source(str(file), forget=True)
        with self.assertRaises(deck.Problem):
            deck.load_games()

    def test_a_name_on_the_deck_belongs_to_one_game(self) -> None:
        deck.add_source(str(write_project(self.work / "one", [{"id": "Twin", "folder": "b", "run": "./x"}])))
        with self.assertRaises(deck.Problem):
            deck.add_source(str(write_project(self.work / "two", [{"id": "twin", "folder": "b", "run": "./x"}])))

    def test_root_moves_a_game_to_another_folder(self) -> None:
        file = write_project(self.work / "tools", [{"id": "Rooted", "root": "../game", "folder": "build", "run": "./x"}])
        deck.add_source(str(file))
        self.assertEqual(deck.game_root(deck.game_named("Rooted")), (self.work / "game").resolve())

    def test_nothing_is_sent_before_pairing(self) -> None:
        file = write_project(self.work / "p", [{"id": "Unpaired", "folder": "b", "run": "./x"}])
        with self.assertRaises(deck.Problem) as caught:
            deck.send(deck.read_games(file)[0], build=False)
        self.assertIn("Not paired", str(caught.exception))


class SteamOnTheDeck(unittest.TestCase):
    """The part that runs on the Deck (REMOTE_REGISTER), against a pretend Steam
    in a scratch home: the files Steam reads, and a pipe it answers on."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        (self.home / "devkit-game" / "MyGame").mkdir(parents=True)
        steam = self.home / ".steam"
        steam.mkdir()
        (steam / "steam.token").write_text("token123")
        (steam / "steam.pipe").write_text("")
        self.steam = steam

    def register(self, answer: str | None, running: bool = True, settings: dict | None = None) -> dict:
        if running:
            (self.steam / "steam.pid").write_text(str(os.getpid()))
        parms = {"gameid": "MyGame", "argv": ["./MyGame.x86_64", "--fullscreen"], "settings": settings or {"steam_play": "0"}, "wait": 4}
        process = subprocess.Popen([sys.executable, "-", json.dumps(parms)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, env={**os.environ, "HOME": str(self.home)})
        if answer is not None:
            threading.Thread(target=self._answer, args=(answer,), daemon=True).start()
        out, _ = process.communicate(deck.REMOTE_REGISTER, timeout=20)
        return json.loads(out.strip().splitlines()[-1])

    def _answer(self, answer: str) -> None:
        """Steam reads the request off its pipe and answers in the file it names."""
        pipe = self.steam / "steam.pipe"
        for _ in range(100):
            asked = pipe.read_text()
            match = re.search(r"response=([^&\s]+)", asked)
            if match:
                response = urllib.parse.unquote_plus(match.group(1))
                Path(response + (".error" if answer.startswith("!") else "")).write_text(answer.lstrip("!"))
                return
            time.sleep(0.05)

    def test_steam_adds_the_game(self) -> None:
        result = self.register("added")
        self.assertEqual(result, {"success": "added"})
        self.assertIn("devkit-1 steam://devkit-1/token123/create-shortcut?", (self.steam / "steam.pipe").read_text())
        self.assertEqual(json.loads((self.home / "devkit-game" / "MyGame-argv.json").read_text()), ["./MyGame.x86_64", "--fullscreen"])

    def test_settings_are_kept_between_sends(self) -> None:
        (self.home / "devkit-game" / "MyGame-settings.json").write_text(json.dumps({"kept": "1"}))
        self.register("added", settings={"steam_play": "1"})
        self.assertEqual(json.loads((self.home / "devkit-game" / "MyGame-settings.json").read_text()), {"kept": "1", "steam_play": "1"})

    def test_steam_saying_no_is_reported(self) -> None:
        self.assertIn("Steam refused: bad id", self.register("!bad id").get("error", ""))

    def test_no_steam_is_reported(self) -> None:
        self.assertIn("Steam is not running", self.register(None, running=False).get("error", ""))


class Page(unittest.TestCase):
    """The page only answers itself: not another site, not another host name."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), deck.Page)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def call(self, path: str, host: str = "localhost", data: bytes | None = None, headers: dict | None = None) -> int:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers={"Host": f"{host}:{self.port}", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    def test_the_page_opens_for_this_mac(self) -> None:
        self.assertEqual(self.call("/"), 200)

    def test_another_host_name_is_turned_away(self) -> None:
        self.assertEqual(self.call("/", host="attacker.example"), 403)
        self.assertEqual(self.call("/api/state", host="attacker.example"), 403)

    def test_changes_need_the_page_s_own_header(self) -> None:
        self.assertEqual(self.call("/api/nothing", data=b"{}"), 403)
        self.assertEqual(self.call("/api/nothing", data=b"{}", headers={"X-Deck-Delivery": "1"}), 404)


class Command(unittest.TestCase):
    def test_version(self) -> None:
        out = subprocess.run([sys.executable, str(ROOT / "deck.py"), "--version"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(out.stdout.strip(), f"deck-delivery {deck.__version__}")

    def test_no_command_prints_the_usage(self) -> None:
        out = subprocess.run([sys.executable, str(ROOT / "deck.py")], capture_output=True, text=True, env=os.environ)
        self.assertEqual(out.returncode, 1)
        self.assertIn("deck-delivery send", out.stdout)


if __name__ == "__main__":
    unittest.main()
