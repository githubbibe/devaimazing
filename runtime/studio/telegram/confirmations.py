"""
Primitives de confirmation Oui/Non partagées entre studio.telegram.handlers
(confirmation d'un outil du registre) et studio.telegram.pm_dialogue
(confirmation de la fiche produite par le dialogue de cadrage PM, ADR 0015)
— extraites dans ce module séparé pour que les deux puissent le réutiliser
sans import circulaire (handlers importe pm_dialogue, pas l'inverse ; les
deux importent celui-ci).
"""

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_PREFIX = "confirm"

# Confirmations en attente : mémoire du process, jamais persistées (cohérent
# avec l'absence de checkpointer dédié pour Devaimazing, voir ADR 0013,
# Décision 3) — perdues si le bot redémarre entre la question et la
# réponse ; un Oui tapé après un redémarrage retombe sur "confirmation
# expirée", à retaper depuis la commande.
pending_confirmations: dict[str, tuple[str, dict[str, Any], Any]] = {}


def build_confirmation_keyboard(confirmation_id: str) -> InlineKeyboardMarkup:
    """Clavier Oui/Non attaché au message de confirmation."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Oui", callback_data=f"{CALLBACK_PREFIX}:{confirmation_id}:yes"
        ),
        InlineKeyboardButton(
            text="Non", callback_data=f"{CALLBACK_PREFIX}:{confirmation_id}:no"
        ),
    ]])
