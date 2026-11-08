"""`haptic-hub bench`: hardware-day checks for the belt, one command each.

  links.py       how the bench talks to a belt: real BLE or the simulator
  serial_log.py  optional: read the belt's serial log to time TTL expiry
  flow.py        the checks, in order, and the operator prompts
  report.py      results, summary table, JSON report
  cli.py         the `bench` subcommand

See docs/hardware-bringup.md for when and how to run it.
"""
