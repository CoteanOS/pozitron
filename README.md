# Pozitron

A pocket robot that runs MicroPython on an ESP32-S3. It shows a pair of eyes,
gives one encouraging message per day in Dutch, and measures heart rate and
blood oxygen from a fingertip.

Two files do everything: `main.py` and `berichten.txt`. No libraries to install,
no external drivers. The SSD1306, MAX30102 and MPU6050 drivers are written
inline against raw I2C registers.

## What it does

* Idle face: two oval eyes that blink and follow tilt
* Button 1: shows the next message from `berichten.txt`, then happy eyes
* Button 2: prompts for a fingertip, measures HR and SpO2, then heart eyes
  pulsing at the measured BPM
* Shake it: same as button 1
* Deep sleep after two minutes idle, wakes on either button or on being picked
  up
* Messages run strictly in order, never random. The position is written to
  `idx.txt` on flash, so it survives reboots and battery swaps and wraps after
  365.

## Hardware

| Part | Notes |
| --- | --- |
| ESP32-S3 Zero | Waveshare or equivalent |
| SSD1306 OLED | 128x64, I2C, address 0x3C |
| MAX30102 | heart rate and SpO2 |
| MPU6050 | accelerometer, address 0x68 |
| 2 tactile buttons | |
| TP4056 | charger board, its own USB port is the charge port |
| LiPo cell | single cell, 3.7V |

## Wiring

Everything shares one I2C bus. Chain the four lines from module to module,
order does not matter.

| Net | ESP32 pin | Goes to |
| --- | --- | --- |
| SDA | GPIO 2 | OLED SDA, MAX SDA, MPU SDA |
| SCL | GPIO 1 | OLED SCL, MAX SCL, MPU SCL |
| 3V3 | 3V3 | OLED VCC, MAX VIN, MPU VCC |
| GND | GND | all modules, both buttons |
| Button 1 | GPIO 3 | other leg to GND |
| Button 2 | GPIO 4 | other leg to GND |
| MPU INT | GPIO 7 | optional, enables pick-up-to-wake |

Pins are set at the top of `main.py`. Change them there if your build differs.

Check the bus before running anything:

```python
from machine import Pin, SoftI2C
SoftI2C(sda=Pin(2), scl=Pin(1), freq=400000).scan()
```

You want `[60, 87, 104]`. Those are the OLED, the MAX30102 and the MPU6050.
Long runs of consecutive addresses mean SDA and SCL are shorted or floating.
An empty list means nothing is answering, usually a bad joint or swapped pins.

## Install

Flash MicroPython once over USB:

```bash
esptool.py --chip esp32s3 erase_flash
esptool.py --chip esp32s3 write_flash 0 ESP32_GENERIC_S3-xxxx.bin
```

Copy the two files:

```bash
pip install mpremote
mpremote cp main.py :main.py
mpremote cp berichten.txt :berichten.txt
mpremote reset
```

Thonny works too. Set the interpreter to MicroPython (ESP32), then use
View > Files to upload both files to the device root.

## Updating over WiFi

The USB port can be sealed shut in a finished build, so there is a way in
without it.

1. Hold both buttons while pressing reset
2. The robot starts its own access point, SSID and password are set at the top
   of `update_mode()` in `main.py`
3. Connect a laptop to that network
4. Open the WebREPL client at https://micropython.org/webrepl/ and connect to
   `ws://192.168.4.1:8266`
5. Upload a new `main.py` or `berichten.txt`
6. Reset, or wait ten minutes and it restarts by itself

Set your own SSID and password before flashing. The values in the repo are
placeholders.

## Messages

`berichten.txt` holds 365 lines, one message per line, in Dutch. Replace it
with your own if you want, the count is set by `NUM_MESSAGES` at the top of
`main.py`.

MicroPython's built-in font is ASCII only, so accented characters are
transliterated for display by `ascii_()`. Proper accents need a custom font
generated with `font_to_py`.

## Notes

* SpO2 uses the standard R-ratio approximation rather than the Maxim algorithm,
  which does not exist in Python. Heart rate is reliable, SpO2 is indicative.
  This is a toy, not a medical device.
* Steppers, servos and anything else are not involved. It is a static object
  with a face.
* Deep sleep is set to two minutes in `SLEEP_AFTER`. During development set it
  high so the robot does not fall asleep between tests.
* If the tilt gaze moves the wrong way, swap the axis or flip the sign on the
  `pupil_t` line in the main loop. It depends on how the board is mounted.

## Debugging in Thonny

Run `main.py`, then press Ctrl-C in the shell to break out of the loop while
keeping every function loaded. The red Stop button soft reboots the board and
wipes the definitions, which is not what you want here.

Then you can drive the display by hand:

```python
wrapped(next_message())
happy_eyes()
render_eyes()
show_result(72, 98)
```
