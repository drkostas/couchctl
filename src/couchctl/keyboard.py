"""Type into an app that draws its own on-screen keyboard, by walking that keyboard with the
remote's arrow keys.

Many television apps (YouTube and the clients built on its interface, Netflix, and others) do
not use the set's system keyboard. They draw their own grid of letters that a person moves
through with the remote. Injected text (see ``samsung.send_text``) never reaches such a field,
because there is no system keyboard for it to fill. The only thing that does reach it is the
same arrow-and-select presses the remote sends.

This module turns a word into those presses. The letters of an app's keyboard form a grid, and
the app highlights a known key when its search opens. From that start each character is reached
by moving the highlight by an exact number of steps and pressing select, and the highlight is
tracked the whole way so it is never driven off the grid onto the surrounding buttons. The
caller sends the presses with a platform's own key channel (``samsung.send_keys`` for a Samsung
set, arrow key events over adb for an Android television).

It works only while that search keyboard is open and the highlight is still on the key the app
started it on, so the caller's contract is: open the app's search, then type, before moving the
highlight by hand.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The abstract key names :func:`plan` emits. A platform maps each to its own remote key.
MOVES = ("left", "right", "up", "down", "ok")


@dataclass(frozen=True)
class Keymap:
    """One app's search keyboard.

    ``rows`` is the letter grid, one string per row, each character a key, read left to right.
    ``start`` is the key the app highlights when its search opens, as ``(row, column)`` into
    ``rows``. ``space`` and ``submit`` give the positions of the space key and the search button
    when they sit outside the letter grid. A position is ``(row, column)`` where ``column`` is a
    grid column and ``row`` may be ``-1`` (a row above the grid, such as Netflix's space) or a
    row index at or past the last grid row (a row below it, such as YouTube's space); either is
    reached by travelling up or down that column from the grid. ``start`` may itself be such a
    position when the app opens on a key outside the letter grid.
    """

    rows: tuple[str, ...]
    start: tuple[int, int]
    space: tuple[int, int] | None = None
    submit: tuple[int, int] | None = None

    def cell(self, ch: str) -> tuple[int, int] | None:
        """The ``(row, column)`` of one character, or ``None`` when the keyboard has no key for
        it. A space resolves to ``space``."""
        if ch == " ":
            return self.space
        ch = ch.lower()
        for r, row in enumerate(self.rows):
            c = row.find(ch)
            if c != -1:
                return (r, c)
        return None


def plan(keymap: Keymap, text: str, *, submit: bool = False) -> list[str]:
    """The remote presses that type ``text`` on ``keymap``, from ``keymap.start``.

    Movement never slides along the irregular row of wide buttons below the letter grid (space,
    clear, search), where one press does not mean one column. A key below the grid is reached by
    arriving at its column within the grid and then descending; leaving such a key, the highlight
    rises back into the grid along that same column before it moves sideways. Within the grid,
    which is a clean rectangle, it moves along the row and then the column. Characters the keyboard
    cannot type are skipped. With ``submit`` and a ``submit`` position, the search button is
    pressed at the end.
    """
    keys: list[str] = []
    cur = keymap.start
    grid_rows = len(keymap.rows)

    def horiz(c: int, tc: int) -> list[str]:
        return ["right"] * (tc - c) if tc > c else ["left"] * (c - tc)

    def go(target: tuple[int, int]) -> None:
        nonlocal cur
        r, c = cur
        tr, tc = target
        # If the highlight sits on a key outside the letter grid (a row above or below it),
        # bring it back onto the grid edge along that key's own column before moving sideways.
        if r < 0:
            keys.extend(["down"] * (-r))
            r = 0
        elif r >= grid_rows:
            keys.extend(["up"] * (r - (grid_rows - 1)))
            r = grid_rows - 1
        # Move along the row to the target's column, inside the grid where one press is one cell.
        keys.extend(horiz(c, tc))
        # Then up or down the column to the target row, which may be above or below the grid.
        if tr < r:
            keys.extend(["up"] * (r - tr))
        elif tr > r:
            keys.extend(["down"] * (tr - r))
        cur = target

    for ch in text:
        target = keymap.cell(ch)
        if target is None:
            continue
        go(target)
        keys.append("ok")
    if submit and keymap.submit is not None:
        go(keymap.submit)
        keys.append("ok")
    return keys


#: The search keyboards of known apps, measured from photographs of each app's search screen.
#: Add an app by giving its grid, the key it starts on, and where space sits.
KEYMAPS: dict[str, Keymap] = {
    # YouTube and the clients built on its television interface: a seven-wide alphabetical grid,
    # with a row below it whose left-hand key is space.
    "youtube": Keymap(
        rows=("abcdefg", "hijklmn", "opqrstu", "vwxyz-'"),
        start=(0, 0),
        space=(4, 0),
    ),
    # Netflix with its keyboard set to Latin: a six-wide grid of letters then digits, with a row
    # above it holding the script toggle, space and backspace. Opens on the top-left key. (The
    # keyboard can also be in Greek, the same grid with Greek letters, added separately.)
    "netflix": Keymap(
        rows=("abcdef", "ghijkl", "mnopqr", "stuvwx", "yz1234", "567890"),
        start=(0, 0),
        space=(-1, 2),
    ),
}
