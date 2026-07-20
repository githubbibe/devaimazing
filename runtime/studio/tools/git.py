"""
Opérations Git pour devaimazing.

Gère les commits par agent (identité Git distincte par agent), un commit à la fin
de chaque tâche terminée (pas seulement en phase 10), le nommage et la création
de branches de run, et le merge final vers develop.
"""

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from studio.tools.tracer import AgentTracer

AGENT_GIT_IDENTITIES = {
    "pm":        ("pm-aimazing",        "pm@aimazing.fr"),
    "architect": ("architect-aimazing", "architect@aimazing.fr"),
    "back":      ("back-aimazing",      "back@aimazing.fr"),
    "front":     ("front-aimazing",     "front@aimazing.fr"),
    "test":      ("test-aimazing",      "test@aimazing.fr"),
    "security":  ("security-aimazing",  "security@aimazing.fr"),
}

# Fiche (specs/<run-id>/<fichier>) -> agent propriétaire, pour attribuer
# correctement un commit de sauvegarde automatique (voir checkout_branch) à
# l'identité Git de l'agent concerné plutôt qu'à une identité générique.
# back-tu/front-tu partagent l'identité Git de back/front (voir docs/agents.md).
_CARD_FILENAME_TO_AGENT = {
    "card-root.md": "pm",
    "architect-brief.md": "architect",
    "back.md": "back",
    "back-tu.md": "back",
    "front.md": "front",
    "front-tu.md": "front",
    "test.md": "test",
    "secu.md": "security",
}

# Rôle tel qu'émis dans le champ "agent" d'un événement trace.jsonl
# (studio.tools.tracer.AgentTracer.for_agent) -> identité Git (voir
# _CARD_FILENAME_TO_AGENT pour le même regroupement back-tu/front-tu ->
# back/front, secu -> security).
_TRACE_ROLE_TO_AGENT = {
    "pm": "pm",
    "architect": "architect",
    "back": "back",
    "back-tu": "back",
    "front": "front",
    "front-tu": "front",
    "test": "test",
    "security": "security",
    "secu": "security",
}


def _infer_agent_from_trace(trace_path: Path) -> Optional[str]:
    """
    Déduit l'agent propriétaire d'un trace.jsonl depuis le champ "agent" de
    son dernier événement — contrairement aux fiches, un fichier de trace
    n'a pas de nom par agent, mais chaque événement en émet un (voir
    studio.tools.tracer.AgentTracer).
    """
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        return _TRACE_ROLE_TO_AGENT.get(event.get("agent"))
    return None


def _infer_owning_agent(repo_path: Path, path: str) -> Optional[str]:
    """Déduit l'agent propriétaire d'un chemin dirty, sinon `None`."""
    name = Path(path).name
    if name == "trace.jsonl":
        return _infer_agent_from_trace(repo_path / path)
    return _CARD_FILENAME_TO_AGENT.get(name)


async def _dirty_paths(repo_path: Path) -> list[str]:
    """
    Liste les chemins (fichiers modifiés, ajoutés, supprimés ou non
    trackés) issus de `git status --porcelain --untracked-files=all` — pour
    un rename (`R  ancien -> nouveau`), seul le chemin `nouveau` est retenu.

    `--untracked-files=all` (plutôt que le défaut `normal`) : sans lui, un
    dossier entièrement nouveau et jamais commité (ex. specs/<run-id>/ d'un
    run qui a planté avant le premier commit de phase) est listé comme une
    seule entrée `?? specs/<run-id>/` — les fiches à l'intérieur ne sont
    alors plus attribuables individuellement à leur agent. Sans risque de
    perf ici : cette fonction ne s'exécute que sur le repo *cible* piloté
    par devaimazing, pas un repo arbitraire (voir docs/roadmap.md 2026-07-16).

    N'utilise pas `_run_git` : son `.strip()` global mangerait l'espace de
    tête de la toute première ligne quand son statut d'index est vide (ex.
    `" M fichier"`, modification non indexée), décalant de un caractère le
    parsing en position fixe (colonnes 0-1 statut, 2 espace, 3+ chemin) —
    bug trouvé en test (2026-07-16, voir docs/roadmap.md).
    """
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo_path), "status", "--porcelain", "--untracked-files=all",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"Commande git échouée (code {process.returncode}) : git status --porcelain\n"
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )
    paths = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        entry = line[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip('"'))
    return paths


async def _run_git(repo_path: Path, *args: str, env: Optional[dict] = None) -> str:
    """
    Exécute une commande git dans repo_path et retourne stdout (strippé).

    Raises:
        RuntimeError: Si git retourne un code de sortie non nul.
    """
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo_path), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"Commande git échouée (code {process.returncode}) : "
            f"git {' '.join(args)}\n{stderr.decode('utf-8', errors='replace').strip()}"
        )
    return stdout.decode("utf-8", errors="replace").strip()


def slugify_feature_name(feature_name: str) -> str:
    """
    Transforme le nom de feature fourni par l'utilisateur en slug utilisable
    dans un nom de branche.

    Args:
        feature_name: Nom brut fourni par l'utilisateur en phase 1.

    Returns:
        Slug en minuscules, espaces remplacés par des tirets, caractères
        spéciaux retirés.

    Example:
        >>> slugify_feature_name("Features Qui Fait Tout")
        "features-qui-fait-tout"
    """
    slug = feature_name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def generate_branch_name(feature_name: str) -> str:
    """
    Génère le nom de branche complet pour un run.

    Format : studio/<slug-feature>-<hash5>
    Le hash est calculé sur (timestamp + nom de la feature) pour garantir
    l'unicité même en cas de noms de feature identiques ou proches.

    Args:
        feature_name: Nom brut fourni par l'utilisateur en phase 1.

    Returns:
        Nom de branche complet, ex: "studio/features-qui-fait-tout-a3f9c".

    Example:
        >>> generate_branch_name("features qui fait tout")
        "studio/features-qui-fait-tout-a3f9c"
    """
    slug = slugify_feature_name(feature_name)
    digest_input = f"{time.time()}{feature_name}".encode("utf-8")
    digest = hashlib.sha1(digest_input).hexdigest()[:5]
    return f"studio/{slug}-{digest}"


async def checkout_branch(repo_path: Path, branch: str) -> list[str]:
    """
    Bascule sur `branch` dans le repo projet.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        branch: Nom de la branche à checkout.

    Returns:
        Liste des hash de commit de sauvegarde créés si le worktree était
        sale (voir Side effects) — liste vide si le worktree était déjà
        propre.

    Raises:
        RuntimeError: Si le checkout échoue (branche inexistante, ou
            modifications locales encore en conflit avec `branch` après la
            sauvegarde automatique — ne devrait plus arriver pour le cas
            simple "fichiers modifiés/non trackés", seulement pour des cas
            hors scope, ex. rebase en cours).

    Side effects:
        Si le worktree contient des modifications non commitées (typiquement
        la trace/fiche d'un run précédent interrompu en cours de nœud, avant
        son propre commit_as_agent — trouvé en run, voir docs/roadmap.md
        2026-07-16), elles sont d'abord sauvegardées : un commit par agent
        propriétaire identifiable (voir _CARD_FILENAME_TO_AGENT pour les
        fiches, ex. specs/<run-id>/back.md -> identité back-aimazing ; pour
        un trace.jsonl, l'agent est déduit du champ "agent" de son dernier
        événement, voir _infer_agent_from_trace — respecte la convention
        "chaque agent assure son commit", voir docs/agents.md), puis un
        commit unique sous l'identité `devaimazing-bootstrap` pour les
        fichiers restants dont l'agent propriétaire ne peut pas être déduit
        (ex. trace.jsonl sans événement exploitable, fichiers de code déjà
        écrits). Tout ça avant le checkout, plutôt que de faire échouer le
        run avec une erreur Git brute. Change ensuite la branche courante
        du repo projet.

    Notes:
        Appelé au tout début d'un nouveau run (`cli.py::_run_async`, avant
        la phase 1), pas à la reprise (`_resume_async`) — un run repris est
        potentiellement déjà sur sa propre branche de feature, la basculer
        de force vers base_branch serait destructeur. Comble un gap réel
        trouvé en run (2026-07-11, voir docs/roadmap.md) : sans ce garde-fou,
        les commits des phases 1/2 (avant que create_run_branch ne crée la
        branche du run, en fin de phase 3) atterrissent sur la branche
        laissée par un run précédent si le repo n'a pas été remis sur
        base_branch entre deux runs — perdus dès que create_run_branch
        rebascule sur base_branch pour créer la nouvelle branche.
    """
    paths = await _dirty_paths(repo_path)
    if not paths:
        await _run_git(repo_path, "checkout", branch)
        return []

    by_agent: dict[Optional[str], list[str]] = {}
    for path in paths:
        by_agent.setdefault(_infer_owning_agent(repo_path, path), []).append(path)

    commit_hashes = []
    for agent, files in by_agent.items():
        if agent is None:
            continue
        name, email = AGENT_GIT_IDENTITIES[agent]
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        await _run_git(repo_path, "add", "--", *files)
        await _run_git(
            repo_path, "commit", "-m",
            f"chore: sauvegarde du run précédent avant changement de branche ({agent})",
            env=env,
        )
        commit_hashes.append(await _run_git(repo_path, "rev-parse", "HEAD"))

    remaining = by_agent.get(None, [])
    if remaining:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "devaimazing-bootstrap",
            "GIT_AUTHOR_EMAIL": "bootstrap@aimazing.fr",
            "GIT_COMMITTER_NAME": "devaimazing-bootstrap",
            "GIT_COMMITTER_EMAIL": "bootstrap@aimazing.fr",
        }
        await _run_git(repo_path, "add", "--", *remaining)
        await _run_git(
            repo_path, "commit", "-m",
            "chore: sauvegarde de fichiers non attribuables à un agent (run précédent)",
            env=env,
        )
        commit_hashes.append(await _run_git(repo_path, "rev-parse", "HEAD"))

    await _run_git(repo_path, "checkout", branch)
    return commit_hashes


async def create_run_branch(repo_path: Path, feature_name: str, base_branch: str = "develop") -> str:
    """
    Crée la branche du run à partir de base_branch.

    Appelée au démarrage effectif du run (fin de phase 3, fiches dépendantes
    écrites), jamais pendant le dialogue de cadrage de la phase 1.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        feature_name: Nom de la feature tel que fourni par l'utilisateur.
        base_branch: Branche de base à partir de laquelle créer la branche du run.

    Returns:
        Nom de la branche créée.

    Raises:
        RuntimeError: Si la création de branche échoue (conflit, permissions).

    Side effects:
        Crée une branche Git dans le repo projet.
    """
    branch_name = generate_branch_name(feature_name)
    await _run_git(repo_path, "checkout", base_branch)
    await _run_git(repo_path, "checkout", "-b", branch_name)
    return branch_name


async def commit_as_agent(
    repo_path: Path,
    agent: str,
    message: str,
    files: list[str],
    tracer: Optional[AgentTracer] = None,
) -> str:
    """
    Crée un commit dans le repo projet au nom d'un agent, à la fin de sa tâche.

    Appelé après chaque tâche d'agent terminée (phases 4 à 9), pas uniquement
    en phase 10. Chaque commit constitue un point de restauration en cas
    d'échec ou de renvoi ultérieur dans le run.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        agent: Nom de l'agent (doit être dans AGENT_GIT_IDENTITIES).
        message: Message de commit (format conventional commits).
        files: Liste des fichiers à inclure dans le commit (chemins relatifs au repo).
        tracer: AgentTracer optionnel (voir tools.tracer) — émet un
            événement "commit" (hash, identité git, fichiers) une fois le
            commit créé. `None` (défaut) : aucune trace émise.

    Returns:
        Hash du commit créé.

    Raises:
        ValueError: Si agent inconnu ou files vide.
        RuntimeError: Si le commit Git échoue.

    Side effects:
        Crée un commit dans le repo projet, sur la branche du run courant.

    Example:
        >>> hash = await commit_as_agent(
        ...     repo_path=Path("/home/user/code/aimazing/webaimazing-v2"),
        ...     agent="back",
        ...     message="feat: add login endpoint stub",
        ...     files=["backend/auth/endpoints.py"],
        ... )
    """
    if agent not in AGENT_GIT_IDENTITIES:
        raise ValueError(
            f"Agent inconnu : {agent!r}. Attendu l'un de {sorted(AGENT_GIT_IDENTITIES)}"
        )
    if not files:
        raise ValueError("La liste de fichiers à committer ne peut pas être vide")

    name, email = AGENT_GIT_IDENTITIES[agent]
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }

    await _run_git(repo_path, "add", "--", *files)
    await _run_git(repo_path, "commit", "-m", message, env=env)
    commit_hash = await _run_git(repo_path, "rev-parse", "HEAD")
    if tracer is not None:
        tracer.emit("commit", hash=commit_hash, git_identity=agent, files=files)
    return commit_hash


async def merge_run_branch(repo_path: Path, branch_name: str, target_branch: str = "develop") -> str:
    """
    Merge la branche du run vers la branche cible, en phase 10 après validation finale.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        branch_name: Nom de la branche du run à merger.
        target_branch: Branche cible (develop par défaut).

    Returns:
        Hash du commit de merge.

    Raises:
        RuntimeError: Si le merge échoue (conflit).

    Side effects:
        Merge la branche dans target_branch. Ne supprime pas la branche du run
        (conservée pour traçabilité et audit). Utilise checkout_branch (pas un
        checkout brut) : le node closer écrit son propre trace.jsonl
        (tracer.emit("node_enter", ...)) juste avant d'appeler cette fonction,
        ce qui salit systématiquement la branche du run et ferait échouer un
        `git checkout` nu — trouvé en run (2026-07-20, voir docs/roadmap.md),
        déjà contourné manuellement une fois avant ce fix (commit
        78536ca sur todo-list2, "trace et fiche non commitées").
    """
    await checkout_branch(repo_path, target_branch)
    await _run_git(
        repo_path, "merge", "--no-ff", branch_name,
        "-m", f"merge: {branch_name} into {target_branch}",
    )
    return await _run_git(repo_path, "rev-parse", "HEAD")


async def init_repo(repo_path: Path, initial_branch: str = "develop") -> None:
    """
    Initialise un nouveau repo Git vide dans repo_path, sur initial_branch.

    Args:
        repo_path: Chemin absolu vers le dossier (doit déjà exister, vide ou non).
        initial_branch: Nom de la branche initiale (develop par défaut, cohérent
            avec git.base_branch de config/studio.yml).

    Raises:
        RuntimeError: Si git init échoue (repo_path inexistant, déjà un repo Git).

    Side effects:
        Crée un répertoire .git dans repo_path.

    Notes:
        Le ref de la branche n'existe pas tant qu'aucun commit n'a été fait
        (voir create_initial_commit) — un `checkout`/`push` avant ce premier
        commit échouerait.
    """
    await _run_git(repo_path, "init", "-b", initial_branch)


async def create_initial_commit(repo_path: Path, project_name: str) -> str:
    """
    Crée le commit initial (README.md minimal) d'un repo tout juste initialisé.

    Args:
        repo_path: Chemin absolu vers le repo projet (déjà initialisé via init_repo).
        project_name: Nom du projet, utilisé dans le contenu du README et le message.

    Returns:
        Hash du commit créé.

    Raises:
        RuntimeError: Si le commit échoue.

    Side effects:
        Crée/écrase repo_path/README.md et crée un commit sur la branche courante.

    Notes:
        Identité Git dédiée (devaimazing-bootstrap), distincte de
        AGENT_GIT_IDENTITIES : ce commit ne représente le travail d'aucun agent,
        seulement l'initialisation du repo par la commande `new-project`.
    """
    (repo_path / "README.md").write_text(f"# {project_name}\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "devaimazing-bootstrap",
        "GIT_AUTHOR_EMAIL": "bootstrap@aimazing.fr",
        "GIT_COMMITTER_NAME": "devaimazing-bootstrap",
        "GIT_COMMITTER_EMAIL": "bootstrap@aimazing.fr",
    }
    await _run_git(repo_path, "add", "README.md")
    await _run_git(repo_path, "commit", "-m", f"chore: initialise {project_name}", env=env)
    return await _run_git(repo_path, "rev-parse", "HEAD")


async def create_github_remote(repo_path: Path, name: str, private: bool = True) -> None:
    """
    Crée le repo GitHub distant via `gh repo create` et l'ajoute comme remote `origin`.

    Args:
        repo_path: Chemin absolu vers le repo projet local (source).
        name: Nom du repo à créer côté GitHub.
        private: Visibilité du repo créé (privé par défaut).

    Raises:
        RuntimeError: Si `gh` échoue (non authentifié, nom déjà pris, etc.)

    Side effects:
        Crée un repo distant sur GitHub (action visible, persistante, hors de ce
        process) et ajoute un remote `origin` au repo local.

    Notes:
        Ne pousse aucune branche (voir push_branch séparément) — ce découplage
        permet à l'appelant de confirmer explicitement chaque effet de bord côté
        GitHub avant de l'exécuter, plutôt qu'une seule confirmation couvrant
        création + push en un geste opaque.
    """
    visibility = "--private" if private else "--public"
    process = await asyncio.create_subprocess_exec(
        "gh", "repo", "create", name, visibility,
        "--source", str(repo_path), "--remote", "origin",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"Échec de la création du repo GitHub {name!r} : "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )


async def push_branch(repo_path: Path, branch: str, remote: str = "origin") -> None:
    """
    Pousse branch vers remote en configurant le suivi amont (-u).

    Args:
        repo_path: Chemin absolu vers le repo projet.
        branch: Nom de la branche à pousser.
        remote: Nom du remote (origin par défaut).

    Raises:
        RuntimeError: Si le push échoue (remote absent, réseau, permissions).

    Side effects:
        Pousse des commits vers un serveur Git distant.
    """
    await _run_git(repo_path, "push", "-u", remote, branch)
