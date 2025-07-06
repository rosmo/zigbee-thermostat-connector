## GPIOs on Radxa Zero

Use `gpioinfo` to list.

All of these are on `gpiochip0`:

- Relay 1, pin 16, GPIOX_10, line 75
- Relay 2, pin 24, GPIOH_6, line 22
- Relay 3, pin 19, GPIOH_4, line 20
- Relay 4, pin 21, GPIOH_5, line 21
- Relay 5, pin 23, GPIOH_7, line 23 - heat
- Relay 6, pin 18, GPIOX_8, line 73 - cool

gpioset 22 relay 2
gpioset 23 relay 5
gpioset 20 relay 3
gpioset 21 relay 4
gpioset 73 relay 6
gpioset 75 relay 1

Use full line name in config:

```py
chip = gpiod.Chip("/dev/gpiochip0")
print(chip.line_offset_from_id("22 [GPIOC_7]"))
```