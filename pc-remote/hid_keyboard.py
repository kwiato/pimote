#!/usr/bin/env python3
import os
import time

DEV = "/dev/hidg0"

MOD = {"ctrl":0x01, "shift":0x02, "alt":0x04, "win":0x08, "gui":0x08,
       "altgr":0x40, "ralt":0x40}   # AltGr = right Alt (used for Polish chars)

# USB HID usage codes. These are hardware-level and layout-INDEPENDENT: the
# same code is sent regardless of layout. What differs between layouts is which
# (modifier, code) pair produces which character on the OS side — that lives in
# the CHARS map built below.
KEYS = {
    "a":0x04,"b":0x05,"c":0x06,"d":0x07,"e":0x08,"f":0x09,"g":0x0a,
    "h":0x0b,"i":0x0c,"j":0x0d,"k":0x0e,"l":0x0f,"m":0x10,"n":0x11,
    "o":0x12,"p":0x13,"q":0x14,"r":0x15,"s":0x16,"t":0x17,"u":0x18,
    "v":0x19,"w":0x1a,"x":0x1b,"y":0x1c,"z":0x1d,
    "1":0x1e,"2":0x1f,"3":0x20,"4":0x21,"5":0x22,"6":0x23,"7":0x24,
    "8":0x25,"9":0x26,"0":0x27,
    "enter":0x28,"esc":0x29,"backspace":0x2a,"tab":0x2b,"space":0x2c,
    "f1":0x3a,"f2":0x3b,"f3":0x3c,"f4":0x3d,"f5":0x3e,"f6":0x3f,
    "f7":0x40,"f8":0x41,"f9":0x42,"f10":0x43,"f11":0x44,"f12":0x45,
    "delete":0x4c,"home":0x4a,"end":0x4d,
    "up":0x52,"down":0x51,"left":0x50,"right":0x4f,
}

SHIFT = MOD["shift"]   # 0x02
ALTGR = MOD["altgr"]   # 0x40


def _base_chars():
    """char -> (modifiers, keycode) for plain US ASCII.

    This base map is shared by the "us" and "pl" (Polish Programmers) layouts,
    because both keep the US positions for ASCII — they only differ in whether
    the AltGr Polish diacritics are added on top.
    """
    chars = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        chars[c] = (0, KEYS[c]); chars[c.upper()] = (SHIFT, KEYS[c])
    for d in "1234567890":
        chars[d] = (0, KEYS[d])
    for sym, d in zip("!@#$%^&*()", "1234567890"):
        chars[sym] = (SHIFT, KEYS[d])
    chars.update({
        " ":(0,0x2c), "-":(0,0x2d),"_":(SHIFT,0x2d),
        "=":(0,0x2e),"+":(SHIFT,0x2e), "[":(0,0x2f),"{":(SHIFT,0x2f),
        "]":(0,0x30),"}":(SHIFT,0x30), "\\":(0,0x31),"|":(SHIFT,0x31),
        ";":(0,0x33),":":(SHIFT,0x33), "'":(0,0x34),"\"":(SHIFT,0x34),
        "`":(0,0x35),"~":(SHIFT,0x35), ",":(0,0x36),"<":(SHIFT,0x36),
        ".":(0,0x37),">":(SHIFT,0x37), "/":(0,0x38),"?":(SHIFT,0x38),
        "\t":(0,0x2b), "\n":(0,0x28),
    })
    return chars


def _with_polish(chars):
    """Add Polish diacritics as AltGr + base letter (Polish "Programmers"
    layout). Works only if Windows is set to that layout."""
    for pl, base in {"ą":"a","ć":"c","ę":"e","ł":"l","ń":"n",
                     "ó":"o","ś":"s","ź":"x","ż":"z"}.items():
        chars[pl] = (ALTGR, KEYS[base])
        chars[pl.upper()] = (ALTGR | SHIFT, KEYS[base])
    return chars


# Active layout, picked by the KEYBOARD_LAYOUT env var (set in config.env):
#   pl = Polish (Programmers): US base + ą/ć/ł… via AltGr  (default)
#   us = US English: ASCII only, Polish chars are skipped
LAYOUTS = {
    "us": lambda: _base_chars(),
    "pl": lambda: _with_polish(_base_chars()),
}
LAYOUT = os.environ.get("KEYBOARD_LAYOUT", "pl").lower()
CHARS = LAYOUTS.get(LAYOUT, LAYOUTS["pl"])()


def _write(report):
    with open(DEV, "rb+") as f:
        f.write(report)

def _release():
    _write(bytes(8))

def send_key(*names):
    """e.g. send_key('win','r') or send_key('enter')"""
    mod = code = 0
    for n in names:
        n = n.lower()
        if n in MOD:  mod  |= MOD[n]
        elif n in KEYS: code = KEYS[n]
    _write(bytes([mod,0,code,0,0,0,0,0]))
    time.sleep(0.01); _release()

def type_string(text, delay=0.006):
    for ch in text:
        if ch not in CHARS:  # silently skip unsupported characters
            continue
        mod, code = CHARS[ch]
        _write(bytes([mod, 0, code, 0,0,0,0,0]))
        time.sleep(delay); _release(); time.sleep(delay)

def run_command(cmd):
    """Win+R -> type -> Enter (launches an app/command on Windows)"""
    send_key("win","r"); time.sleep(0.4)
    type_string(cmd);    time.sleep(0.1)
    send_key("enter")

def login(password, settle=1.0):
    """Dismiss the lock-screen curtain and type the password + Enter.

    After a WoL wake, Windows sits on the lock/login screen. Esc raises the
    curtain and — with a single user — focuses the password field. `settle` is
    the pause for the screen to appear; increase it for slow cold-boots from S5.
    Note: characters outside CHARS are skipped by type_string; Polish characters
    only work when the login screen uses the Polish "Programmers" layout and
    KEYBOARD_LAYOUT=pl.
    """
    send_key("esc"); time.sleep(settle)
    type_string(password); time.sleep(0.1)
    send_key("enter")
