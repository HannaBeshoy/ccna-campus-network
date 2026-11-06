# Building it in Packet Tracer

About 1.5 hours. The result, `packet-tracer/campus.pkt`, goes into the repository.

## 1. Devices (15 min)

| Name | Packet Tracer device | Notes |
| --- | --- | --- |
| R1 | ISR4321 | |
| ISP | 2911 (or any router) | only G0/0 with 203.0.113.1/30, a loopback 8.8.8.8/32 to ping, and `ip route 10.20.0.0 255.255.128.0 203.0.113.2` |
| D1, D2 | 3650-24PS | add the power supply in the Physical tab (AC-POWER-SUPPLY), or the switch stays off |
| A1, A2 | 2960-24TT | |
| WLC1 | WLC-2504 | |
| AP | LAP-PT or 3702i | |
| PCs, a server | PC-PT, Server-PT | the server at 10.20.99.53 (DNS) and 10.20.99.30 (RADIUS), in VLAN 99 |
| ESP32 stand-in | PC-PT (or an IoT device) | on A1 Fa0/17; Packet Tracer has no ESP32 |

## 2. Cables (15 min)

Copper straight-through everywhere, following the port names in `network.yaml`:

| From | To |
| --- | --- |
| R1 G0/0/0 | ISP G0/0 |
| R1 G0/0/1 | D1 G1/0/24 |
| R1 G0/0/2 | D2 G1/0/24 |
| D1 G1/0/21-22 | D2 G1/0/21-22 |
| D1 G1/0/1-2 | A1 Fa0/21-22 |
| D2 G1/0/1-2 | A1 Fa0/23-24 |
| D1 G1/0/3-4 | A2 Fa0/21-22 |
| D2 G1/0/3-4 | A2 Fa0/23-24 |
| D2 G1/0/10 | WLC1 G1 (management port) |
| A1 Fa0/19 | AP |
| A1 Fa0/1, Fa0/9, Fa0/17 | a PC in each of VLAN 10, 20, 30 |
| A2 Fa0/1 | a test-station PC |

## 3. Configurations (20 min)

On your PC: `cp secrets.example.yaml secrets.yaml`, set two passwords, `python -m campus generate --out build`. Then for each device: CLI tab, `enable`, `configure terminal`, paste `build/<name>.cfg`. Paste R1 first, then D1 and D2, then A1 and A2. `end`, `copy running-config startup-config`.

If Packet Tracer rejects a line, note it (device, line, message) for the README: some Packet Tracer versions do not know every IOS command (for example `ip arp inspection validate` on the 2960, or IPv6 HSRP on the 3650). The real switches on the lab day are the reference.

## 4. WLC and AP (15 min)

WLC1: management interface 10.20.99.20/24, gateway 10.20.99.1, VLAN 99. New dynamic interface `wifi` on VLAN 40, 10.20.40.10/24, gateway 10.20.40.1, DHCP server 10.20.254.1. RADIUS server 10.20.99.30 (shared secret also on the server's AAA service). WLAN `HU01-STAFF`, interface `wifi`, security WPA2 + 802.1X. The AP: static IP 10.20.99.21/24, gateway 10.20.99.1, controller 10.20.99.20 (VLAN 99 has no DHCP). A laptop with a wireless card joins `HU01-STAFF` with a user from the RADIUS server.

## 5. Check (15 min)

Wait about a minute for STP and EtherChannel, then:

| Test | Expected |
| --- | --- |
| PCs get addresses (Desktop, IP Configuration, DHCP) | 10.20.10.21, 10.20.20.21, ...; the ESP32 stand-in gets 10.20.30.21 once its MAC is in `network.yaml` |
| ping between VLANs | works (inter-VLAN routing on D1/D2) |
| ping 8.8.8.8 from a PC | works (default routes to the ISP) |
| `show etherchannel summary` on every switch | every Po (SU), members (P) |
| `show standby brief` on D1 | Active for 10, 20, 99; Standby for 30, 40 |
| `show spanning-tree` on A1 | as `python -m campus stp` predicts |
| shut D1's link to R1, `show ip route` on R1 | the floating route via 10.20.255.6 appears |
| shut D1 entirely, ping from a VLAN 10 PC | a few lost pings, then D2 answers as the gateway (HSRP) |
| a second PC on A1 Fa0/17 (sensor port) | the port goes err-disabled (port security, shutdown) |

Save the show outputs of Packet Tracer in `captures/pt/` and run `python -m campus verify captures/pt`. Screenshots to `docs/img/`.
