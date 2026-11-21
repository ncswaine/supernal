#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nick Swaine
# SPDX-License-Identifier: MIT
"""supernal - summons the Super key the uConsole was never given.

The familiar grabs the physical keyboard (EVIOCGRAB) so no other input
handler sees its events, and re-emits every event through a uinput virtual
keyboard named "supernal familiar". That virtual keyboard declares
KEY_LEFTMETA, which the physical keyboard does not advertise.

The sigil (default: Right Alt + S) is a chord:

  * Right Alt alone and S alone pass through untouched.
  * Pressing S while Right Alt is held releases Right Alt on the virtual
    keyboard, then presses KEY_LEFTMETA. Keys pressed while S stays held are
    forwarded, so the OS sees Super+<key>. Releasing S releases KEY_LEFTMETA.

Because the remapping happens at the kernel input layer, it works the same
under Wayland, X11 and on the Linux console.
"""

import argparse
import logging
import signal
import sys
import time

try:
    import evdev
    from evdev import ecodes
except ImportError:  # The state machine (and its tests) do not need evdev.
    evdev = None
    ecodes = None

# Event type/code constants from linux/input-event-codes.h. They are part of
# the stable kernel ABI, so hard-coding them lets the Familiar run without
# evdev installed (e.g. in the unit tests).
EV_SYN = 0x00
EV_KEY = 0x01
EV_MSC = 0x04
SYN_REPORT = 0

KEY_UP = 0      # value of an EV_KEY release
KEY_DOWN = 1    # value of an EV_KEY press
KEY_REPEAT = 2  # value of an EV_KEY autorepeat

FAMILIAR_NAME = "supernal familiar"

# Familiar states.
DORMANT = "DORMANT"   # sigil modifier not held
ATTUNED = "ATTUNED"   # sigil modifier physically held, Super not pressed
INVOKED = "INVOKED"   # Super is held down on the virtual keyboard

log = logging.getLogger("supernal")


def key_name(code):
    """Return a readable name for a key code (falls back to the number)."""
    if ecodes is not None:
        name = ecodes.KEY.get(code) or ecodes.BTN.get(code)
        if isinstance(name, (list, tuple)):
            name = name[0]
        if name:
            return name
    return "key %d" % code


class Familiar:
    """Pure event state machine: physical events in, virtual events out.

    feed() takes one input event as (type, code, value) and returns a list of
    (type, code, value) tuples to write to the virtual keyboard. It does no
    I/O, so it can be tested without hardware.

    Terms used below:
      mod    - the sigil modifier (default KEY_RIGHTALT)
      key    - the sigil key (default KEY_S)
      super  - the key that gets invoked (default KEY_LEFTMETA)
      down   - the set of keys the virtual keyboard currently reports as
               pressed, i.e. what the OS believes is held. Every emitted
               EV_KEY press/release updates it, which is what lets
               release_all() guarantee that nothing stays stuck.
    """

    def __init__(self, sigil_mod, sigil_key, invoke_key):
        if len({sigil_mod, sigil_key, invoke_key}) != 3:
            raise ValueError("sigil modifier, sigil key and invoked key "
                             "must be three different keys")
        self.mod = sigil_mod
        self.key = sigil_key
        self.super = invoke_key
        self.state = DORMANT
        self.mod_held = False   # physical state of the sigil modifier
        self.down = set()

    # -- helpers ---------------------------------------------------------

    def _shift(self, new_state, why):
        if new_state != self.state:
            log.debug("%s -> %s (%s)", self.state, new_state, why)
            self.state = new_state

    def _key(self, code, value):
        """Build one EV_KEY output event and track the virtual key state."""
        if value == KEY_DOWN:
            self.down.add(code)
        elif value == KEY_UP:
            self.down.discard(code)
        return (EV_KEY, code, value)

    # -- public API ------------------------------------------------------

    def feed(self, ev_type, code, value):
        """Process one input event; return the events to emit."""
        if ev_type != EV_KEY:
            # EV_SYN, EV_MSC (scan codes) etc. pass through unchanged. The
            # SYN_REPORT that closes each physical frame also closes any
            # frame the familiar rewrote.
            return [(ev_type, code, value)]
        if code == self.mod:
            return self._on_mod(value)
        if code == self.key:
            return self._on_key(value)
        # Any other key is forwarded as-is in every state. In ATTUNED this
        # gives Alt+<key> (e.g. Alt+F4); in INVOKED it gives Super+<key>.
        return [self._key(code, value)]

    def invoke(self):
        """Press Super. If the modifier is down on the virtual keyboard,
        release it first, in its own SYN frame, so the OS never observes
        modifier+Super at the same time."""
        out = []
        if self.mod in self.down:
            out.append(self._key(self.mod, KEY_UP))
            out.append((EV_SYN, SYN_REPORT, 0))
        out.append(self._key(self.super, KEY_DOWN))
        self._shift(INVOKED, "sigil traced")
        log.info("sigil traced: Super invoked")
        return out

    def revoke(self):
        """Release Super and end the invocation."""
        out = [self._key(self.super, KEY_UP)]
        # If the modifier is still physically held we return to ATTUNED.
        # It stays released toward the OS (it was "spent" on the sigil):
        # its eventual release is swallowed, and pressing the sigil key
        # again invokes Super again.
        self._shift(ATTUNED if self.mod_held else DORMANT, "sigil key lifted")
        log.info("Super revoked")
        return out

    def release_all(self):
        """Release every key the virtual keyboard still holds, then SYN.

        Used on banishment. Key state is tracked per input device by
        libinput, X and the console, so keys pressed on the virtual keyboard
        must be released on it before it disappears.
        """
        out = [self._key(code, KEY_UP) for code in sorted(self.down)]
        out.append((EV_SYN, SYN_REPORT, 0))
        self.mod_held = False
        self._shift(DORMANT, "banished")
        return out

    # -- per-key handlers ------------------------------------------------

    def _on_mod(self, value):
        if value == KEY_DOWN:
            self.mod_held = True
            if self.state == DORMANT:
                self._shift(ATTUNED, "sigil modifier held")
            # Forwarded in every state. Pressed during INVOKED (after an
            # earlier release) it yields Super+Alt+<key> combinations.
            return [self._key(self.mod, KEY_DOWN)]

        if value == KEY_REPEAT:
            # Only repeat a modifier the OS thinks is held.
            if self.mod in self.down:
                return [self._key(self.mod, KEY_REPEAT)]
            return []

        # KEY_UP
        self.mod_held = False
        if self.state == ATTUNED:
            self._shift(DORMANT, "sigil modifier released")
        # During INVOKED (or after a spent sigil) the modifier was already
        # released toward the OS by invoke(), so this release is swallowed.
        if self.mod in self.down:
            return [self._key(self.mod, KEY_UP)]
        return []

    def _on_key(self, value):
        if self.state == DORMANT:
            # Plain S, including autorepeat.
            return [self._key(self.key, value)]

        if self.state == ATTUNED:
            if value == KEY_DOWN:
                # The sigil is complete. The S press itself is swallowed.
                return self.invoke()
            # Repeat/release of an S that was already down before the
            # modifier was pressed (S then Alt): it was forwarded, so keep
            # forwarding it. An S the OS never saw is swallowed.
            if self.key in self.down:
                return [self._key(self.key, value)]
            return []

        # INVOKED: S is the "Super" key now. Swallow its press/autorepeat;
        # its release ends the invocation.
        if value == KEY_UP:
            return self.revoke()
        return []


# ---------------------------------------------------------------------------
# Hardware side (requires python3-evdev and root)
# ---------------------------------------------------------------------------

class Banishment(Exception):
    """Raised from the signal handler to unwind into the cleanup path."""


def _on_signal(signum, _frame):
    raise Banishment(signal.Signals(signum).name)


def resolve_key(name):
    """Turn 'KEY_RIGHTALT', 'rightalt' or '100' into a key code."""
    text = name.strip()
    if text.isdigit():
        return int(text)
    text = text.upper()
    if not text.startswith(("KEY_", "BTN_")):
        text = "KEY_" + text
    code = ecodes.ecodes.get(text)
    if code is None:
        raise ValueError("unknown key name: %s" % name)
    return code


def _has_letters(device):
    return ecodes.KEY_A in device.capabilities().get(ecodes.EV_KEY, [])


def scry_vessels():
    """Return (devices, unreadable_paths) for all evdev nodes, by path."""
    devices, unreadable = [], []
    for path in sorted(evdev.list_devices(),
                       key=lambda p: int(p.rsplit("event", 1)[-1] or 0)):
        try:
            devices.append(evdev.InputDevice(path))
        except OSError:
            unreadable.append(path)
    return devices, unreadable


def find_vessel(name):
    """Open the keyboard whose name contains `name` (case-insensitive).

    Only devices that report KEY_A count, which filters out the trackball
    and gamepad nodes that may share the keyboard's name. Our own virtual
    keyboard is never chosen.
    """
    wanted = name.casefold()
    devices, unreadable = scry_vessels()
    matches = []
    for dev in devices:
        if (dev.name != FAMILIAR_NAME and wanted in dev.name.casefold()
                and _has_letters(dev)):
            matches.append(dev)
        else:
            dev.close()
    if not matches:
        hint = " (some devices were unreadable; run as root)" if unreadable else ""
        raise LookupError("no vessel named like %r with letter keys was found%s; "
                          "run --scry to see the candidates" % (name, hint))
    if len(matches) > 1:
        log.warning("several vessels answer to %r: %s; choosing %s", name,
                    ", ".join("%s (%s)" % (d.name, d.path) for d in matches),
                    matches[0].path)
        for dev in matches[1:]:
            dev.close()
    return matches[0]


def conjure_familiar_device(vessel, extra_keys):
    """Create the uinput keyboard: the vessel's keys plus `extra_keys`.

    EV_REP is deliberately not declared. Without it the kernel does not run
    its own autorepeat timer on the virtual device, so the only repeats are
    the physical ones we forward (value 2). Declaring it would double the
    repeat rate on the console. Wayland and X ignore value-2 events and
    generate repeats themselves, so they are unaffected either way.
    """
    caps = vessel.capabilities()
    keys = set(caps.get(ecodes.EV_KEY, [])) | set(extra_keys)
    events = {ecodes.EV_KEY: sorted(keys)}
    if ecodes.EV_MSC in caps:
        events[ecodes.EV_MSC] = caps[ecodes.EV_MSC]
    return evdev.UInput(events, name=FAMILIAR_NAME)


def bind(vessel):
    """Wait until no keys are physically held, then grab the vessel.

    Grabbing while a key is down (e.g. the Enter that launched us) would
    leave that key pressed forever from the OS's point of view, because
    its release would be delivered only to us.
    """
    held = vessel.active_keys()
    if held:
        log.info("waiting for the summoner's hands to leave the keys (%s)",
                 ", ".join(key_name(c) for c in held))
        while vessel.active_keys():
            time.sleep(0.05)
    vessel.grab()


def banish(familiar, vessel, ui):
    """Cleanup path: release synthetic keys, ungrab, close everything.

    Each step is attempted even if an earlier one fails, and further
    signals are ignored so they cannot interrupt the cleanup.
    """
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    if ui is not None:
        try:
            for ev in familiar.release_all():
                ui.write(*ev)
        except OSError as err:
            log.error("could not release keys: %s", err)
    if vessel is not None:
        try:
            vessel.ungrab()
        except OSError:
            pass  # not grabbed, or the device is already gone
        try:
            vessel.close()
        except OSError:
            pass
    if ui is not None:
        try:
            ui.close()
        except OSError:
            pass
    log.info("the familiar is banished; all keys released")


def scry():
    devices, unreadable = scry_vessels()
    print("Vessels seen in the scrying glass (* = has letter keys):")
    for dev in devices:
        mark = "*" if _has_letters(dev) else " "
        print("  %s %-20s %s" % (mark, dev.path, dev.name))
        dev.close()
    if unreadable:
        print("%d device(s) could not be read; scry again with sudo."
              % len(unreadable))
    return 0


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="supernal",
        description="supernal - summons the Super key the uConsole "
                    "was never given")
    p.add_argument("--scry", "--list", action="store_true",
                   help="list input devices (path + name) and exit")
    p.add_argument("--vessel", "--device", metavar="NAME",
                   help="case-insensitive substring of the keyboard's name; "
                        "the matching device with KEY_A is used")
    p.add_argument("--sigil-mod", "--modifier", metavar="KEY",
                   default="KEY_RIGHTALT",
                   help="modifier half of the sigil (default: %(default)s)")
    p.add_argument("--sigil-key", "--key", metavar="KEY", default="KEY_S",
                   help="key half of the sigil (default: %(default)s)")
    p.add_argument("--invoke", "--super", metavar="KEY",
                   default="KEY_LEFTMETA",
                   help="key the sigil invokes (default: %(default)s)")
    p.add_argument("--whisper", "--verbose", action="store_true",
                   help="log state transitions to stderr")
    return p, p.parse_args(argv)


def main(argv=None):
    parser, args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.whisper else logging.INFO,
                        format="supernal: %(message)s", stream=sys.stderr)

    if evdev is None:
        log.error("a reagent is missing: python3-evdev "
                  "(sudo apt install python3-evdev)")
        return 2
    if args.scry:
        return scry()
    if not args.vessel:
        parser.error("name the vessel with --vessel NAME "
                     "(run --scry to see the candidates)")
    try:
        familiar = Familiar(resolve_key(args.sigil_mod),
                            resolve_key(args.sigil_key),
                            resolve_key(args.invoke))
    except ValueError as err:
        parser.error(str(err))

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _on_signal)

    vessel = ui = None
    status = 0
    try:
        vessel = find_vessel(args.vessel)
        ui = conjure_familiar_device(vessel, (familiar.mod, familiar.key,
                                              familiar.super))
        bind(vessel)
        log.info("the circle is drawn: bound to %s (%s)",
                 vessel.name, vessel.path)
        log.info("sigil: %s + %s invokes %s", key_name(familiar.mod),
                 key_name(familiar.key), key_name(familiar.super))
        for event in vessel.read_loop():
            for ev in familiar.feed(event.type, event.code, event.value):
                ui.write(*ev)
    except Banishment as sig:
        log.info("banished by %s", sig)
    except LookupError as err:
        log.error("%s", err)
        status = 1
    except (OSError, evdev.UInputError) as err:
        # Device unplugged/removed (ENODEV), grab refused (EBUSY), or
        # /dev/uinput not writable. Exit non-zero so systemd restarts us.
        log.error("the circle is broken: %s", err)
        status = 1
    finally:
        banish(familiar, vessel, ui)
    return status


if __name__ == "__main__":
    sys.exit(main())
