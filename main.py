# Pozitron - pocket robot, MicroPython, ESP32-S3
# Self-contained: SSD1306 + MAX30102 + MPU6050 drivers inline, no libraries needed.
#
# Files on the board:
#   main.py         (this file)
#   berichten.txt   (365 messages, one per line)
#
# Setup once over USB:
#   1. Flash MicroPython: https://micropython.org/download/ESP32_GENERIC_S3/
#      esptool.py --chip esp32s3 erase_flash
#      esptool.py --chip esp32s3 write_flash 0 ESP32_GENERIC_S3-xxxx.bin
#   2. pip install mpremote
#      mpremote cp main.py :main.py
#      mpremote cp berichten.txt :berichten.txt
#
# Updates later (USB glued shut):
#   Hold BOTH buttons while resetting -> robot starts WiFi AP
#   AP_SSID (see settings below) with WebREPL on ws://192.168.4.1:8266
#   Open webrepl client (https://micropython.org/webrepl/), password: AP_PASS,
#   upload a new main.py, done.

import machine, time, framebuf, struct, os, esp32, sys

# ---------- PINS (adjust to your wiring) ----------
PIN_SDA     = 2
PIN_SCL     = 1
PIN_BTN_MSG = 3
PIN_BTN_HR  = 4
PIN_MPU_INT = 7      # or None if not wired
# ---------------------------------------------------

NUM_MESSAGES   = 365
MSG_TIMEOUT    = 20     # s
RESULT_TIME    = 12     # s
FINGER_IR_MIN  = 50000
PLACE_TIMEOUT  = 30     # s
SLEEP_AFTER    = 120    # s idle -> deep sleep (raise this while developing)
SHAKE_G        = 1.8
GREET          = "Hallo!"        # put a name here if you like
AP_SSID        = "Pozitron-Update"
AP_PASS        = "CHANGE_ME_8CHARS"   # set your own before flashing

i2c = machine.SoftI2C(sda=machine.Pin(PIN_SDA), scl=machine.Pin(PIN_SCL), freq=400000)
btn_msg = machine.Pin(PIN_BTN_MSG, machine.Pin.IN, machine.Pin.PULL_UP)
btn_hr  = machine.Pin(PIN_BTN_HR,  machine.Pin.IN, machine.Pin.PULL_UP)

# ================= SSD1306 =================
class SSD1306(framebuf.FrameBuffer):
    def __init__(self, i2c, addr=0x3C):
        self.i2c, self.addr = i2c, addr
        self.buf = bytearray(1024)
        super().__init__(self.buf, 128, 64, framebuf.MONO_VLSB)
        for cmd in (0xAE, 0x20, 0x00, 0x40, 0xA1, 0xA8, 63, 0xC8, 0xD3, 0,
                    0xDA, 0x12, 0xD5, 0x80, 0xD9, 0xF1, 0xDB, 0x30,
                    0x81, 40,          # contrast: dim to save battery
                    0xA4, 0xA6, 0x8D, 0x14, 0xAD, 0x30, 0xAF):
            self.cmd(cmd)
    def cmd(self, c): self.i2c.writeto(self.addr, bytes((0x80, c)))
    def show(self):
        self.cmd(0x21); self.cmd(0); self.cmd(127)
        self.cmd(0x22); self.cmd(0); self.cmd(7)
        self.i2c.writeto(self.addr, b'\x40' + self.buf)
    def power(self, on): self.cmd(0xAF if on else 0xAE)

oled = SSD1306(i2c)

# framebuf font is ASCII-only: transliterate accents for display
_ACC = {'é':'e','è':'e','ë':'e','ê':'e','á':'a','à':'a','ä':'a','ï':'i',
        'í':'i','ó':'o','ö':'o','ú':'u','ü':'u','ç':'c','ñ':'n',
        'É':'E','Ë':'E','Ä':'A','Ö':'O','Ü':'U'}
def ascii_(s):
    return ''.join(_ACC.get(c, c) for c in s)

def centered(l1, l2=None):
    oled.fill(0)
    l1 = ascii_(l1)
    oled.text(l1, (128 - len(l1) * 8) // 2, 24 if l2 else 28)
    if l2:
        l2 = ascii_(l2)
        oled.text(l2, (128 - len(l2) * 8) // 2, 38)
    oled.show()

def wrapped(msg):
    oled.fill(0)
    words, lines, line = ascii_(msg).split(), [], ""
    for w in words:
        t = (line + " " + w).strip()
        if len(t) <= 15: line = t
        else: lines.append(line); line = w
    lines.append(line)
    y = max(2, (64 - len(lines) * 11) // 2)
    for l in lines:
        oled.text(l, 2, y); y += 11
    oled.show()

# ================= MAX30102 =================
MAX_ADDR = 0x57
def max_w(reg, val): i2c.writeto_mem(MAX_ADDR, reg, bytes((val,)))
def max_r(reg, n=1): return i2c.readfrom_mem(MAX_ADDR, reg, n)

def max_present():
    try: return max_r(0xFF)[0] == 0x15   # part ID
    except OSError: return False

def max_on():
    max_w(0x09, 0x40); time.sleep_ms(50)          # reset
    max_w(0x08, 0x4F)                             # FIFO: avg 4, rollover
    max_w(0x09, 0x03)                             # SpO2 mode (red + IR)
    max_w(0x0A, 0x27)                             # 100 Hz, 411 us, 4096 nA
    max_w(0x0C, 0x3C); max_w(0x0D, 0x3C)          # LED currents
    max_w(0x04, 0); max_w(0x05, 0); max_w(0x06, 0)  # clear FIFO

def max_off(): max_w(0x09, 0x80)                  # shutdown

def max_read():
    """Return list of (red, ir) samples waiting in FIFO."""
    wr, rd = max_r(0x04)[0], max_r(0x06)[0]
    n = (wr - rd) % 32
    out = []
    if n:
        data = max_r(0x07, n * 6)
        for k in range(n):
            r = ((data[k*6] << 16) | (data[k*6+1] << 8) | data[k*6+2]) & 0x3FFFF
            ir = ((data[k*6+3] << 16) | (data[k*6+4] << 8) | data[k*6+5]) & 0x3FFFF
            out.append((r, ir))
    return out

def calc_vitals(red, ir):
    """HR from IR peaks, SpO2 from R-ratio. ~25 samples/s effective."""
    FS = 25
    n = len(ir)
    dc_ir = sum(ir) / n; dc_r = sum(red) / n
    ac = [v - dc_ir for v in ir]
    # light smoothing
    sm = [sum(ac[max(0, k-2):k+3]) / len(ac[max(0, k-2):k+3]) for k in range(n)]
    thr = max(sm) * 0.5
    peaks, last = [], -99
    for k in range(1, n - 1):
        if sm[k] > thr and sm[k] >= sm[k-1] and sm[k] > sm[k+1] and k - last >= FS // 3:
            peaks.append(k); last = k
    hr = 0
    if len(peaks) >= 3:
        hr = int(60 * FS * (len(peaks) - 1) / (peaks[-1] - peaks[0]))
    ac_r  = (max(red) - min(red)) / 2
    ac_i  = (max(ir) - min(ir)) / 2
    spo2 = 0
    if dc_r > 0 and dc_ir > 0 and ac_i > 0:
        R = (ac_r / dc_r) / (ac_i / dc_ir)
        spo2 = int(110 - 25 * R)
    hr_ok = 35 < hr < 200
    sp_ok = 70 <= spo2 <= 100
    return (hr if hr_ok else 0), (spo2 if sp_ok else 0)

# ================= MPU6050 =================
MPU_ADDR = 0x68
mpu_ok = False
def mpu_w(reg, val): i2c.writeto_mem(MPU_ADDR, reg, bytes((val,)))
def mpu_begin():
    global mpu_ok
    try:
        mpu_w(0x6B, 0x00); mpu_w(0x1C, 0x00); time.sleep_ms(50)
        mpu_ok = True
    except OSError:
        mpu_ok = False
def mpu_accel():
    d = i2c.readfrom_mem(MPU_ADDR, 0x3B, 6)
    x, y, z = struct.unpack('>hhh', d)
    return x / 16384, y / 16384, z / 16384
def mpu_sleep():
    if mpu_ok: mpu_w(0x6B, 0x40)
def mpu_arm_wake():
    mpu_w(0x6B, 0x00); mpu_w(0x1C, 0x01)
    mpu_w(0x1F, 20); mpu_w(0x20, 40)     # motion threshold + duration
    mpu_w(0x37, 0x20); mpu_w(0x38, 0x40) # latched active-high INT on motion
    time.sleep_ms(5)
    mpu_w(0x6C, 0x07); mpu_w(0x6B, 0x20) # cycle mode, gyro off

# ================= eyes =================
import random
pupil = 0.0; pupil_t = 0.0
droop = 0.0
def render_eyes(closed=False):
    oled.fill(0)
    if closed:
        oled.fill_rect(10, 30, 44, 5, 1)
        oled.fill_rect(74, 30, 44, 5, 1)
    else:
        oled.ellipse(32, 32, 22, 28, 1, True)
        oled.ellipse(96, 32, 22, 28, 1, True)
        dx = int(pupil)
        oled.ellipse(38 + dx, 22, 5, 7, 0, True)
        oled.ellipse(102 + dx, 22, 5, 7, 0, True)
        lid = int(droop * 26)
        if lid:
            oled.fill_rect(8, 0, 48, 4 + lid, 0)
            oled.fill_rect(72, 0, 48, 4 + lid, 0)
    oled.show()

def happy_eyes():
    oled.fill(0)
    for t in range(4):
        for e in (32, 96):
            oled.ellipse(e, 46 + t, 22, 22, 1, False, 0b0011)  # upper half arcs
    oled.show()

def heart(cx, cy, s):
    oled.ellipse(cx - s // 2, cy - s // 3, s // 2, s // 2, 1, True)
    oled.ellipse(cx + s // 2, cy - s // 3, s // 2, s // 2, 1, True)
    for row in range(s + s // 6):
        w = int((s + s // 6 - row) * s / (s + s // 6))
        oled.hline(cx - w, cy - s // 6 + row, 2 * w, 1)

# ================= messages =================
def next_message():
    try:
        idx = int(open('idx.txt').read())
    except OSError:
        idx = 0
    msg = "Bestand niet gevonden :("
    try:
        with open('berichten.txt') as f:
            for n, line in enumerate(f):
                if n == idx:
                    msg = line.strip(); break
    except OSError:
        pass
    with open('idx.txt', 'w') as f:
        f.write(str((idx + 1) % NUM_MESSAGES))
    return msg

# ================= update mode (WebREPL over own AP) =================
def update_mode():
    import network, webrepl
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASS)
    try:
        webrepl.start(password=AP_PASS)
    except Exception:
        webrepl.start()
    centered("Update-modus", "192.168.4.1:8266")
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(1)
    machine.reset()

# ================= deep sleep =================
def go_to_sleep():
    global droop
    d = droop
    while d < 1.0:
        d += 0.15; droop = min(d, 1.0); render_eyes()
        time.sleep_ms(120)
    render_eyes(True); time.sleep_ms(300)
    oled.power(False)
    try: max_off()
    except OSError: pass
    wake_pins = [btn_msg, btn_hr]
    esp32.wake_on_ext1(pins=wake_pins, level=esp32.WAKEUP_ALL_LOW)
    # Pick-up-to-wake. Not every ESP32-S3 build exposes ext0, so fall back to
    # button-only wake if it is missing instead of crashing before deepsleep.
    if mpu_ok and PIN_MPU_INT is not None:
        try:
            mpu_arm_wake()
            esp32.wake_on_ext0(pin=machine.Pin(PIN_MPU_INT, machine.Pin.IN,
                               machine.Pin.PULL_DOWN), level=esp32.WAKEUP_ANY_HIGH)
        except (AttributeError, ValueError, OSError):
            mpu_sleep()
    else:
        mpu_sleep()
    machine.deepsleep()

# ================= shake detection =================
_shake_hits = 0
_shake_last = 0
_shake_cool = 0
def shaken():
    global _shake_hits, _shake_last, _shake_cool
    if not mpu_ok or time.ticks_ms() < _shake_cool:
        return False
    try: x, y, z = mpu_accel()
    except OSError: return False
    mag = (x*x + y*y + z*z) ** 0.5
    now = time.ticks_ms()
    if abs(mag - 1.0) > (SHAKE_G - 1.0):
        if time.ticks_diff(now, _shake_last) > 60:
            _shake_hits += 1; _shake_last = now
        if _shake_hits >= 3:
            _shake_hits = 0; _shake_cool = now + 2000
            return True
    if time.ticks_diff(now, _shake_last) > 1000:
        _shake_hits = 0
    return False

# ================= measurement =================
def measure():
    centered("Leg je vingertop", "op mijn sensor")
    max_on()
    t0 = time.time()
    while True:                              # wait for finger
        s = max_read()
        if s and s[-1][1] > FINGER_IR_MIN:
            break
        if time.time() - t0 > PLACE_TIMEOUT or not btn_hr.value():
            max_off(); centered("Niets gemeten", "Probeer opnieuw")
            time.sleep(2); return None
        time.sleep_ms(50)

    centered("Stil houden...", "Ik luister")
    red, ir = [], []
    good, hr_f, sp_f = 0, 0, 0
    t0 = time.time()
    while time.time() - t0 < 60:
        for r, i in max_read():
            red.append(r); ir.append(i)
        if len(ir) > 100:
            red = red[-100:]; ir = ir[-100:]
            if ir[-1] < FINGER_IR_MIN:
                max_off(); centered("Vinger kwijt!", "Probeer opnieuw")
                time.sleep(2); return None
            hr, sp = calc_vitals(red, ir)
            oled.fill(0)
            oled.text("Meten...", 2, 4)
            oled.text("HR:   %s bpm" % (hr if hr else "--"), 2, 28)
            oled.text("SpO2: %s %%" % (sp if sp else "--"), 2, 44)
            oled.show()
            if hr and sp:
                good += 1; hr_f, sp_f = hr, sp
                if good >= 6:
                    max_off(); return hr_f, sp_f
        time.sleep_ms(200)
    max_off(); centered("Geen goede meting", "Probeer opnieuw")
    time.sleep(2); return None

def show_result(hr, sp):
    t0 = time.time()
    beat_ms = 60000 // max(40, min(180, hr))
    while time.time() - t0 < RESULT_TIME:
        big = (time.ticks_ms() % beat_ms) < beat_ms // 3
        s = 16 if big else 13
        oled.fill(0)
        heart(32, 24, s); heart(96, 24, s)
        txt = "%d bpm  %d%%" % (hr, sp)
        oled.text(txt, (128 - len(txt) * 8) // 2, 54)
        oled.show()
        if not btn_msg.value() or not btn_hr.value():
            return
        time.sleep_ms(40)

# ================= main =================
def main():
    global pupil, pupil_t, droop

    # both buttons held at boot -> update mode
    if not btn_msg.value() and not btn_hr.value():
        time.sleep_ms(50)
        if not btn_msg.value() and not btn_hr.value():
            update_mode()

    mpu_begin()
    if max_present():
        max_off()
    else:
        centered("MAX30102", "niet gevonden!"); time.sleep(2)

    # wake-up: greet, then eyes slowly open
    if machine.reset_cause() == machine.DEEPSLEEP_RESET:
        centered(GREET); time.sleep(1.6)
        d = 1.0
        while d > 0:
            droop = d; render_eyes(); d -= 0.2
            time.sleep_ms(90)
    droop = 0
    render_eyes()

    last_activity = time.time()
    next_blink = time.ticks_ms() + 2000

    while True:
        now = time.ticks_ms()

        # blink
        if time.ticks_diff(now, next_blink) >= 0:
            render_eyes(True); time.sleep_ms(140); render_eyes()
            next_blink = now + 2500 + random.getrandbits(11)

        # gaze follows tilt (swap axis/sign to match your mounting)
        if mpu_ok:
            try:
                ax, _, _ = mpu_accel()
                pupil_t = max(-9, min(9, ax * 14))
            except OSError:
                pass
        if abs(pupil_t - pupil) > 0.4:
            pupil += (pupil_t - pupil) * 0.3
            render_eyes()

        # message button or shake
        if not btn_msg.value() or shaken():
            last_activity = time.time()
            wrapped(next_message())
            t0 = time.time()
            time.sleep_ms(300)
            while time.time() - t0 < MSG_TIMEOUT:
                if not btn_msg.value() or not btn_hr.value():
                    break
                time.sleep_ms(40)
            happy_eyes(); time.sleep(1.8)
            last_activity = time.time()
            render_eyes()

        # HR button
        elif not btn_hr.value():
            last_activity = time.time()
            time.sleep_ms(300)
            res = measure()
            if res:
                show_result(*res)
            last_activity = time.time()
            render_eyes()

        # idle -> sleep
        if time.time() - last_activity > SLEEP_AFTER:
            go_to_sleep()

        time.sleep_ms(50)

main()
