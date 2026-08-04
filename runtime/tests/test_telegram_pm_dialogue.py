"""
Tests de studio.telegram.pm_dialogue (ADR 0015, phase 1 d'implémentation) —
Claude Code CLI mocké (comme test_pm_node.py), bot Telegram remplacé par un
objet simple qui enregistre les appels (comme test_telegram_handlers.py, pas
de vrais objets aiogram).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import studio.nodes.pm as pm_node
import studio.telegram.confirmations as confirmations_module
import studio.telegram.pm_dialogue as pm_dialogue
from studio.config import StudioConfig
from studio.tools.registry import execute_tool
from studio.telegram.handlers import handle_confirmation_callback

_CHAT_ID = 42
_THREAD_ID = 111
_MESSAGE_ID = 999


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    return repo


@pytest.fixture
def config(tmp_path: Path, repo: Path) -> StudioConfig:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"pm_opus": "claude-opus-4-8"},
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    return StudioConfig(project_name="demo", config_dir=config_dir)


@pytest.fixture(autouse=True)
def _clear_pending_state():
    pm_dialogue._pending_dialogues.clear()
    confirmations_module.pending_confirmations.clear()
    yield
    pm_dialogue._pending_dialogues.clear()
    confirmations_module.pending_confirmations.clear()


@pytest.fixture(autouse=True)
def _dialogues_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirige la persistance du transcript vers un répertoire jetable —
    sans ça, les tests écriraient dans le vrai ~/.devaimazing/dialogues."""
    state_dir = tmp_path / "dialogues"
    monkeypatch.setattr(pm_dialogue, "_DIALOGUES_STATE_DIR", state_dir)
    return state_dir


class _FakeBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.chat_actions: list[dict] = []
        self.reactions: list[dict] = []
        self.edits: list[dict] = []
        self._next_message_id = 1000

    async def send_message(self, chat_id, text, *, message_thread_id=None, reply_markup=None):
        self._next_message_id += 1
        message_id = self._next_message_id
        self.messages.append({
            "chat_id": chat_id, "text": text, "message_id": message_id,
            "message_thread_id": message_thread_id, "reply_markup": reply_markup,
        })
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, text, *, chat_id, message_id, reply_markup=None):
        self.edits.append({
            "chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup,
        })

    async def send_chat_action(self, chat_id, action, *, message_thread_id=None):
        self.chat_actions.append({
            "chat_id": chat_id, "action": action, "message_thread_id": message_thread_id,
        })

    async def set_message_reaction(self, chat_id, message_id, *, reaction=None):
        self.reactions.append({
            "chat_id": chat_id, "message_id": message_id, "reaction": reaction,
        })


def _fake_claude_result(content: str) -> dict:
    return {
        "content": content,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "duration_ms": 500,
    }


VALID_FICHE = (
    "**Nom de la feature** : ajout-panier\n"
    "**Objectif brut** : ajouter un panier\n"
)


async def test_start_feature_dialogue_posts_first_question(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("QUESTION: quel est le nom de la feature ?")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()

    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    # Un placeholder envoyé avant l'appel PM (potentiellement long), édité
    # en place par la question réelle une fois disponible — sans ça, le
    # déclenchement initial d'un dialogue reste silencieux jusqu'à la
    # première question (gap trouvé en usage réel, voir docs/roadmap.md).
    assert len(bot.messages) == 1
    assert bot.messages[0]["message_thread_id"] == _THREAD_ID
    assert bot.messages[0]["text"] == pm_dialogue._PM_PREPARES_QUESTION
    assert len(bot.edits) == 1
    assert "nom de la feature" in bot.edits[0]["text"]
    assert bot.edits[0]["message_id"] == bot.messages[0]["message_id"]
    assert _THREAD_ID in pm_dialogue._pending_dialogues


async def test_start_feature_dialogue_injects_inspiration_sources(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, tmp_path: Path,
):
    """ADR 0016 : le prompt système envoyé au premier tour inclut
    skills/inspiration-sources.md, via le même point de construction que le
    dialogue terminal (pm_node.build_cadrage_system_prompt)."""
    sources_path = tmp_path / "inspiration-sources.md"
    sources_path.write_text("## exemple-source\nURL: https://example.com\n", encoding="utf-8")
    monkeypatch.setattr(pm_node, "_INSPIRATION_SOURCES_PATH", sources_path)

    calls = []

    async def fake_run_claude_code(**kwargs):
        calls.append(kwargs)
        return _fake_claude_result("QUESTION: quel est le nom de la feature ?")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()

    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    assert "exemple-source" in calls[0]["prompt"]


async def test_start_feature_edit_dialogue_seeds_transcript_with_existing_content(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    """Menu "Modifier une feature" (ADR 0015, Décision 7 révisée) : le PM
    voit la fiche actuelle dès le premier tour, au lieu de repartir d'une
    page blanche comme start_feature_dialogue."""
    calls = []

    async def fake_run_claude_code(**kwargs):
        calls.append(kwargs)
        return _fake_claude_result("QUESTION: qu'est-ce qui doit changer ?")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()

    await pm_dialogue.start_feature_edit_dialogue(
        bot, _CHAT_ID, _THREAD_ID, config, "ajout-panier", VALID_FICHE,
    )

    assert len(bot.edits) == 1
    assert "qu'est-ce qui doit changer" in bot.edits[0]["text"]
    assert _THREAD_ID in pm_dialogue._pending_dialogues
    assert "ajout-panier" in calls[0]["prompt"]
    assert VALID_FICHE in calls[0]["prompt"]


async def test_handle_dialogue_reply_without_pending_dialogue_returns_false(
    config: StudioConfig,
):
    bot = _FakeBot()

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "peu importe", _MESSAGE_ID)

    assert consumed is False
    assert bot.messages == []


async def test_handle_dialogue_reply_none_thread_id_returns_false():
    bot = _FakeBot()

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, None, "peu importe", _MESSAGE_ID)

    assert consumed is False


# --- cancel_dialogue (ADR 0015, Décision 6, /stop) ---

async def test_cancel_dialogue_removes_pending_state(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, _dialogues_state_dir: Path,
):
    async def fake_run_claude_code(**kwargs):
        return {
            "content": "QUESTION: quel est le nom de la feature ?",
            "usage": {"input_tokens": 1, "output_tokens": 1}, "duration_ms": 0,
        }

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)
    assert _THREAD_ID in pm_dialogue._pending_dialogues
    assert (_dialogues_state_dir / f"{_THREAD_ID}.json").is_file()

    cancelled = pm_dialogue.cancel_dialogue(_THREAD_ID)

    assert cancelled is True
    assert _THREAD_ID not in pm_dialogue._pending_dialogues
    assert not (_dialogues_state_dir / f"{_THREAD_ID}.json").exists()


def test_cancel_dialogue_without_pending_returns_false():
    assert pm_dialogue.cancel_dialogue(_THREAD_ID) is False


# --- persistance du transcript (reprise après redémarrage du bot) ---

async def test_handle_dialogue_reply_updates_persisted_transcript(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, _dialogues_state_dir: Path,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result("QUESTION: à quoi ça sert ?"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    data = json.loads((_dialogues_state_dir / f"{_THREAD_ID}.json").read_text(encoding="utf-8"))
    assert data["kind"] == "feature"
    assert data["project_name"] == "demo"
    assert any("ajout-panier" in line for line in data["transcript"])
    assert any("à quoi ça sert" in line for line in data["transcript"])


async def test_handle_dialogue_reply_shows_placeholder_then_edits_into_question(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    """Accusé de réception textuel — un placeholder est envoyé dès réception
    de la réponse, avant l'appel PM potentiellement long, puis édité en
    place par la question réelle. Remplace l'ancien mécanisme de réaction
    (👀), jugé moins clair en usage réel (voir docs/roadmap.md)."""
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result("QUESTION: à quoi ça sert ?"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    # bot.messages[0] = placeholder de start_feature_dialogue,
    # bot.messages[1] = placeholder de ce tour-ci.
    assert len(bot.messages) == 2
    assert bot.messages[1]["text"] == pm_dialogue._PM_PREPARES_ANSWER
    assert len(bot.edits) == 2
    assert bot.edits[1]["message_id"] == bot.messages[1]["message_id"]
    assert "à quoi ça sert" in bot.edits[1]["text"]


async def test_handle_dialogue_reply_draft_deletes_persisted_transcript(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, _dialogues_state_dir: Path,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)
    assert (_dialogues_state_dir / f"{_THREAD_ID}.json").is_file()

    await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    assert not (_dialogues_state_dir / f"{_THREAD_ID}.json").exists()


async def test_restore_pending_dialogues_reloads_state_and_allows_continuing(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, _dialogues_state_dir: Path,
):
    _dialogues_state_dir.mkdir(parents=True, exist_ok=True)
    (_dialogues_state_dir / f"{_THREAD_ID}.json").write_text(json.dumps({
        "kind": "feature",
        "trace_id": "run-20260730-000000",
        "project_name": "demo",
        "transcript": [
            "Objectif initial de l'utilisateur : (non précisé).",
            "PM : quel est le nom de la feature ?",
            "Utilisateur : ajout-panier",
        ],
    }), encoding="utf-8")

    pm_dialogue.restore_pending_dialogues(config.config_dir)

    assert _THREAD_ID in pm_dialogue._pending_dialogues
    state = pm_dialogue._pending_dialogues[_THREAD_ID]
    assert state.trace_id == "run-20260730-000000"
    assert state.config.project_name == "demo"

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("QUESTION: à quoi ça sert ?")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "peu importe", _MESSAGE_ID)

    assert consumed is True
    assert "à quoi ça sert" in bot.edits[0]["text"]


async def test_restore_pending_dialogues_skips_unknown_project(
    config: StudioConfig, _dialogues_state_dir: Path,
):
    _dialogues_state_dir.mkdir(parents=True, exist_ok=True)
    (_dialogues_state_dir / f"{_THREAD_ID}.json").write_text(json.dumps({
        "kind": "feature", "trace_id": "run-x", "project_name": "inconnu", "transcript": [],
    }), encoding="utf-8")

    pm_dialogue.restore_pending_dialogues(config.config_dir)

    assert _THREAD_ID not in pm_dialogue._pending_dialogues


def test_restore_pending_dialogues_noop_if_dir_missing(config: StudioConfig):
    pm_dialogue.restore_pending_dialogues(config.config_dir)  # ne doit pas lever

    assert pm_dialogue._pending_dialogues == {}


async def test_handle_dialogue_reply_advances_to_next_question(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result("QUESTION: à quoi ça sert ?"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    assert consumed is True
    # 2 : un placeholder pour le tour initial (start_feature_dialogue), un
    # pour la réponse traitée ici (handle_dialogue_reply) — chacun édité en
    # place par le contenu réel une fois disponible.
    assert len(bot.messages) == 2
    assert len(bot.edits) == 2
    assert "à quoi ça sert" in bot.edits[1]["text"]
    assert _THREAD_ID in pm_dialogue._pending_dialogues


async def test_handle_dialogue_reply_validated_draft_presents_confirmation(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    assert consumed is True
    # Dialogue terminé : plus en attente d'une réponse texte, relayé à la
    # confirmation Oui/Non (même mécanisme que les outils du registre).
    assert _THREAD_ID not in pm_dialogue._pending_dialogues
    # messages[0] = placeholder de start_feature_dialogue (édité en question),
    # messages[1] = placeholder de ce tour (édité en "Fiche prête"),
    # messages[2] = brouillon + confirmation (_present_draft).
    assert len(bot.messages) == 3
    draft_message = bot.messages[2]
    assert VALID_FICHE.strip() in draft_message["text"]
    assert draft_message["reply_markup"] is not None
    assert len(confirmations_module.pending_confirmations) == 1


async def test_handle_dialogue_reply_keeps_dialogue_alive_if_present_draft_fails(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, _dialogues_state_dir: Path,
):
    """Si _present_draft échoue (crash réseau, incident Telegram...), le
    dialogue ne doit pas être perdu : il reste en attente (mémoire ET
    disque), récupérable sans reprendre tout le cadrage — incident réel
    constaté (todolist3, cadrage de gestion-taches, voir docs/roadmap.md),
    où le dépassement de la limite Telegram faisait perdre tout le dialogue."""
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    async def fake_present_draft(*args, **kwargs):
        raise RuntimeError("crash simulé")

    monkeypatch.setattr(pm_dialogue, "_present_draft", fake_present_draft)

    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    with pytest.raises(RuntimeError):
        await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    assert _THREAD_ID in pm_dialogue._pending_dialogues
    data = json.loads((_dialogues_state_dir / f"{_THREAD_ID}.json").read_text(encoding="utf-8"))
    assert any("ajout-panier" in line for line in data["transcript"])


async def test_handle_dialogue_reply_splits_long_draft_across_messages(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    """Une fiche complète peut dépasser la limite Telegram par message
    (4096 caractères) — crash constaté en usage réel avant ce fix
    (todolist3, cadrage de gestion-taches). Le clavier de confirmation doit
    rester joignable, attaché au dernier morceau envoyé."""
    long_fiche = "**Nom de la feature** : ajout-panier\n" + ("x" * 9000)
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{long_fiche}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    assert consumed is True
    # Plus d'un seul message de brouillon (1 question initiale + plusieurs
    # morceaux) — aucun crash "message is too long".
    assert len(bot.messages) > 2
    for message in bot.messages[1:-1]:
        assert len(message["text"]) <= pm_dialogue._TELEGRAM_MESSAGE_LIMIT
        assert message["reply_markup"] is None
    assert bot.messages[-1]["reply_markup"] is not None
    assert len(confirmations_module.pending_confirmations) == 1


async def test_confirming_draft_writes_card_root(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, repo: Path,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)
    await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier", _MESSAGE_ID)

    confirmation_id = next(iter(confirmations_module.pending_confirmations))
    reply_text = await handle_confirmation_callback(
        f"confirm:{confirmation_id}:yes", chat_id=_CHAT_ID, allowed_chat_id=_CHAT_ID, bot=bot,
    )

    assert reply_text is not None
    # Le run_id n'est plus dans _pending_dialogues (nettoyé) — on le retrouve
    # via le seul dossier créé sous specs/ (planification.md, écrit par
    # valider_fiche_feature depuis l'ADR 0015/Décision 4, est un fichier,
    # pas un dossier — exclu ici pour ne cibler que le dossier du run).
    run_dirs = [p for p in (repo / "specs").iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    card_path = run_dirs[0] / "card-root.md"
    assert card_path.read_text(encoding="utf-8") == VALID_FICHE.strip()


# --- start_project_dialogue (ADR 0015, phase 2) ---

VALID_FICHE_PROJET = (
    "**Objectif** : plateforme de gestion de paniers\n"
    "**Utilisateurs cibles** : commerçants\n"
)


async def test_start_project_dialogue_posts_first_question(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("QUESTION: quel est l'objectif du projet ?")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()

    await pm_dialogue.start_project_dialogue(bot, _CHAT_ID, _THREAD_ID, config, "mon-projet")

    assert len(bot.messages) == 1
    assert bot.messages[0]["message_thread_id"] == _THREAD_ID
    assert len(bot.edits) == 1
    assert "objectif du projet" in bot.edits[0]["text"]
    assert _THREAD_ID in pm_dialogue._pending_dialogues
    assert pm_dialogue._pending_dialogues[_THREAD_ID].kind == "project"


async def test_project_dialogue_validated_draft_calls_valider_fiche_projet(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, repo: Path,
):
    responses = [
        _fake_claude_result("QUESTION: quel est l'objectif du projet ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE_PROJET}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_project_dialogue(bot, _CHAT_ID, _THREAD_ID, config, "mon-projet")
    await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "une plateforme de paniers", _MESSAGE_ID)

    assert _THREAD_ID not in pm_dialogue._pending_dialogues
    confirmation_id = next(iter(confirmations_module.pending_confirmations))
    tool_name, args, _ = confirmations_module.pending_confirmations[confirmation_id]
    assert tool_name == "valider_fiche_projet"
    assert "run_id" not in args

    reply_text = await handle_confirmation_callback(
        f"confirm:{confirmation_id}:yes", chat_id=_CHAT_ID, allowed_chat_id=_CHAT_ID, bot=bot,
    )

    assert reply_text is not None
    card_path = repo / "specs" / "fiche-projet.md"
    assert card_path.read_text(encoding="utf-8") == VALID_FICHE_PROJET.strip()
