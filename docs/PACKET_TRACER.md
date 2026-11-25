# Building it in Packet Tracer

About 1.5 hours. The result, `packet-tracer/campus.pkt`, goes into the repository.

## 1. Devices (15 min)

| Name | Packet Tracer device | Notes |
| --- | --- | --- |
| R1 | 2911 | three Gigabit ports G0/0-G0/2 (the ISR4321 and ISR4331 in Packet Tracer have only two) |
| ISP | 2911 | G0/0 to R1, G0/1 to the server LAN; paste `build/ISP.cfg` (routes the site's whole block back to R1, since there is no NAT yet) |
| Internet | Cloud-PT (optional) | only a picture between R1 and the ISP, as in the course activities; a straight cable R1 G0/0 to ISP G0/0 works the same |
| Dual Stack Server | Server-PT | 64.100.1.10/24, gateway 64.100.1.1; 2001:db8:100:1::a/64, gateway 2001:db8:100:1::1 (Desktop, IP Configuration) |
| D1, D2 | 3650-24PS | add the power supply in the Physical tab (AC-POWER-SUPPLY), or the switch stays off |
| A1, A2 | 2960-24TT | |
| WLC1 | WLC-2504 | |
| AP | LAP-PT or 3702i | |
| PCs, campus server | PC-PT, Server-PT | on A1 Fa0/20 (VLAN 99) at 10.20.99.53, running DNS and RADIUS (AAA) |
| IP phone | IP Phone (7960) | on A1 Fa0/2, with a PC on the phone's switch port; plug in its power adapter (Physical tab) |
| ESP32 stand-in | PC-PT (or an IoT device) | on A1 Fa0/17; Packet Tracer has no ESP32 |

## 2. Cables (15 min)

Copper straight-through everywhere, following the port names in `network.yaml`:

| From | To |
| --- | --- |
| R1 G0/0 | ISP G0/0 |
| ISP G0/1 | Dual Stack Server Fa0 |
| R1 G0/1 | D1 G1/0/24 |
| R1 G0/2 | D2 G1/0/24 |
| D1 G1/0/21-22 | D2 G1/0/21-22 |
| D1 G1/0/1-2 | A1 Fa0/21-22 |
| D2 G1/0/1-2 | A1 Fa0/23-24 |
| D1 G1/0/3-4 | A2 Fa0/21-22 |
| D2 G1/0/3-4 | A2 Fa0/23-24 |
| D2 G1/0/10 | WLC1 G1 (management port) |
| A1 Fa0/19 | AP |
| A1 Fa0/20 | the campus server (DNS + RADIUS) |
| A1 Fa0/1, Fa0/9, Fa0/17 | a PC in each of VLAN 10, 20, 30 |
| A2 Fa0/1 | a test-station PC |

## 3. Configurations (20 min)

On your PC: `cp secrets.example.yaml secrets.yaml`, set two passwords, `python -m campus generate --out build`. Then for each device: CLI tab, `enable`, `configure terminal`, paste `build/<name>.cfg`. Paste ISP and R1 first, then D1 and D2, then A1 and A2. `end`, `copy running-config startup-config`.

Packet Tracer notes, all handled by the generated configs:

- The 2960 accepts only `channel-group 1-6`, so the EtherChannels are numbered 1-5 (`python -m campus check` enforces it).
- A 3650 Gigabit port facing a 2960 FastEthernet port only joins an LACP bundle with `speed 100` and `duplex full` fixed on both ends.
- The 2960 checks DHCP snooping per physical port, so the uplink members carry `ip dhcp snooping trust` as well as the Port-channel.
- If a command is rejected inside an interface block, the lines after it land on the previous interface: check `show running-config` after a paste.
- After the .pkt file is reopened (every device reboots), the 2960's DHCP snooping drops the replies the relays send back, even with `ip dhcp snooping trust` on the uplink members and on the Port-channel. In Packet Tracer turn DHCP snooping and DAI off on A1 and A2 (`no ip dhcp snooping`, `no ip arp inspection vlan 10,20,30,40`): DAI checks ARP against the snooping bindings, so it cannot stay on alone. The real 2960s on the lab day keep both, and the lab-day tests check them.
- Changing a bundle's mode (on, PAgP, LACP) leaves the old protocol on the Port-channel. Delete the Port-channel, recreate it with its trunk settings first, then add the members with the same settings, or the members are suspended ("vlan mask is different").
- Save the .pkt file in the oldest Packet Tracer version you use: a file saved in a newer version does not open in an older one.
- The WLC-2504's Config tab has no VLAN field for the management interface, so its management traffic is untagged: on D2 G1/0/10 set `switchport trunk native vlan 99` (Packet Tracer only; VLAN 40 stays tagged). Do not set VLAN Identifier 99 on the WLC's management interface in Packet Tracer: with the management VLAN tagged, the links start flapping (green, then orange) and the EtherChannels fall apart. The wifi interface takes Port Number 1 (the WLC's socket) and VLAN Identifier 40. On the real controller the management interface is tagged VLAN 99 and the native VLAN stays 999.
- Packet Tracer's lightweight AP does not tunnel the wireless clients to the WLC: it sends their traffic untagged out of its own switch port, so a laptop on `HU01-STAFF` lands in the AP port's VLAN (VLAN 99 on A1 Fa0/19), not in VLAN 40. A trunk on the AP port does not change this, and with the AP in VLAN 40 it does not rejoin the WLC after a reset (R1 accepts `option 43 ip` but the AP ignores it; `option 43 hex` is rejected). Keep the AP in VLAN 99 and test the login, DNS and internet from there; on the real controller the WLAN's interface `wifi` puts the clients in VLAN 40. Port security stays off on A1 Fa0/19 in Packet Tracer, because every client's MAC appears on that port.
- After a distribution switch reboots, the other one can stay Active for the IPv6 HSRP groups (10xx) while the IPv4 groups preempt back: `shutdown` / `no shutdown` on its VLAN interfaces where it should be Standby clears it.
- To power-cycle the AP, use the Reset button on its Physical tab: after pulling the power adapter, it stays off until Reset.
- The lightweight AP takes its address only by DHCP. The campus server's DHCP service hands out 10.20.99.21-25 in VLAN 99 with the WLC address (option 43) 10.20.99.20, so the AP finds the controller. R1's pools still leave VLAN 99 out.
- Adding a VLAN to a bundle (`switchport trunk allowed vlan add 50`) one interface at a time suspends the members ("vlan mask is different") for the moment they differ from the Port-channel. Shut the members, change the members and the Port-channel, then `no shutdown`.
- R1's reservation pool for the ESP32 (`host` + `hardware-address`) ends up without its `host` line in Packet Tracer, and the router then stops at that empty pool for every pool defined after it: a relayed Discover for VLAN 50 gets "the DHCP pool does not have any more available IP addresses". Remove the `ESP32-NODE-1` pool in Packet Tracer (the ESP32 stand-in takes its address from the IOT pool); the real router keeps the reservation.
- The 7960 IP phone needs its power adapter (the 2960-24TT has no PoE) and learns the voice VLAN by CDP. Packet Tracer's MAC table lists the phone in VLAN 10 only, although it takes its address in VLAN 50; `show ip dhcp binding` on R1 is the proof.
- Packet Tracer has no `debug ip dhcp server` or `show mac address-table vlan`: follow a DHCP request in Simulation mode (filter DHCP) and read each device's OSI Model tab for the reason a packet is dropped.
- A PC does not repeat SLAAC after a reload: switch its IPv6 setting to Static and back to Automatic. The PC command line has no `tracert -d`; use `tracert`.

If Packet Tracer rejects a line, note it (device, line, message) for the README: some Packet Tracer versions do not know every IOS command (for example `ip arp inspection validate` on the 2960, or IPv6 HSRP on the 3650). The real switches on the lab day are the reference.

## 4. WLC and AP (15 min)

WLC1: management interface 10.20.99.20/24, gateway 10.20.99.1, VLAN 99. New dynamic interface `wifi` on VLAN 40, 10.20.40.10/24, gateway 10.20.40.1, DHCP server 10.20.254.1. RADIUS server 10.20.99.53 (shared secret also on the server's AAA service). WLAN `HU01-STAFF`, interface `wifi`, security WPA2 + 802.1X. The AP on A1 Fa0/19 (access VLAN 99) takes 10.20.99.21-25 and the controller address 10.20.99.20 from the campus server's DHCP service (R1 has no VLAN 99 pool). A laptop with a wireless card joins `HU01-STAFF` with a user from the RADIUS server.

## 5. Check (15 min)

Wait about a minute for STP and EtherChannel, then:

| Test | Expected |
| --- | --- |
| PCs get addresses (Desktop, IP Configuration, DHCP) | 10.20.10.21, 10.20.20.21, ...; the ESP32 stand-in gets 10.20.30.21 once its MAC is in `network.yaml` |
| ping between VLANs | works (inter-VLAN routing on D1/D2) |
| ping 64.100.1.10 and 2001:db8:100:1::a from a PC | works (default routes to the ISP, routes back on the ISP) |
| `tracert 64.100.1.10` from a VLAN 10 PC and a VLAN 30 PC | the hops listed in [CONNECTIVITY.md](CONNECTIVITY.md): 10.20.10.2, 10.20.255.1, 203.0.113.1, 64.100.1.10 from VLAN 10 |
| on R1 `ping 64.100.1.10 source Loopback0` | replies (the ISP routes the whole site block back) |
| `show etherchannel summary` on every switch | every Po (SU), members (P) |
| `show standby brief` on D1 | Active for 10, 20, 99; Standby for 30, 40, 50 |
| `show spanning-tree` on A1 | as `python -m campus stp` predicts |
| shut D1's link to R1, `show ip route` on R1 | the floating route via 10.20.255.6 appears |
| shut D1 entirely, ping from a VLAN 10 PC | a few lost pings, then D2 answers as the gateway (HSRP) |
| a second PC on A1 Fa0/17 (sensor port) | the port goes err-disabled (port security, shutdown) |

Save the show outputs of Packet Tracer in `captures/pt/` and run `python -m campus verify captures/pt`. Screenshots to `docs/img/`.

## Side lab: router-on-a-stick (module 4, 15 min)

A new file with a 1941 router and a 2960 switch, G0/1 to G0/1. Paste `configs/roas/R1-ROAS.cfg` and `configs/roas/S1-ROAS.cfg` (or the `build/roas` copies with real passwords), put a PC on Fa0/1, Fa0/5 and Fa0/9, and run the tests in [ROUTER_ON_A_STICK.md](ROUTER_ON_A_STICK.md): `show interfaces trunk` on the switch, `show vlans` on the router, DHCP on the PCs, ping and tracert between VLANs (one hop through the router).

## Optional: R1 as a DHCP client of the ISP (module 7)

Set `dhcp_client: true` on R1's G0/0 in `network.yaml`, run `python -m campus generate --out build`, and paste the new `build/R1.cfg` and `build/ISP.cfg`. `show dhcp lease` on R1 shows 203.0.113.2 leased from 203.0.113.1, `show ip route` shows `S* 0.0.0.0/0 [254/0] via 203.0.113.1`, and the pings to the server still work.
