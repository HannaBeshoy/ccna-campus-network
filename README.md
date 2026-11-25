# CCNA Campus Network

[![CI](https://github.com/HannaBeshoy/ccna-campus-network/actions/workflows/ci.yml/badge.svg)](https://github.com/HannaBeshoy/ccna-campus-network/actions/workflows/ci.yml)

**Programming Network Devices 1 course project (Cisco CCNA: Switching, Routing and Wireless Essentials)** · University of Debrecen, Faculty of Informatics · Cisco IOS, Python · **configurations generated and checked offline; Packet Tracer build and lab test on the Cisco switches next**

The network of a small factory test hall, designed the way the SRWE course builds one, module by module: VLANs, trunks, inter-VLAN routing on Layer 3 switches, Rapid PVST+, EtherChannel, DHCPv4 with relays, SLAAC, stateless and stateful DHCPv6, HSRP for the default gateway, switch security, a wireless LAN on a controller, and static routing with a floating backup route, tested end to end against a dual-stack server behind an ISP router. The whole network is described in one file, [network.yaml](network.yaml); a Python tool generates the IOS configuration of all five devices from it, checks the design for mistakes, checks every configuration against a security policy, predicts the spanning-tree result, and on the lab day reads the show-command output from the real switches and says what matches the design and what does not.

- [x] [network.yaml](network.yaml): 7 VLANs (one a voice VLAN), IPv4 and IPv6 addressing, 5 LACP EtherChannels, HSRP and STP roots per VLAN, DHCP pools and a reservation for the ESP32 sensor node
- [x] `python -m campus generate`: [configs/](configs/) for R1, ISP, D1, D2, A1, A2, with password placeholders; real passwords come from a local `secrets.yaml` that is never committed
- [x] `python -m campus check`: about 30 checks for design mistakes (overlapping subnets, a port used twice, mismatched EtherChannel ends, an access switch without two uplinks, an unassigned port, ...)
- [x] `python -m campus lint`: the switch-security policy of modules 10 and 11 and the device hardening of ITN 16 on any config file, generated or saved from a real switch
- [x] `python -m campus stp`: which Port-channel is root, designated or blocking in every VLAN, before anything is cabled
- [x] `python -m campus diff` and `verify`: compare a running-config and saved show commands with the design
- [x] `python -m campus trace` and `paths`: every router's routing table built from the configs, packets followed hop by hop to the dual-stack server behind the ISP, with links or switches taken down ([docs/PATHS.md](docs/PATHS.md)); this found a real gap, fixed with a floating default route between the distribution switches
- [x] `python -m campus vlsm`: every subnet sized from the hosts it must hold plus growth, a VLSM allocation next to the /24 plan, /30 against /31 links ([docs/VLSM.md](docs/VLSM.md))
- [x] `python -m campus connectivity`: the ping and tracert test plan with every expected hop in IPv4 and IPv6, the failover paths and extended pings from the routers ([docs/CONNECTIVITY.md](docs/CONNECTIVITY.md)); `verify` checks the saved outputs. The extended ping found that the ISP only routed the VLAN summary back
- [x] `python -m campus roas`: the router-on-a-stick variant (one router with a subinterface per VLAN, one 2960) from the same model, for the module 4 lab or a lab without Layer 3 switches ([docs/ROUTER_ON_A_STICK.md](docs/ROUTER_ON_A_STICK.md))
- [x] option: R1 takes its outside address by DHCP from the ISP (`ip address dhcp`, default route from the offer with AD 254), checked by the simulator
- [x] **146 tests pass** in CI, and CI fails if the committed configs differ from what the model generates
- [ ] Packet Tracer build ([steps](docs/PACKET_TRACER.md)) and the test on the lab's Cisco switches ([test sheet](docs/LAB_DAY.md))

## Topology

```mermaid
flowchart TB
    SRV[Dual Stack Server<br/>64.100.1.10<br/>2001:db8:100:1::a] --- ISP[ISP router<br/>routes the site block back]
    ISP ---|"203.0.113.0/30<br/>2001:db8:f::/64"| R1[R1 Cisco 2911<br/>DHCP server, static routes]
    R1 ---|"10.20.255.0/30"| D1[D1 Catalyst 3650<br/>HSRP active VLAN 10, 20, 99<br/>STP root VLAN 10, 20, 99]
    R1 ---|"10.20.255.4/30"| D2[D2 Catalyst 3650<br/>HSRP active VLAN 30, 40, 50<br/>STP root VLAN 30, 40, 50]
    D1 ===|"Po1 LACP 2x1G"| D2
    D1 ===|Po2| A1[A1 Catalyst 2960]
    D2 ===|Po3| A1
    D1 ===|Po4| A2[A2 Catalyst 2960]
    D2 ===|Po5| A2
    D2 ---|trunk 40, 99| WLC[WLC1]
    A1 --- PC1[staff PCs + IP phones<br/>VLAN 10 + voice 50]
    A1 --- ESP[ESP32 node<br/>VLAN 30]
    A1 --- AP[AP<br/>VLAN 99]
    A2 --- TS[test stations<br/>VLAN 20]
```

| VLAN | Name | IPv4 | IPv6 | Gateway: HSRP active |
| ---: | --- | --- | --- | --- |
| 10 | STAFF | 10.20.10.0/24 | 2001:db8:acad:10::/64 | D1 |
| 20 | PRODUCTION | 10.20.20.0/24 | 2001:db8:acad:20::/64 (stateful DHCPv6) | D1 |
| 30 | IOT | 10.20.30.0/24 | 2001:db8:acad:30::/64 (SLAAC + stateless DHCPv6) | D2 |
| 40 | WIFI | 10.20.40.0/24 | 2001:db8:acad:40::/64 | D2 |
| 50 | VOICE | 10.20.50.0/24 (IP phones on the staff ports) | 2001:db8:acad:50::/64 | D2 |
| 99 | MGMT | 10.20.99.0/24 | 2001:db8:acad:99::/64 | D1 |
| 999 | PARKING | unused ports, native VLAN of every trunk | | |

The full addressing plan is generated into [docs/ADDRESSING.md](docs/ADDRESSING.md), and the subnet sizes are checked against the expected number of hosts in [docs/VLSM.md](docs/VLSM.md).

## Design decisions

| Decision | Why | Module |
| --- | --- | --- |
| The STP root and the HSRP active router of a VLAN are the same switch | otherwise frames to the gateway cross the inter-switch link for nothing; `primary:` in the YAML sets both, so they cannot drift apart | 5, 9 |
| A /24 per VLAN with the VLAN number in the third octet, not VLSM | the /17 has room; a VLSM plan would fit in a /21, but 10.20.30.x = VLAN 30 is readable at a glance; every pool is checked to hold its hosts + 50% growth | ITN 11 |
| Two distribution switches share the load: D1 for VLANs 10, 20, 99, D2 for 30, 40, 50 | both uplinks of every access switch carry traffic, each for some VLANs | 5, 9 |
| Every link between switches is a 2-member LACP EtherChannel | a cable can fail without STP reconverging, and STP sees one link instead of two | 6 |
| Routing on the Layer 3 switches (SVIs), routed uplinks to R1 | inter-VLAN traffic never leaves the distribution layer; no router-on-a-stick bottleneck (the router-on-a-stick variant is generated for comparison and as a fallback) | 4, 14 |
| DHCP server on R1, relays on the SVIs | one place for all pools; the relay fills in giaddr so the server picks the right pool | 7 |
| All three IPv6 address methods: SLAAC only, stateless DHCPv6 on IOT, stateful DHCPv6 on PRODUCTION | sensors only need a DNS server; test stations get recorded addresses, so a test result can be traced to the station | 8 |
| R1 reaches the campus by one summary route, with a floating static (AD 10) through D2 | one line instead of five, and an automatic backup when D1's link fails | 15 |
| Each distribution switch has a floating default route (AD 10) through the other one over VLAN 99 | HSRP does not notice an uplink failure, so the switch stays the gateway; without this route its VLANs lose the outside (found by `campus paths`) | 9, 15 |
| An ISP router and a dual-stack server outside the campus, with static routes back | the default routes can be tested end to end with ping and tracert in IPv4 and IPv6; NAT comes in the next course | 15, 16 |
| R1's outside address is static; DHCP from the ISP is one line away (`dhcp_client: true`) | a static address keeps the ISP's route back predictable; the DHCP option keeps the /30, so the only address the ISP can hand out is the same one | 7 |
| The ISP routes the whole site block (/16, /48) back; R1 sends it to Null0 | the loopback and the links to D1/D2 are outside the VLAN summary (found by an extended ping from Loopback0); the Null0 route keeps traffic for unused addresses from bouncing between R1 and the ISP | 15, ITN 17 |
| A /32 and a /128 host route to the WLC through D2 | longest match beats the summary, so controller traffic goes straight to the switch it hangs off | 14, 15 |
| IPv6 static routes are fully specified: exit interface + the neighbour's manual link-local address | a link-local address is unique only on its link; the next hop stays the same if global prefixes are renumbered | 8, 15 |
| A voice VLAN on the staff desk ports, CoS trusted, port security maximum 3 | one cable for phone and PC; speech keeps its priority; the phone's MAC appears in both VLANs | 3, 11 |
| Native VLAN 999 on every trunk, DTP off, unused ports shut down in VLAN 999 | stops VLAN hopping (double tagging and switch spoofing) | 10, 11 |
| Port security (sticky), PortFast + BPDU guard, DHCP snooping with rate limit, Dynamic ARP Inspection on every access port | MAC flooding, rogue switches, rogue DHCP servers and ARP spoofing are each blocked by one of these | 11 |
| SSH version 2 only, local users with `secret`, `service password-encryption` | no plain-text management traffic or stored passwords | 1, 10 |
| `security passwords min-length 10`, `login block-for 120 attempts 3 within 60`, logged logins, 10-minute idle timeout | short passwords refused, password guessing slowed down and visible in the log (ITN 16.4) | ITN 16 |
| Passwords are placeholders in the repository | a configuration in a public repository must not carry real credentials | |

## Spanning tree, predicted

`python -m campus stp` runs the four steps of module 5 on the design (Cisco short path costs: a 2×100 Mb/s EtherChannel costs 12, 2×1 Gb/s costs 3):

```text
VLAN 10: root bridge D1                    VLAN 30: root bridge D2
  A1  Po2  Root FWD                         A1  Po2  Altn BLK
  A1  Po3  Altn BLK                         A1  Po3  Root FWD
  D2  Po1   Root FWD                         D1  Po1   Root FWD
  D2  Po3  Desg FWD                         D1  Po2  Desg FWD
```

In every VLAN exactly one uplink of each access switch blocks, the one towards the switch that is not that VLAN's root, and no port of the distribution layer blocks. No decision depends on a MAC address, so the real switches must show the same roles; `verify` checks this on the lab day.

## Running it

```bash
pip install -r requirements.txt
python -m campus check                 # the design
python -m campus generate              # configs/*.cfg (placeholders)
cp secrets.example.yaml secrets.yaml   # set real lab passwords, then:
python -m campus generate --out build  # ready-to-paste configs in build/ (not committed)
python -m campus lint configs/*.cfg
python -m campus stp
python -m campus vlsm                  # subnet sizes from the host counts
python -m campus trace vlan10 server --down R1:G0/1   # follow a packet with D1's uplink down
python -m campus paths                 # every VLAN to the server, five failure scenarios
python -m campus connectivity          # ping/tracert plan with the expected hops
python -m campus roas                  # router-on-a-stick variant in configs/roas/
python -m campus diff A1 saved_running_config.txt
python -m campus verify captures       # show-command outputs from the lab
python -m pytest -v tests
```

## Repository layout

```text
network.yaml        the design
campus/             model and checks, generator, policy lint, running-config diff, STP prediction, routing simulator, lab verifier
templates/          Jinja2 templates of the router, distribution and access configurations
configs/            the generated configurations (R1, ISP, D1, D2, A1, A2); configs/roas/ the router-on-a-stick variant
docs/               project guide (module by module), addressing plan, VLSM sizing, end-to-end paths, ping/tracert plan, router-on-a-stick sheet, Packet Tracer steps, lab-day test sheet
tests/              146 tests; fixtures/lab holds hand-written show outputs in IOS format
captures/           where the real lab-day outputs go
```

The [project guide](docs/PROJECT_GUIDE.md) explains each part with the SRWE module it comes from.

## Author

Beshoy Hanna · Computer Science Engineering BSc, University of Debrecen · [LinkedIn](https://www.linkedin.com/in/beshoy-hanna-8b52213a0)
