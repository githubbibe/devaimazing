"""Tests de studio.telegram.citations (accusé de réception "en cours" du
bot Telegram, demande explicite de l'utilisateur, 2026-08-05)."""

from studio.telegram.citations import CITATIONS, pick_citation


def test_citations_all_non_empty_strings():
    assert len(CITATIONS) > 0
    assert all(isinstance(c, str) and c.strip() for c in CITATIONS)


def test_pick_citation_returns_one_of_the_citations():
    assert pick_citation() in CITATIONS
