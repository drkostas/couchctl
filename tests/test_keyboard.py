"""The keyboard planner is checked by simulating the on-screen keyboard: apply the moves it
plans to a model of the grid and read back what would be typed. If a move ever lands the
highlight on a key that is not the one intended, or walks off the grid, the round-trip fails."""
import pytest

from couchctl import keyboard
from couchctl.keyboard import Keymap

YT = keyboard.KEYMAPS["youtube"]


def char_at(km: Keymap, pos):
    """What a select on `pos` would type: a letter, a space, None for a button that types
    nothing (submit), or '?' for a cell that is not a key at all (a walk-off, which must never
    happen)."""
    r, c = pos
    if pos == km.space:
        return " "
    if pos == km.submit:
        return None
    if 0 <= r < len(km.rows) and 0 <= c < len(km.rows[r]):
        return km.rows[r][c]
    return "?"


def run(km: Keymap, moves):
    """Apply moves from the start, recording each select and every position visited."""
    r, c = km.start
    typed, visited = [], [(r, c)]
    for m in moves:
        if m == "left":
            c -= 1
        elif m == "right":
            c += 1
        elif m == "up":
            r -= 1
        elif m == "down":
            r += 1
        elif m == "ok":
            ch = char_at(km, (r, c))
            if ch is not None:
                typed.append(ch)
            continue
        visited.append((r, c))
    return "".join(typed), visited


WORDS = ["cat", "goat house", "the expanse", "a b", "a", "zoo", "yz", "disco",
         "mr beast", "lo fi", "e e", "wxyz", "a man a plan"]


@pytest.mark.parametrize("word", WORDS)
def test_youtube_round_trips(word):
    typed, _ = run(YT, keyboard.plan(YT, word))
    assert typed == word


@pytest.mark.parametrize("word", WORDS)
def test_youtube_never_leaves_the_keyboard(word):
    _, visited = run(YT, keyboard.plan(YT, word))
    for pos in visited:
        assert char_at(YT, pos) != "?", f"walked off the keyboard to {pos} typing {word!r}"


def test_cat_matches_the_measured_sequence():
    # the exact presses that typed "cat" on the real set
    assert keyboard.plan(YT, "cat") == [
        "right", "right", "ok", "left", "left", "ok",
        "right", "right", "right", "right", "right", "down", "down", "ok"]


def test_goat_house_is_fifty_four_presses():
    # matches the real run that typed "goat house" correctly
    assert len(keyboard.plan(YT, "goat house")) == 54


def test_unknown_characters_are_skipped():
    typed, _ = run(YT, keyboard.plan(YT, "a@b"))
    assert typed == "ab"


def test_empty_text_plans_nothing():
    assert keyboard.plan(YT, "") == []


def test_submit_presses_the_search_button_last():
    km = Keymap(rows=("abc", "def"), start=(0, 0), space=(2, 0), submit=(2, 2))
    moves = keyboard.plan(km, "a", submit=True)
    assert moves[-1] == "ok"
    typed, visited = run(km, moves)
    assert typed == "a"              # the button itself types nothing
    assert visited[-1] == (2, 2)     # ended on the search button
    for pos in visited:
        assert char_at(km, pos) != "?"


def test_submit_is_ignored_without_a_submit_position():
    moves_no = keyboard.plan(YT, "cat", submit=True)
    moves_plain = keyboard.plan(YT, "cat", submit=False)
    assert moves_no == moves_plain


# Netflix: a six-wide grid with space on the row ABOVE the letters, the opposite side from
# YouTube. These exercise the engine's handling of a special that sits above the grid.
NF = keyboard.KEYMAPS["netflix"]
NF_WORDS = ["mob", "a b", "peaky blinders", "365", "lupin", "z 0", "mob house", "friends"]


@pytest.mark.parametrize("word", NF_WORDS)
def test_netflix_round_trips(word):
    typed, _ = run(NF, keyboard.plan(NF, word))
    assert typed == word


@pytest.mark.parametrize("word", NF_WORDS)
def test_netflix_never_leaves_the_keyboard(word):
    _, visited = run(NF, keyboard.plan(NF, word))
    for pos in visited:
        assert char_at(NF, pos) != "?", f"walked off the keyboard to {pos} typing {word!r}"


def test_netflix_a_space_b_is_the_measured_eight_presses():
    # matches the real run that typed "a b" correctly on Netflix (space is above the grid)
    assert keyboard.plan(NF, "a b") == ["ok", "right", "right", "up", "ok", "down", "left", "ok"]
