# Packet Tracer captures

Outputs saved from the Packet Tracer build (older Packet Tracer version, file state of 17 Nov plus the
fixes in docs/PACKET_TRACER.md). Check them with `python -m campus verify captures/pt`.

| File | Device |
| --- | --- |
| `D1/D2/A1/A2_show_etherchannel_summary.txt` | every switch, all five bundles SU |
| `D1_show_standby_brief.txt` | D1 before the failover tests |
| `vlan10_ping_server.txt`, `vlan10_tracert_server.txt`, `vlan10_tracert6_server.txt` | PC-STAFF (VLAN 10) |
| `vlan30_ping_server.txt`, `vlan30_tracert_server.txt` | PC-ESP32 (VLAN 30) |

Result: 25 passed, 2 failed. The two failures are HSRP for VLAN 50 (voice): the Packet Tracer file
predates the voice VLAN, so it has no VLAN 50 yet. Everything else matches the design.

Not captured yet: `show vlan brief`, `show spanning-tree` (full), D2 `show standby brief`, R1 `show ip route`.
