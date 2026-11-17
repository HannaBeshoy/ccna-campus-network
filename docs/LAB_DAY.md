# Lab-day test sheet: the lab's Cisco switches

About 2 hours with the lab's routers and switches. The lab's models may differ from the design (for example 2960s instead of 3650s for the distribution layer, or a 1941 instead of a 2911): change `model:` and the port names in `network.yaml`, run `python -m campus check` and `generate`, and use the new configurations. A Layer 2 only switch cannot be D1/D2; then use a router-on-a-stick variant (module 4) and note it.

## Before

- `secrets.yaml` with the lab passwords; `python -m campus generate --out build`
- a console cable or USB console, PuTTY or another terminal
- erase each device first: `write erase`, `delete flash:vlan.dat` (switches), `reload`

## 1. Configure (30 min)

Paste `build/<name>.cfg` into each device in configuration mode, R1 first. Save. Write down any line the device rejects.

## 2. Capture (20 min)

Save the output of these commands into `captures/<device>_<command>.txt` (spaces as underscores), for example with PuTTY's session log:

| Device | Commands |
| --- | --- |
| A1, A2 | `show vlan brief`, `show etherchannel summary`, `show spanning-tree`, `show port-security`, `show ip dhcp snooping binding` |
| D1, D2 | `show etherchannel summary`, `show standby brief`, `show spanning-tree`, `show ip route` |
| R1 | `show ip route`, `show ipv6 route` (the /56 and ::/0 via FE80:: addresses with their interfaces), `show ipv6 interface brief`, `show ip dhcp binding` |
| each | `show running-config` (as `<device>_running-config.txt`) |
| a laptop in VLAN 10 and in VLAN 30, R1 | the pings and tracerts of [CONNECTIVITY.md](CONNECTIVITY.md) section 5 (`vlan10_tracert_server.txt`, `vlan30_tracert6_server.txt`, `R1_ping_server_source_Loopback0.txt`, ...) |

Then:

```bash
python -m campus verify captures
for d in R1 D1 D2 A1 A2; do python -m campus diff $d captures/${d}_running-config.txt; done
python -m campus lint captures/*_running-config.txt
```

## 3. Tests (50 min)

Record each result (and the command output) in `docs/lab_day.md`.

| # | Test | How | Expected |
| ---: | --- | --- | --- |
| 1 | DHCP | a laptop on A1 Fa0/1, then Fa0/9 | 10.20.10.x, then 10.20.20.x; `show ip dhcp binding` on R1 |
| 2 | SLAAC + stateless DHCPv6 | the laptop on Fa0/17 (VLAN 30), `ipconfig /all` | a 2001:db8:acad:30:: address and the DNS server 2001:db8:acad:99::53 |
| 2b | stateful DHCPv6 | the laptop on A2 Fa0/1 (VLAN 20), `ipconfig /all`; on R1 `show ipv6 dhcp binding` | one 2001:db8:acad:20:: address (no second SLAAC address), listed in R1's binding table |
| 3 | inter-VLAN routing | ping from VLAN 10 to a VLAN 20 host | replies |
| 3a | end to end | from VLAN 10 and VLAN 30: `tracert -d 64.100.1.10`, `tracert -d 2001:db8:100:1::a`; on R1 `ping 64.100.1.10 source Loopback0` | the hops of [CONNECTIVITY.md](CONNECTIVITY.md); the first hop is the switch's own SVI address; R1's extended ping replies |
| 4 | EtherChannel | unplug one cable of Po11 during a continuous ping | no loss; `show etherchannel summary` shows one member (D) |
| 5 | HSRP failover | continuous ping to 10.20.10.1, reload D1 | a few seconds of loss, D2 becomes Active; D1 takes it back after the reload (preempt) |
| 6 | STP | `show spanning-tree` on A1 | `verify` passes; Po12 Altn in VLAN 10, Root in VLAN 30 |
| 7 | floating static | `shutdown` on D1 G1/0/24, then from a VLAN 10 laptop `ping 64.100.1.10` and `tracert 64.100.1.10` | R1 installs 10.20.0.0/17 via 10.20.255.6 [10/0]; D1 installs 0.0.0.0/0 via 10.20.99.3 [10/0]; the ping still works and tracert shows D1, D2, R1, ISP (as `python -m campus trace vlan10 server --down R1:G0/1`); `no shutdown` brings back [1/0] |
| 8 | port security | a second device behind a small hub/switch on a sensor port | the port goes err-disabled; recover with `shutdown` / `no shutdown` |
| 9 | BPDU guard | plug a spare switch into Fa0/2 | Fa0/2 err-disabled (BPDU guard) |
| 10 | rogue DHCP | a laptop running a DHCP server on Fa0/3 | clients still get R1's addresses; snooping drops the offers |
| 11 | DAI | a static IP that is not in the snooping table, then ping | ARP is dropped (`show ip arp inspection statistics`) |
| 12 | SSH only | `telnet 10.20.99.11` and `ssh -l admin 10.20.99.11` | telnet refused, SSH works |
| 12a | login blocking | three wrong passwords over SSH within a minute, then the right one; `show login` on A1 | the fourth login is refused for 120 s; `show login` shows quiet mode; the log lists the failures |
| 12b | voice VLAN | an IP phone (or a PC set to tag VLAN 50) on A1 Fa0/2 with a laptop behind it; `show interfaces fa0/2 switchport`, `show mac address-table interface fa0/2` | Voice VLAN: 50; the phone gets a 10.20.50.x address, the laptop 10.20.10.x; the phone's MAC is listed in VLAN 10 and 50 |
| 13 | ESP32 | the sensor node (esp32-network-node project) on Fa0/17 with its MAC in the reservation | gets 10.20.30.21; the SNMP collector in VLAN 10 reads it |

## Afterwards

- commit `captures/` (without passwords: the running-configs show only hashes, check anyway) and `docs/lab_day.md`
- tick the box in the README, change the status line, and change the CV line to "tested on Cisco hardware"
