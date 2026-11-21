# The Grimoire of supernal

*supernal - summons the Super key the uConsole was never given.*

The ClockworkPi uConsole keyboard has no Super key. supernal is a small
familiar you bind to it: it takes hold of the keyboard, passes every key
through unchanged, and when you trace the **sigil** it invokes Super.

**The sigil: hold Right Alt, then press S.**

| You do                               | The OS sees                         |
|--------------------------------------|-------------------------------------|
| Right Alt alone, or Right Alt + F4   | Alt, Alt+F4 (unchanged)             |
| S alone (held, autorepeating)        | s, ssssss (unchanged)               |
| Right Alt + S, release               | Super tap (GNOME opens Activities)  |
| Right Alt + S, keep S held, press A  | Super+A                             |
| Right Alt + S held, then arrows/1-9  | Super+Left, Super+1, ...            |

While S is held, Right Alt may be let go; Super stays until S is released.
Pressing S again while still holding Right Alt invokes Super again.

The familiar works at the kernel input layer (evdev + uinput), so the same
sigil works under GNOME on Wayland, on X11 and on the text console.

## How the Familiar Works

1. It finds your keyboard (the *vessel*) and waits until no keys are held.
2. It grabs the vessel (EVIOCGRAB) so nothing else sees its raw events.
3. It conjures a virtual keyboard named `supernal familiar` that has all of
   the vessel's keys plus KEY_LEFTMETA, and re-emits every event there.
4. Its state machine has three states:
   - **DORMANT**: Right Alt is not held. Everything passes through.
   - **ATTUNED**: Right Alt is held. Everything passes through, but an S
     press completes the sigil: Right Alt is released toward the OS, then
     Super is pressed. The S press is swallowed.
   - **INVOKED**: Super is held. Other keys pass through (Super+key). S
     autorepeat and the Right Alt release are swallowed. Releasing S
     releases Super.
5. On banishment (SIGTERM, SIGINT, SIGHUP, device error) it releases every
   key still held on the virtual keyboard, ungrabs and closes everything.

## Reagents

- Python 3 (already present on Ubuntu)
- python3-evdev
- The `uinput` kernel module (built in or loaded automatically on Ubuntu)

```bash
sudo apt install python3-evdev
```

Check that `/dev/uinput` exists:

```bash
ls -l /dev/uinput
```

## Consulting the Wards

The wards (unit tests) need neither hardware nor root, nor even evdev:

```bash
python3 -m unittest -v test_wards
```

## Finding the Vessel

List the input devices. Devices marked `*` have letter keys and can serve
as the vessel:

```bash
sudo python3 supernal.py --scry
```

Or use evtest (`sudo apt install evtest`):

```bash
sudo evtest
```

Choose a distinctive part of the keyboard's name; `--vessel` matches it
case-insensitively and picks the matching device that has letter keys
(the trackball and gamepad nodes are skipped).

## A Safe First Summoning

Run it in the foreground with a timeout first. Whatever happens, `timeout`
banishes the familiar after 60 seconds and your keyboard is released.
On the uConsole the vessel is `ClockworkPI uConsole Keyboard`.

```bash
sudo timeout 60 python3 supernal.py --vessel "uConsole Keyboard" --whisper
```

Release Enter after typing the command; the familiar waits until no key is
held before it binds. Look for:

```
supernal: the circle is drawn: bound to <device name> (/dev/input/eventN)
```

Then try the sigil: tap Right Alt + S (Activities should open), hold
Right Alt + S and press an arrow or a number. Ctrl+C also banishes it, since
Ctrl and C still pass through.

## Drawing the Circle

Copy the familiar and the grimoire into place:

```bash
sudo install -D -m 0755 supernal.py /usr/local/lib/supernal/supernal.py
```

```bash
sudo install -D -m 0644 GRIMOIRE.md /usr/local/lib/supernal/GRIMOIRE.md
```

Install the unit (it already names the vessel `uConsole Keyboard`):

```bash
sudo install -m 0644 supernal.service /etc/systemd/system/supernal.service
```

```bash
sudo systemctl daemon-reload
```

## Binding the Familiar

Start it now and at every boot:

```bash
sudo systemctl enable --now supernal.service
```

Check on it and read its log:

```bash
systemctl status supernal.service
```

```bash
journalctl -u supernal.service -b
```

If the keyboard vanishes or the familiar fails, systemd summons it again
after 3 seconds (Restart=on-failure).

## Banishment

Stop it (all keys are released and the keyboard is ungrabbed):

```bash
sudo systemctl stop supernal.service
```

Stop it from returning at boot:

```bash
sudo systemctl disable supernal.service
```

Remove it entirely:

```bash
sudo systemctl disable --now supernal.service
```

```bash
sudo rm /etc/systemd/system/supernal.service
```

```bash
sudo rm -r /usr/local/lib/supernal
```

```bash
sudo systemctl daemon-reload
```

## Changing the Sigil

Every key can be changed by name (`KEY_` prefix optional):

```bash
sudo timeout 60 python3 supernal.py --vessel "uConsole Keyboard" --sigil-mod KEY_RIGHTCTRL --sigil-key KEY_SPACE --invoke KEY_LEFTMETA
```

Plain aliases exist for every flag: `--list`, `--device`, `--modifier`,
`--key`, `--super`, `--verbose`.

## Known Quirks

- Tracing the sigil shows the OS a brief Right Alt tap before Super. GNOME
  ignores a lone Alt tap, but applications that react to a bare Alt tap
  (for example Firefox showing its menu bar) may react.
- After the sigil, Right Alt stays released toward the OS until you lift it
  physically; keys pressed after S is released arrive without Alt.
- The virtual keyboard does not declare kernel autorepeat (EV_REP); the
  physical keyboard's repeats are forwarded instead. On the text console
  the repeat rate is therefore the physical keyboard's.
- uconsole-sleep unbinds the keyboard's USB device while the screen is off.
  The familiar loses its vessel and exits; systemd summons it again every
  3 seconds until the keyboard returns (StartLimitIntervalSec=0 means it
  never gives up). Expect "the circle is broken" and "no vessel" lines in
  the journal while the screen sleeps, and up to 3 seconds without the
  sigil after waking. Ordinary keys work during that gap. uconsole-sleep
  grabs only the power key, so it never fights the familiar for the
  keyboard.
