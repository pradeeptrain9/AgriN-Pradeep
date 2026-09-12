"""No endpoint may read a variable that only some branches assign.

This exists because of a live 500 that no test and no linter caught.

`create_diagnosis` bound `identification` only inside its escalation branch, then
read it unconditionally when assembling the response. The read was written
defensively -- `identification.provisional_name if identification else None` --
which reads as safe and is not: the guard tests a name that does not exist yet,
so Python raises UnboundLocalError before the condition is evaluated.

What made it expensive is where it landed. The unbound path is the one where the
on-device model answered confidently: free, offline, in under a second, the best
outcome the product has. Every one of those became "Could not check the photo".
The escalated path, which costs money, worked fine. So the bug was invisible to
anyone testing the cloud fallback and total for anyone testing the thing that
actually works.

Ruff's F821 does not catch it -- the name is bound somewhere, just not on every
path -- and the suite's 729 tests are contract-level and never execute the
endpoint body. Hence a structural check.

The rule enforced: within one function, if a name is assigned inside some
branches of an if/else and then read after that statement, every branch must
assign it, or it must be bound before. A bare `x = None` ahead of the branch
satisfies it, which is the fix and also the documentation.
"""

import ast
import pathlib

import pytest

API = pathlib.Path(__file__).resolve().parents[2] / "app" / "api"


def _assigned_names(nodes: list[ast.stmt]) -> set[str]:
    """Names bound anywhere in these statements, including nested branches."""
    found: set[str] = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    found |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
            elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(child.target, ast.Name):
                    found.add(child.target.id)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                found |= {n.id for n in ast.walk(child.target) if isinstance(n, ast.Name)}
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        found |= {
                            n.id for n in ast.walk(item.optional_vars)
                            if isinstance(n, ast.Name)
                        }
            elif isinstance(child, ast.ExceptHandler) and child.name:
                found.add(child.name)
    return found


def _reads(nodes: list[ast.stmt]) -> set[str]:
    return {
        n.id
        for node in nodes
        for n in ast.walk(node)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }


def _offences(tree: ast.AST) -> list[str]:
    problems: list[str] = []

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Anything bound by the signature is always available.
        args = function.args
        safe: set[str] = {
            a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        }
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                safe.add(extra.arg)

        body = function.body
        for index, statement in enumerate(body):
            if isinstance(statement, ast.If) and statement.orelse:
                in_body = _assigned_names(statement.body)
                in_else = _assigned_names(statement.orelse)
                # Assigned on one side only -- fine in itself.
                lopsided = in_body.symmetric_difference(in_else) - safe
                # Walk what follows in order. A name rebound before it is next
                # read is not a hazard -- readiness.py legitimately reuses
                # `level` in later, self-contained blocks, and flagging that
                # would make this check noise, and noise gets muted.
                pending = set(lopsided)
                for follower in body[index + 1:]:
                    if not pending:
                        break
                    read_here = _reads([follower]) & pending
                    bound_here = _assigned_names([follower])
                    for name in sorted(read_here - bound_here):
                        problems.append(
                            f"{function.name}: '{name}' is assigned in only one "
                            f"branch of the if on line {statement.lineno}, then "
                            f"read on line {follower.lineno} without being rebound"
                        )
                    pending -= (read_here | bound_here)
            safe |= _assigned_names([statement])

    return problems


@pytest.mark.parametrize(
    "path", sorted(API.glob("*.py")), ids=lambda p: p.name
)
def test_no_endpoint_reads_a_conditionally_bound_name(path):
    problems = _offences(ast.parse(path.read_text()))
    assert not problems, (
        f"{path.name} can raise UnboundLocalError at runtime:\n  "
        + "\n  ".join(problems)
        + "\n\nBind the name before the branch (e.g. `x = None`). A guard like "
        "`x if x else None` does not help: the name has to exist before it can "
        "be tested."
    )


class TestTheCheckActuallyCatchesTheBugItWasWrittenFor:
    """A structural test that cannot fail is decoration. This proves it bites."""

    SOURCE = '''
def create_diagnosis(decision):
    if decision.accepted:
        disease = 1
    else:
        identification = fetch()
        disease = 2
    return identification.provisional_name if identification else None
'''

    FIXED = '''
def create_diagnosis(decision):
    identification = None
    if decision.accepted:
        disease = 1
    else:
        identification = fetch()
        disease = 2
    return identification.provisional_name if identification else None
'''

    def test_it_flags_the_original_shape(self):
        problems = _offences(ast.parse(self.SOURCE))
        assert any("identification" in p for p in problems)

    def test_it_passes_once_the_name_is_bound_first(self):
        assert _offences(ast.parse(self.FIXED)) == []

    def test_a_name_assigned_in_both_branches_is_not_flagged(self):
        source = "def f(c):\n    if c:\n        x = 1\n    else:\n        x = 2\n    return x\n"
        assert _offences(ast.parse(source)) == []

    def test_a_name_never_read_afterwards_is_not_flagged(self):
        # Assigning on one side only is perfectly normal when nothing reads it
        # later. Flagging that would make the check noise, and noise gets muted.
        source = "def f(c):\n    if c:\n        x = 1\n    else:\n        y = 2\n    return 0\n"
        assert _offences(ast.parse(source)) == []
