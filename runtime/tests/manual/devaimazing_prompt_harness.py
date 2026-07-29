"""
Harness manuel de validation empirique du prompt Devaimazing (ADR 0013,
tranche S4). Ce fichier n'est PAS collecté par pytest (pas de préfixe/suffixe
"test_") : il appelle un vrai serveur Ollama local (podman, voir
infra/ollama/), pas un mock — non reproductible en CI, à lancer à la main :

    cd runtime && uv run python tests/manual/devaimazing_prompt_harness.py

Utilisé le 2026-07-29 pour valider studio.devaimazing.agent.build_system_prompt
contre les 7 outils réels de TOOL_REGISTRY (voir docs/roadmap.md pour les
scores obtenus et la méthode). Conservé dans le dépôt plutôt que dans un
scratchpad temporaire pour pouvoir reproduire la mesure si le prompt ou le
registre évoluent — un fichier de scratchpad équivalent a déjà été perdu une
fois dans la session qui a produit ce harness.
"""

import asyncio

from ollama import AsyncClient
from studio.devaimazing.agent import build_system_prompt, parse_devaimazing_turn
from studio.tools.ollama import DEVAIMAZING_TURN_SCHEMA
from studio.tools.registry import TOOL_REGISTRY

MODEL = "gemma3:4b"
NUM_CTX = 4096

# (question, nom d'outil attendu ou None si conversationnel). Les deux
# derniers cas sont délibérément ambigus/limites (voir docs/roadmap.md) :
# ils ne comptent pas comme des échecs francs s'ils ne matchent pas.
CASES = [
    ("Quel est le statut du run run-042 ?", "lire_statut"),
    ("Ou en est le run run-007 ?", "lire_statut"),
    (
        "Peux-tu me donner un diagnostic detaille du run run-abc123, je veux "
        "savoir si je dois intervenir ?",
        "lire_progression",
    ),
    (
        "J'ai besoin de la progression detaillee du run run-555 pour voir si "
        "une intervention manuelle est necessaire.",
        "lire_progression",
    ),
    ("Liste-moi tous les projets.", "lister_projets"),
    ("Quels sont les projets existants ?", "lister_projets"),
    ("Cree un nouveau projet qui s'appelle 'super-app'.", "creer_projet"),
    ("Archive le projet 'vieux-projet', il n'est plus utilise.", "archive_projet"),
    ("Annule le checkpoint en attente du run run-042.", "reject_checkpoint"),
    ("Arrete tout de suite le run run-999.", "stop_run"),
    ("Bonjour, comment vas-tu ?", None),
    ("Merci pour ton aide !", None),
    ("Explique-moi ce que tu fais.", None),  # ambigu : question meta sur l'outillage
    ("Quel est le statut du projet le plus recent ?", "lire_statut"),  # ambigu : pas de run_id
]


async def _ask(client: AsyncClient, system: str, question: str, expected):
    resp = await client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        format=DEVAIMAZING_TURN_SCHEMA,
        options={"num_ctx": NUM_CTX},
    )
    content = resp["message"]["content"]
    print(f"--- {question} (attendu: {expected})")
    print(content)
    try:
        _reply, tool_call = parse_devaimazing_turn(content)
    except ValueError as exc:
        print("PARSE FAILED:", exc)
        return expected, "PARSE_FAIL"
    got_name = tool_call["name"] if tool_call else None
    if got_name is not None and got_name not in TOOL_REGISTRY:
        print(f"tool_call.name = {got_name} -> NOM INCONNU DU REGISTRE")
    else:
        print("tool_call.name =", got_name, "-> ", "OK" if got_name == expected else "MISMATCH")
    return expected, got_name


async def main():
    system = build_system_prompt()
    client = AsyncClient()
    results = [await _ask(client, system, question, expected) for question, expected in CASES]

    print("\n=== RESUME ===")
    n_ok = sum(1 for expected, got in results if got == expected)
    for (question, _), (expected, got) in zip(CASES, results):
        status = "OK" if got == expected else "MISMATCH"
        print(f"{status:10s} attendu={str(expected):20s} obtenu={str(got):20s} | {question}")
    print(f"\nScore: {n_ok}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
