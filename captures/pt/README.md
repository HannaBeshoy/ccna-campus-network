# Packet Tracer captures

Outputs saved from the Packet Tracer build (the 17 Nov file with the fixes in docs/PACKET_TRACER.md and the
voice VLAN 50 added on 25 Nov). Check them with `python -m campus verify captures/pt`.

| File | Device |
| --- | --- |
| `D1/D2/A1/A2_show_etherchannel_summary.txt` | every switch, all five bundles SU |
| `D1_show_standby_brief.txt`, `D2_show_standby_brief.txt` | HSRP for every VLAN, VLAN 50 included (D2 Active, D1 Standby) |
| `vlan10_ping_server.txt`, `vlan10_tracert_server.txt`, `vlan10_tracert6_server.txt` | PC-STAFF (VLAN 10) |
| `vlan30_ping_server.txt`, `vlan30_tracert_server.txt` | PC-ESP32 (VLAN 30) |
| `R1_show_ip_dhcp_binding.txt` | the IP phone (000C.85BB.7DAE) leased 10.20.50.21, PC-PHONE 10.20.10.22 |
| `A1_show_interfaces_Fa0-2_switchport.txt` | phone port: access VLAN 10, voice VLAN 50 |
| `A1_show_mac_address-table_interface_Fa0-2.txt`, `A1_show_port-security_interface_Fa0-2.txt` | phone port MACs and port security (Secure-up, 0 violations) |

Result: 39 passed, 0 failed. The four A1/R1 phone files are not read by `verify`; they are kept as evidence.
Packet Tracer lists the phone's MAC in VLAN 10 only, although its lease is in VLAN 50 (see the R1 binding).
The `10.20.50.23` lease belongs to a test PC that was on A1 Fa0/3 in VLAN 50 during troubleshooting.

Not captured yet: `show vlan brief`, `show spanning-tree` (full), R1 `show ip route`.
