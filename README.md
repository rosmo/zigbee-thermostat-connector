## GPIOs on Radxa Zero

Use `gpioinfo` to list.

All of these are on `gpiochip0`:

- Relay 1: pin 22, GPIOC_7 (line 48)
- Relay 2: pin 24, GPIOH_6 (line 22)
- Relay 3: pin 19, GPIOH_4 (line 20)
- Relay 4: pin 21, GPIOH_5 (line 21)
- Relay 5: pin 23, GPIOH_7 (line 23)
- (Relay 6: pin 36, GPIOH_8 (line 24))

Use full line name in config:

```py
chip = gpiod.Chip("/dev/gpiochip0")
print(chip.line_offset_from_id("22 [GPIOC_7]"))
```