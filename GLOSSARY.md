# Monitor Switch

Monitor Switch selects the video inputs of external monitors when a person chooses a computer or moves a shared USB device.

## Language

**Destination**:
The computer whose video connection a switch request selects: Mac or Windows.

**Monitor profile**:
The association between a particular monitor and the inputs used by each destination.

**Switch batch**:
One request to select a destination on the included monitors, with a separate outcome for each monitor.

**Accepted input**:
The input from the last app command accepted without a reported error. It does not confirm the picture currently displayed.
_Avoid_: Current input, confirmed picture

**Automatic request**:
A switch batch prompted by the shared USB device arriving at a destination. It can be suppressed when the app already accepted the same input.

**Manual request**:
A switch batch explicitly selected by the person using the app. It reapplies the chosen destination even when an accepted input matches.

**Quick panel**:
The small window opened from the menu bar or tray to choose a destination, inspect the included monitors, or open settings.
