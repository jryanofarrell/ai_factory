"""Tests for the chain-grouping logic (ADR-019)."""

from __future__ import annotations

import pytest

from factory.chains import (
    ChainCycleError,
    group_into_chains,
)
from factory.ticket import Ticket


def _t(tid: str, repo: str = "thms-platform", deps: list[str] | None = None) -> Ticket:
    return Ticket(
        id=tid,
        title=f"ticket {tid}",
        target_repo=repo,
        acceptance_criteria="- x",
        depends_on=deps or [],
    )


# ---------- Happy paths ----------

def test_single_independent_ticket() -> None:
    result = group_into_chains([_t("A")])
    assert result.chains == [[_t("A")]]
    assert result.skipped_unsatisfied == []
    assert result.skipped_cross_repo == []


def test_two_independent_tickets_form_separate_chains() -> None:
    a, b = _t("A"), _t("B")
    result = group_into_chains([a, b])
    assert len(result.chains) == 2
    assert [t.id for chain in result.chains for t in chain] == ["A", "B"]


def test_linear_chain_three_tickets() -> None:
    a = _t("A")
    b = _t("B", deps=["A"])
    c = _t("C", deps=["B"])
    result = group_into_chains([a, b, c])
    assert len(result.chains) == 1
    assert [t.id for t in result.chains[0]] == ["A", "B", "C"]


def test_chain_order_independent_of_queue_order() -> None:
    """Even if user queues C, A, B with C deps on B and B deps on A, run order is A→B→C."""
    a = _t("A")
    b = _t("B", deps=["A"])
    c = _t("C", deps=["B"])
    result = group_into_chains([c, a, b])
    assert [t.id for t in result.chains[0]] == ["A", "B", "C"]


def test_diamond_chain_one_pr() -> None:
    """D depends on B and C; B and C both depend on A. One chain, topo-sorted."""
    a = _t("A")
    b = _t("B", deps=["A"])
    c = _t("C", deps=["A"])
    d = _t("D", deps=["B", "C"])
    result = group_into_chains([a, b, c, d])
    assert len(result.chains) == 1
    order = [t.id for t in result.chains[0]]
    assert order[0] == "A"
    assert order[-1] == "D"
    assert set(order[1:3]) == {"B", "C"}


# ---------- Merged-on-main deps ----------

def test_dep_on_main_treated_as_satisfied() -> None:
    """B depends on A which is already merged. B runs alone as its own chain."""
    b = _t("B", deps=["A"])
    result = group_into_chains([b], merged_ticket_ids={"A"})
    assert len(result.chains) == 1
    assert [t.id for t in result.chains[0]] == ["B"]
    assert result.skipped_unsatisfied == []


def test_dep_neither_in_queue_nor_merged_skips() -> None:
    """B depends on A but A is missing entirely. B is skipped with the missing dep listed."""
    b = _t("B", deps=["A"])
    result = group_into_chains([b])
    assert result.chains == []
    assert len(result.skipped_unsatisfied) == 1
    skipped_ticket, missing = result.skipped_unsatisfied[0]
    assert skipped_ticket.id == "B"
    assert missing == ["A"]


def test_partial_satisfaction_skips_dependent_only() -> None:
    """A independent ticket runs; B depends on missing C and is skipped."""
    a = _t("A")
    b = _t("B", deps=["C"])
    result = group_into_chains([a, b])
    assert [chain[0].id for chain in result.chains] == ["A"]
    assert result.skipped_unsatisfied[0][0].id == "B"


# ---------- Cross-repo refusal ----------

def test_cross_repo_dep_refuses_to_run() -> None:
    """A in repo X, B in repo Y depending on A. B is refused (cross-repo)."""
    a = _t("A", repo="repo-x")
    b = _t("B", repo="repo-y", deps=["A"])
    result = group_into_chains([a, b])
    # A still runs in its own repo
    a_chain_ids = [chain[0].id for chain in result.chains]
    assert "A" in a_chain_ids
    # B is refused
    assert len(result.skipped_cross_repo) == 1
    refused, bad_deps = result.skipped_cross_repo[0]
    assert refused.id == "B"
    assert bad_deps == ["A"]


# ---------- Cycles ----------

def test_cycle_raises() -> None:
    a = _t("A", deps=["B"])
    b = _t("B", deps=["A"])
    with pytest.raises(ChainCycleError, match="A.*B|B.*A"):
        group_into_chains([a, b])


def test_self_dep_raises() -> None:
    a = _t("A", deps=["A"])
    with pytest.raises(ChainCycleError):
        group_into_chains([a])


def test_three_cycle_raises() -> None:
    a = _t("A", deps=["C"])
    b = _t("B", deps=["A"])
    c = _t("C", deps=["B"])
    with pytest.raises(ChainCycleError):
        group_into_chains([a, b, c])


# ---------- Max depth ----------

def test_max_depth_splits_long_chain() -> None:
    """Chain A→B→C→D→E→F with max_depth=3 splits into two chains."""
    tickets = [_t("A")]
    for prev, curr in zip(["A", "B", "C", "D", "E"], ["B", "C", "D", "E", "F"]):
        tickets.append(_t(curr, deps=[prev]))
    result = group_into_chains(tickets, max_depth=3)
    assert len(result.chains) == 2
    assert [t.id for t in result.chains[0]] == ["A", "B", "C"]
    assert [t.id for t in result.chains[1]] == ["D", "E", "F"]


def test_default_max_depth_is_five() -> None:
    """Sanity: default cap is 5, so a 5-chain stays as one."""
    tickets = [_t("A")]
    for prev, curr in zip(["A", "B", "C", "D"], ["B", "C", "D", "E"]):
        tickets.append(_t(curr, deps=[prev]))
    result = group_into_chains(tickets)  # no max_depth → 5
    assert len(result.chains) == 1
    assert len(result.chains[0]) == 5


# ---------- Mixed scenarios ----------

def test_mix_of_chain_independent_skipped_and_cross_repo() -> None:
    """
    Queue: A, B (deps A), C (independent), D (deps missing), E (cross-repo).
    Expect: chains = [A→B, C]; skipped_unsatisfied = [D]; skipped_cross_repo = [E].
    """
    a = _t("A")
    b = _t("B", deps=["A"])
    c = _t("C")
    d = _t("D", deps=["Z"])  # Z is missing
    e = _t("E", repo="other-repo", deps=["A"])  # cross-repo
    result = group_into_chains([a, b, c, d, e])

    chain_ids = [[t.id for t in chain] for chain in result.chains]
    assert ["A", "B"] in chain_ids
    assert ["C"] in chain_ids
    assert len(result.chains) == 2

    skipped_ids = [t.id for t, _ in result.skipped_unsatisfied]
    assert "D" in skipped_ids

    cross_repo_ids = [t.id for t, _ in result.skipped_cross_repo]
    assert "E" in cross_repo_ids
