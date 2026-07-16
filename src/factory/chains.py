"""Group a queue of tickets into dependency chains for shared-branch execution.

A "chain" is an ordered list of tickets that share one branch + one PR, with
each ticket producing one commit. Tickets with no in-queue dependencies form
single-ticket "chains" — those run on the existing single-ticket path.

Rules (ADR-019):
- **Always chain when deps are in the queue.** No opt-in flag.
- **Topo order.** A ticket runs after all its declared deps (whether in the
  same chain or already merged).
- **Cross-repo deps refuse to run.** If a ticket depends on a ticket in a
  different repo, that ticket is skipped (caller surfaces the error). This
  is stricter than the chain rule because cross-repo deps cannot share a
  branch and silently dropping the constraint would be wrong.
- **Cycles abort the whole grouping.** A dependency cycle is a ticket-writing
  bug; surface it, refuse to run.
- **Max chain depth.** Default 10 (ADR-022; was 5 per ADR-019). Chains past
  that are split arbitrarily — and beware: a split tail chain branches from
  the default branch in the same run, *before* the head chain's PR merges,
  so its in-queue deps are not actually on its branch. Keep queues within
  the cap; if you need more chained tickets, the work probably wants to be
  one ticket with subtasks (ADR-018).
- **Deps not in queue and not merged on main are treated as not satisfied.**
  The dependent ticket is skipped with a stderr warning, not chained.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .ticket import Ticket

DEFAULT_MAX_CHAIN_DEPTH = 10


class ChainCycleError(ValueError):
    """A dependency cycle was detected during chain grouping."""


@dataclass
class GroupedTickets:
    """Result of grouping a queue.

    chains: list of ordered ticket lists, each ready to run on one branch.
    skipped_unsatisfied: tickets whose deps aren't in queue and aren't on
        main; caller should log and leave them in the queue.
    skipped_cross_repo: tickets that declared a dep on a different repo;
        caller surfaces the error (cross-repo deps refuse to run per ADR-019).
    """

    chains: list[list[Ticket]] = field(default_factory=list)
    skipped_unsatisfied: list[tuple[Ticket, list[str]]] = field(default_factory=list)
    skipped_cross_repo: list[tuple[Ticket, list[str]]] = field(default_factory=list)


def group_into_chains(
    tickets: Iterable[Ticket],
    *,
    merged_ticket_ids: set[str] | None = None,
    max_depth: int = DEFAULT_MAX_CHAIN_DEPTH,
) -> GroupedTickets:
    """Group an ordered queue of tickets into dependency chains per ADR-019.

    Args:
        tickets: queue in user-specified order. Chains will respect ticket
            dependencies but otherwise preserve input order to keep behavior
            predictable.
        merged_ticket_ids: IDs of tickets already merged on main. A dep
            present here counts as satisfied even when not in the queue.
        max_depth: maximum chain length. Past this, the chain is truncated
            and the tail tickets become independent chains.

    Returns:
        GroupedTickets with chains plus the per-skip reason lists.

    Raises:
        ChainCycleError: if the queue contains a dependency cycle.
    """
    merged_ticket_ids = merged_ticket_ids or set()
    ticket_list = list(tickets)
    queue_ids = {t.id for t in ticket_list}
    by_id = {t.id: t for t in ticket_list}

    result = GroupedTickets()

    # First pass: identify cross-repo deps. A ticket whose dep is also in the
    # queue but belongs to a different repo is refused (ADR-019).
    cross_repo_offenders: set[str] = set()
    for t in ticket_list:
        bad_deps = [
            d for d in t.depends_on
            if d in by_id and by_id[d].target_repo != t.target_repo
        ]
        if bad_deps:
            cross_repo_offenders.add(t.id)
            result.skipped_cross_repo.append((t, bad_deps))

    # Second pass: identify tickets whose deps aren't satisfiable (not in
    # queue and not in merged_ticket_ids and not a cross-repo offender).
    unsatisfied_offenders: set[str] = set()
    for t in ticket_list:
        if t.id in cross_repo_offenders:
            continue
        missing = [
            d for d in t.depends_on
            if d not in queue_ids and d not in merged_ticket_ids
        ]
        if missing:
            unsatisfied_offenders.add(t.id)
            result.skipped_unsatisfied.append((t, missing))

    # Eligible tickets: not refused for either reason.
    refused = cross_repo_offenders | unsatisfied_offenders

    # Refusals cascade: a ticket whose in-queue dep was refused cannot run
    # either — its dep will not merge during this run. Without this, the
    # dependent's edge to the refused ticket silently vanishes and it runs
    # as an "independent" chain out of order (the BIL-9 incident,
    # 2026-07-13). Propagate to a fixpoint so an entire tail behind one bad
    # ticket is skipped, not detached.
    changed = True
    while changed:
        changed = False
        for t in ticket_list:
            if t.id in refused:
                continue
            blocked = [d for d in t.depends_on if d in refused]
            if blocked:
                refused.add(t.id)
                result.skipped_unsatisfied.append(
                    (t, [f"{d} (refused this run)" for d in blocked])
                )
                changed = True

    eligible = [t for t in ticket_list if t.id not in refused]

    # Detect cycles before grouping. Only consider intra-queue edges
    # (deps that aren't in the queue have already been resolved upstream).
    _check_cycles(eligible)

    # Group eligible tickets into connected components by undirected
    # adjacency through in-queue deps, then topo-sort each component.
    components = _connected_components(eligible)
    for component in components:
        ordered = _topo_sort(component)
        # Enforce max_depth by splitting the chain at the cap. Tail tickets
        # become independent chains (single-ticket "chains") preserving order.
        for i in range(0, len(ordered), max_depth):
            result.chains.append(ordered[i:i + max_depth])

    return result


def _check_cycles(tickets: list[Ticket]) -> None:
    """Raise ChainCycleError if a cycle exists in the intra-queue dep graph."""
    by_id = {t.id: t for t in tickets}
    # 0=unvisited, 1=in-progress (on current DFS path), 2=done
    state: dict[str, int] = {t.id: 0 for t in tickets}

    def dfs(node_id: str, path: list[str]) -> None:
        if state[node_id] == 1:
            cycle_start = path.index(node_id)
            cycle = path[cycle_start:] + [node_id]
            raise ChainCycleError(f"Dependency cycle: {' → '.join(cycle)}")
        if state[node_id] == 2:
            return
        state[node_id] = 1
        path.append(node_id)
        for dep in by_id[node_id].depends_on:
            if dep in by_id:  # only chase intra-queue deps
                dfs(dep, path)
        path.pop()
        state[node_id] = 2

    for t in tickets:
        if state[t.id] == 0:
            dfs(t.id, [])


def _connected_components(tickets: list[Ticket]) -> list[list[Ticket]]:
    """Partition tickets into connected components via intra-queue deps.

    Edges are undirected for component detection (we want all transitively-
    related tickets in one component), but direction matters for topo sort.
    """
    by_id = {t.id: t for t in tickets}
    # Build undirected adjacency
    adj: dict[str, set[str]] = {t.id: set() for t in tickets}
    for t in tickets:
        for dep in t.depends_on:
            if dep in by_id:
                adj[t.id].add(dep)
                adj[dep].add(t.id)

    seen: set[str] = set()
    components: list[list[Ticket]] = []
    for t in tickets:
        if t.id in seen:
            continue
        # BFS to collect this component
        component_ids: set[str] = set()
        stack = [t.id]
        while stack:
            curr = stack.pop()
            if curr in component_ids:
                continue
            component_ids.add(curr)
            seen.add(curr)
            stack.extend(adj[curr] - component_ids)
        # Preserve input order within the component
        components.append([x for x in tickets if x.id in component_ids])
    return components


def _topo_sort(tickets: list[Ticket]) -> list[Ticket]:
    """Topo-sort tickets such that deps precede their dependents.

    Stable: among orderings that satisfy the partial order, preserves input
    order (Kahn's algorithm with deterministic tie-breaking by input index).
    """
    by_id = {t.id: t for t in tickets}
    index = {t.id: i for i, t in enumerate(tickets)}
    in_degree: dict[str, int] = {t.id: 0 for t in tickets}
    children: dict[str, list[str]] = {t.id: [] for t in tickets}
    for t in tickets:
        for dep in t.depends_on:
            if dep in by_id:
                in_degree[t.id] += 1
                children[dep].append(t.id)

    # Ready queue ordered by input index for determinism.
    ready = sorted([tid for tid, d in in_degree.items() if d == 0], key=lambda x: index[x])
    result: list[Ticket] = []
    while ready:
        tid = ready.pop(0)
        result.append(by_id[tid])
        for child in children[tid]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                # Insert preserving input order
                pos = 0
                while pos < len(ready) and index[ready[pos]] < index[child]:
                    pos += 1
                ready.insert(pos, child)
    # If we couldn't drain everything, cycle existed (should have been caught
    # earlier by _check_cycles). Sanity check.
    if len(result) != len(tickets):
        raise ChainCycleError("topological sort failed — cycle not caught earlier")
    return result
