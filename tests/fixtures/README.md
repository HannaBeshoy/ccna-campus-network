# Test fixtures

`lab/` holds **hand-written** show-command outputs in the format Cisco IOS prints, used to test
the parsers and `python -m campus verify`. They are not captures from real devices; the real
captures go to `captures/` on the lab day (docs/LAB_DAY.md). Bridge MAC addresses and HSRP
virtual link-local addresses in them are made up.
