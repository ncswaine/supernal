#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nick Swaine
# SPDX-License-Identifier: MIT
"""The wards: unit tests for the Familiar state machine.

No hardware or python3-evdev needed. Run with:
    python3 -m unittest -v test_wards
"""

import itertools
import logging
import random
import unittest

from supernal import (ATTUNED, DORMANT, EV_KEY, EV_MSC, EV_SYN, INVOKED,
                      SYN_REPORT, Familiar)

# Key codes from linux/input-event-codes.h (stable kernel ABI).
KEY_1 = 2
KEY_TAB = 15
KEY_A = 30
KEY_S = 31
KEY_LEFTALT = 56
KEY_F4 = 62
KEY_RIGHTALT = 100
KEY_RIGHT = 106
KEY_LEFTMETA = 125

ALT, S, SUPER = KEY_RIGHTALT, KEY_S, KEY_LEFTMETA
UP, DOWN, REPEAT = 0, 1, 2
SYN = (EV_SYN, SYN_REPORT, 0)


def k(code, value):
    return (EV_KEY, code, value)


class Witness:
    """Plays the OS: replays the familiar's output and checks invariants."""

    def __init__(self, test):
        self.test = test
        self.held = set()
        self.super_presses = 0

    def see(self, events):
        for ev_type, code, value in events:
            if ev_type != EV_KEY:
                continue
            if value == DOWN:
                self.test.assertNotIn(code, self.held,
                                      "double press of %d" % code)
                if code == SUPER:
                    # The OS must never observe Alt+Super from the sigil.
                    self.test.assertNotIn(ALT, self.held,
                                          "Super pressed while Alt held")
                    self.super_presses += 1
                self.held.add(code)
            elif value == UP:
                self.test.assertIn(code, self.held,
                                   "release of unpressed key %d" % code)
                self.held.discard(code)
            else:
                self.test.assertIn(code, self.held,
                                   "repeat of unpressed key %d" % code)


class WardBase(unittest.TestCase):
    def setUp(self):
        self.familiar = Familiar(ALT, S, SUPER)
        self.witness = Witness(self)

    def cast(self, *steps):
        """Feed (code, value) key steps; return all key output events.

        Each step is sent as a full frame (MSC_SCAN, EV_KEY, SYN_REPORT),
        like a real keyboard. Only EV_KEY output is returned, so the
        expected lists stay readable; SYN placement is tested separately.
        """
        out = []
        for code, value in steps:
            frame = []
            frame += self.familiar.feed(EV_MSC, 4, 0x70000 + code)
            frame += self.familiar.feed(EV_KEY, code, value)
            frame += self.familiar.feed(EV_SYN, SYN_REPORT, 0)
            self.witness.see(frame)
            out += [ev for ev in frame if ev[0] == EV_KEY]
        return out

    def assertAllReleased(self):
        self.assertEqual(self.witness.held, set())
        self.assertEqual(self.familiar.down, set())
        self.assertEqual(self.familiar.state, DORMANT)


class WardsOfPassage(WardBase):
    """Keys that are not the sigil pass through unchanged."""

    def test_ward_of_the_lone_alt(self):
        self.assertEqual(self.cast((ALT, DOWN)), [k(ALT, DOWN)])
        self.assertEqual(self.familiar.state, ATTUNED)
        self.assertEqual(self.cast((ALT, REPEAT), (ALT, UP)),
                         [k(ALT, REPEAT), k(ALT, UP)])
        self.assertAllReleased()

    def test_ward_of_the_lone_s_with_autorepeat(self):
        out = self.cast((S, DOWN), (S, REPEAT), (S, REPEAT), (S, REPEAT),
                        (S, UP))
        self.assertEqual(out, [k(S, DOWN), k(S, REPEAT), k(S, REPEAT),
                               k(S, REPEAT), k(S, UP)])
        self.assertEqual(self.witness.super_presses, 0)
        self.assertAllReleased()

    def test_ward_of_alt_f4(self):
        out = self.cast((ALT, DOWN), (KEY_F4, DOWN), (KEY_F4, UP),
                        (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(KEY_F4, DOWN),
                               k(KEY_F4, UP), k(ALT, UP)])
        self.assertAllReleased()

    def test_ward_of_alt_tab_with_repeat(self):
        out = self.cast((ALT, DOWN), (KEY_TAB, DOWN), (KEY_TAB, REPEAT),
                        (KEY_TAB, UP), (KEY_TAB, DOWN), (KEY_TAB, UP),
                        (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(KEY_TAB, DOWN),
                               k(KEY_TAB, REPEAT), k(KEY_TAB, UP),
                               k(KEY_TAB, DOWN), k(KEY_TAB, UP),
                               k(ALT, UP)])
        self.assertAllReleased()

    def test_ward_of_the_other_alt(self):
        # Left Alt is not the sigil modifier: Left Alt + S is just Alt+S.
        out = self.cast((KEY_LEFTALT, DOWN), (S, DOWN), (S, UP),
                        (KEY_LEFTALT, UP))
        self.assertEqual(out, [k(KEY_LEFTALT, DOWN), k(S, DOWN), k(S, UP),
                               k(KEY_LEFTALT, UP)])
        self.assertAllReleased()

    def test_ward_of_s_before_alt(self):
        # S held first, then Alt: not the sigil; S keeps typing as S.
        out = self.cast((S, DOWN), (ALT, DOWN), (S, REPEAT), (S, UP),
                        (ALT, UP))
        self.assertEqual(out, [k(S, DOWN), k(ALT, DOWN), k(S, REPEAT),
                               k(S, UP), k(ALT, UP)])
        self.assertEqual(self.witness.super_presses, 0)
        self.assertAllReleased()

    def test_ward_of_untouched_non_key_events(self):
        self.assertEqual(self.familiar.feed(EV_MSC, 4, 1234),
                         [(EV_MSC, 4, 1234)])
        self.assertEqual(self.familiar.feed(EV_SYN, SYN_REPORT, 0), [SYN])


class WardsOfTheSigil(WardBase):
    """Right Alt + S becomes Super."""

    def test_sigil_tap_yields_clean_super(self):
        out = self.cast((ALT, DOWN), (S, DOWN), (S, UP), (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(SUPER, UP)])
        self.assertEqual(self.witness.super_presses, 1)
        self.assertAllReleased()

    def test_sigil_releases_alt_in_its_own_frame(self):
        self.familiar.feed(EV_KEY, ALT, DOWN)
        out = self.familiar.feed(EV_KEY, S, DOWN)
        self.assertEqual(out, [k(ALT, UP), SYN, k(SUPER, DOWN)])

    def test_sigil_states(self):
        self.cast((ALT, DOWN))
        self.assertEqual(self.familiar.state, ATTUNED)
        self.cast((S, DOWN))
        self.assertEqual(self.familiar.state, INVOKED)
        self.cast((ALT, UP))
        self.assertEqual(self.familiar.state, INVOKED)
        self.cast((S, UP))
        self.assertEqual(self.familiar.state, DORMANT)

    def test_sigil_with_another_key(self):
        for other in (KEY_A, KEY_RIGHT, KEY_1):
            with self.subTest(key=other):
                self.setUp()
                out = self.cast((ALT, DOWN), (S, DOWN), (other, DOWN),
                                (other, UP), (S, UP), (ALT, UP))
                self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP),
                                       k(SUPER, DOWN), k(other, DOWN),
                                       k(other, UP), k(SUPER, UP)])
                self.assertAllReleased()

    def test_sigil_with_alt_released_before_the_other_key(self):
        out = self.cast((ALT, DOWN), (S, DOWN), (ALT, UP), (KEY_A, DOWN),
                        (KEY_A, UP), (S, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(KEY_A, DOWN), k(KEY_A, UP), k(SUPER, UP)])
        self.assertAllReleased()

    def test_sigil_traced_twice_while_alt_held(self):
        out = self.cast((ALT, DOWN), (S, DOWN), (S, UP), (S, DOWN), (S, UP),
                        (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(SUPER, UP), k(SUPER, DOWN), k(SUPER, UP)])
        self.assertEqual(self.witness.super_presses, 2)
        self.assertAllReleased()

    def test_spent_alt_does_not_return(self):
        # After the sigil, Alt stays released toward the OS even though it
        # is still physically held: a following key arrives without Alt.
        out = self.cast((ALT, DOWN), (S, DOWN), (S, UP), (KEY_A, DOWN),
                        (KEY_A, UP), (ALT, REPEAT), (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(SUPER, UP), k(KEY_A, DOWN), k(KEY_A, UP)])
        self.assertAllReleased()

    def test_alt_pressed_again_during_invocation(self):
        # Release Alt, then press it again while S is held: Super+Alt+A.
        out = self.cast((ALT, DOWN), (S, DOWN), (ALT, UP), (ALT, DOWN),
                        (KEY_A, DOWN), (KEY_A, UP), (ALT, UP), (S, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(ALT, DOWN), k(KEY_A, DOWN), k(KEY_A, UP),
                               k(ALT, UP), k(SUPER, UP)])
        self.assertAllReleased()

    def test_custom_sigil(self):
        f = Familiar(KEY_LEFTALT, KEY_A, SUPER)
        f.feed(EV_KEY, KEY_LEFTALT, DOWN)
        self.assertEqual(f.feed(EV_KEY, KEY_A, DOWN),
                         [k(KEY_LEFTALT, UP), SYN, k(SUPER, DOWN)])

    def test_sigil_keys_must_differ(self):
        with self.assertRaises(ValueError):
            Familiar(ALT, ALT, SUPER)


class WardsAgainstRepetition(WardBase):
    """Autorepeat is forwarded for ordinary keys, swallowed for the sigil."""

    def test_s_autorepeat_is_swallowed_while_invoked(self):
        out = self.cast((ALT, DOWN), (S, DOWN), (S, REPEAT), (S, REPEAT),
                        (S, UP), (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(SUPER, UP)])

    def test_alt_autorepeat_is_swallowed_while_invoked(self):
        out = self.cast((ALT, DOWN), (S, DOWN), (ALT, REPEAT), (S, UP),
                        (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(SUPER, UP)])

    def test_other_key_autorepeat_is_forwarded_while_invoked(self):
        out = self.cast((ALT, DOWN), (S, DOWN), (KEY_RIGHT, DOWN),
                        (KEY_RIGHT, REPEAT), (KEY_RIGHT, REPEAT),
                        (KEY_RIGHT, UP), (S, UP), (ALT, UP))
        self.assertEqual(out, [k(ALT, DOWN), k(ALT, UP), k(SUPER, DOWN),
                               k(KEY_RIGHT, DOWN), k(KEY_RIGHT, REPEAT),
                               k(KEY_RIGHT, REPEAT), k(KEY_RIGHT, UP),
                               k(SUPER, UP)])
        self.assertAllReleased()


class WardsOfReleaseOrder(WardBase):
    """Every release order after Alt, S, A ends with nothing held."""

    def test_every_release_order(self):
        for order in itertools.permutations([ALT, S, KEY_A]):
            with self.subTest(order=order):
                self.setUp()
                self.cast((ALT, DOWN), (S, DOWN), (KEY_A, DOWN))
                self.assertEqual(self.witness.held, {SUPER, KEY_A})
                self.cast(*[(code, UP) for code in order])
                self.assertAllReleased()
                self.assertEqual(self.witness.super_presses, 1)

    def test_every_press_and_release_order(self):
        # Any press order of Alt/S/A followed by any release order.
        for presses in itertools.permutations([ALT, S, KEY_A]):
            for releases in itertools.permutations([ALT, S, KEY_A]):
                with self.subTest(presses=presses, releases=releases):
                    self.setUp()
                    self.cast(*[(c, DOWN) for c in presses])
                    self.cast(*[(c, UP) for c in releases])
                    self.assertAllReleased()
                    # Super appears only if Alt came before S.
                    expect = 1 if presses.index(ALT) < presses.index(S) else 0
                    self.assertEqual(self.witness.super_presses, expect)


class WardsOfBanishment(WardBase):
    """release_all() leaves the OS with nothing held."""

    def test_ward_against_stuck_super(self):
        self.cast((ALT, DOWN), (S, DOWN))
        self.assertEqual(self.witness.held, {SUPER})
        out = self.familiar.release_all()
        self.assertEqual(out, [k(SUPER, UP), SYN])
        self.witness.see(out)
        self.assertAllReleased()

    def test_banishment_mid_combo_releases_everything(self):
        self.cast((ALT, DOWN), (S, DOWN), (ALT, UP), (ALT, DOWN),
                  (KEY_A, DOWN))
        out = self.familiar.release_all()
        self.assertEqual(out[-1], SYN)
        self.assertEqual(sorted(out[:-1]), sorted([k(ALT, UP), k(KEY_A, UP),
                                                   k(SUPER, UP)]))
        self.witness.see(out)
        self.assertAllReleased()

    def test_banishment_while_attuned(self):
        self.cast((ALT, DOWN), (KEY_TAB, DOWN))
        out = self.familiar.release_all()
        self.witness.see(out)
        self.assertAllReleased()

    def test_banishment_while_dormant_is_quiet(self):
        self.assertEqual(self.familiar.release_all(), [SYN])

    def test_ward_against_stuck_keys_under_chaos(self):
        # Random physical sequences (always physically consistent, with
        # autorepeat on held keys) must never produce Alt+Super, and must
        # leave nothing held once every key is lifted.
        rng = random.Random(1999)
        pool = [ALT, S, KEY_A, KEY_LEFTALT, KEY_RIGHT]
        for _ in range(500):
            self.setUp()
            held = set()
            for _ in range(rng.randint(1, 30)):
                code = rng.choice(pool)
                if code in held:
                    value = rng.choice([UP, REPEAT])
                else:
                    value = DOWN
                self.cast((code, value))
                if value == DOWN:
                    held.add(code)
                elif value == UP:
                    held.discard(code)
            self.cast(*[(code, UP) for code in sorted(held)])
            self.assertAllReleased()


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main(verbosity=2)
