# Project guide

The network, module by module of CCNA Switching, Routing and Wireless Essentials (SRWE). Each section names the generated lines to look at, so the guide can be read next to [configs/](../configs/). The study guide explains each module from zero; this guide shows where it is used.

| Module | Topic | Where in the project |
| ---: | --- | --- |
| 1 | basic device configuration, SSH | `_base.j2`: hostname, secrets, SSH v2, vty lines; SVI 99 on the access switches |
| 2 | switching concepts | why the access layer floods unknown unicast and why VLANs split broadcast domains |
| 3 | VLANs, trunks, DTP | `vlan` blocks, every trunk `switchport nonegotiate`, native VLAN 999; voice VLAN 50 with CoS trust on the staff ports |
| 4 | inter-VLAN routing | SVIs on D1/D2 with `ip routing`; routed uplinks (`no switchport`) to R1 |
| 5 | STP | `spanning-tree mode rapid-pvst`, priorities per VLAN; `python -m campus stp` |
| 6 | EtherChannel | 5 LACP bundles, `channel-group N mode active` |
| 7 | DHCPv4 | pools on R1, `ip helper-address` on the SVIs, a manual binding for the ESP32 |
| 8 | SLAAC and DHCPv6 | all three methods: SLAAC only (VLANs 10, 40, 99), stateless DHCPv6 (VLAN 30, O flag), stateful DHCPv6 (VLAN 20, M flag, A flag off); relays to R1; manual link-local addresses |
| 9 | FHRP, HSRP | HSRP version 2 per VLAN, IPv4 and IPv6 groups, priority 110 + preempt on the primary |
| 10 | LAN security concepts | the threats the access-port settings answer |
| 11 | switch security configuration | port security, unused ports, VLAN hopping, DHCP snooping, DAI, PortFast, BPDU guard |
| 12 | WLAN concepts | lightweight AP (CAPWAP) on A1, WLC on D2, WPA2-Enterprise |
| 13 | WLAN configuration | WLC settings: interface on VLAN 40, DHCP via the relay, RADIUS |
| 14 | routing concepts | longest match: the WLC's /32 beats the campus /17; administrative distance |
| 15 | static routing | default, summary, floating (AD 10) and host routes, IPv4 and IPv6; IPv6 fully specified with link-local next hops; the ISP and dual-stack server for end-to-end tests |
| 16 | troubleshooting static routes | `python -m campus trace` and `paths` before the lab, `verify` on `show ip route`; the failover tests |

## 1. One file, generated configurations

Writing five configurations by hand, the same VLAN list ends up typed ten times and the same mistake made in one place. Here the design lives in `network.yaml` and the configurations are generated, so a VLAN added in one line appears on every trunk, every switch and the router's DHCP pools. CI regenerates the configurations on every push and fails if the committed files differ: the repository can never show configurations that do not match the design.

## 2. Basic configuration and SSH (module 1)

Every device gets the same base: `no ip domain-lookup` (a mistyped command does not hang trying to resolve it), `service password-encryption`, an `enable secret` and a local user with `secret` (hashed, not reversible), the RSA key (`crypto key generate rsa general-keys modulus 2048`, needed before SSH works), `ip ssh version 2`, and vty lines with `transport input ssh` and `login local`. The access switches are managed through an SVI in VLAN 99 with `ip default-gateway 10.20.99.1`, because a Layer 2 switch does not route.

## 3. VLANs and trunks (modules 2 and 3)

Six VLANs separate staff PCs, test stations, sensors, wireless clients, IP phones and management: each is its own broadcast domain and its own IPv4 subnet. Trunks carry them between switches with 802.1Q tags. Three settings on every trunk:

- `switchport trunk allowed vlan 10,20,30,40,50,99`: only the VLANs that are used;
- `switchport trunk native vlan 999`: untagged frames land in a VLAN that carries nothing (against double-tagging);
- `switchport nonegotiate`: no DTP, so a PC cannot negotiate a trunk by pretending to be a switch.

**Voice VLAN (3.2.6 - 3.3.7).** A staff desk has a PC plugged into the back of an IP phone, and one cable to the switch. The port stays an access port in VLAN 10 for the PC's untagged frames and adds `switchport voice vlan 50`: the switch tells the phone (by CDP) to tag its own frames with VLAN 50 and a CoS priority. `mls qos trust cos` makes the switch keep that priority instead of resetting it, so speech goes first when the uplink is busy (`mls qos` turns QoS on for the 2960). Port security on these ports allows three MAC addresses: the PC, and the phone, which the switch learns in the data VLAN and in the voice VLAN. `show interfaces fa0/1 switchport` shows "Voice VLAN: 50 (VOICE)". The voice VLAN gets everything the other VLANs get from the model: an HSRP gateway on D2, a DHCP pool, snooping and DAI, and a place on every trunk.

## 4. Inter-VLAN routing (module 4)

D1 and D2 are Layer 3 switches: `ip routing` and one SVI (`interface VlanN`) per VLAN, each the gateway of its subnet. This is the module's third method, after legacy routing (one router port per VLAN) and router-on-a-stick (one trunk to a router): the routing happens in the switch hardware, at wire speed. The links to R1 are routed ports (`no switchport` with an IP address), not trunks.

## 5. Rapid PVST+ (module 5)

With two uplinks per access switch there are loops, so spanning tree must block one path per VLAN. The design chooses which: D1 has priority 24576 (root) for VLANs 10, 20, 99 and 28672 (secondary) for 30 and 40; D2 the other way round. The access switches keep the default 32768. `python -m campus stp` computes the result (README). Rapid PVST+ instead of the original 802.1D because it converges in about a second instead of 30 to 50, and one tree per VLAN is what lets the two distribution switches share the load.

## 6. EtherChannel (module 6)

Each pair of switches is connected by two cables bundled with LACP (`channel-group N mode active` on both ends: active-active or active-passive forms a bundle, passive-passive does not). STP sees one logical link; if one cable fails, the bundle carries on with the other and STP does not even notice. The trunk settings are on the member ports and on the Port-channel interface, because every member must match (speed, duplex, mode, allowed VLANs, native VLAN), or it is suspended (`s` in `show etherchannel summary`).

## 7. DHCPv4 (module 7)

R1 is the DHCP server for VLANs 10, 20, 30 and 40: one pool each, with `.1` to `.20` excluded (gateways, switches, printers). The clients are not on R1's subnets, so each SVI has `ip helper-address 10.20.254.1`: the switch turns the client's broadcast into a unicast to R1 and writes its own address (giaddr) into it, which is how R1 knows which pool to use. The ESP32 sensor node gets a fixed address from a manual binding (`host 10.20.30.21` + `hardware-address`; its real MAC goes into `network.yaml` on the lab day).

## 8. IPv6: SLAAC, stateless and stateful DHCPv6 (module 8)

Every VLAN has a /64 and every SVI an IPv6 address, so routers send Router Advertisements. Three flags in the RA tell a host how to get its address, and the design uses all three methods:

| Method | VLANs | RA flags | SVI lines | The host |
| --- | --- | --- | --- | --- |
| SLAAC only | 10 STAFF, 40 WIFI, 99 MGMT | A = 1, O = 0, M = 0 | none extra | builds its address from the prefix; DNS must come another way (RDNSS or by hand) |
| SLAAC + stateless DHCPv6 | 30 IOT | A = 1, O = 1 | `ipv6 nd other-config-flag`, relay | builds its address, asks DHCPv6 only for the DNS server and domain (Information-Request) |
| stateful DHCPv6 | 20 PRODUCTION | A = 0, M = 1 | `ipv6 nd managed-config-flag`, `ipv6 nd prefix default no-autoconfig`, relay | gets its address from the server (Solicit, Advertise, Request, Reply); the server records who has which address |

Clearing the A flag matters: with only the M flag a host builds a SLAAC address too and ends up with two. In every case the default gateway still comes from the RA, never from DHCPv6.

The SVIs relay to R1 (`ipv6 dhcp relay destination 2001:db8:acad:254::1`). R1 has one pool, `CAMPUS-DHCPV6`, bound to both interfaces the relayed requests arrive on. It holds the DNS server and domain for everyone, and `address prefix 2001:db8:acad:20::/64` with `link-address 2001:db8:acad:20::/64`: the relay puts the VLAN's address into the request, and the server hands out addresses only to requests from that link. The SVIs also get a fixed link-local address (`FE80::2` on D1, `FE80::3` on D2), which makes `show` output and neighbor tables readable.

## 9. HSRP (module 9)

Hosts have one default gateway. If that switch fails, they are cut off, unless the gateway address is virtual. Each VLAN has an HSRP group on D1 and D2 with the virtual address `.1`; the primary switch has priority 110 and `preempt`, so it takes the role back after a reboot. Version 2 is used because it supports IPv6 groups and group numbers above 255: the IPv6 group of VLAN N is 1000 + N, with `ipv6 autoconfig` for its virtual link-local address.

## 10. Switch security (modules 10 and 11)

Every access port:

| Line | Against |
| --- | --- |
| `switchport mode access` + `switchport nonegotiate` | switch spoofing (a PC negotiating a trunk) |
| `switchport port-security`, `maximum 2` (1 for sensors and the AP), `mac-address sticky` | MAC address table flooding; a foreign device on a sensor port |
| `violation restrict` (PCs) or `shutdown` (sensors) | restrict drops and counts; on a sensor port, a new MAC means someone unplugged the sensor, so the port shuts down |
| `spanning-tree portfast` + `bpduguard enable` | slow startup of end devices; a switch plugged into a user port (STP attack) |
| `ip dhcp snooping limit rate 10` | DHCP starvation |

Globally: `ip dhcp snooping vlan ...` with only the Port-channels to the distribution switches trusted (a rogue DHCP server on a user port is ignored), and `ip arp inspection vlan ...`, which checks every ARP reply against the snooping bindings (ARP spoofing). `no ip dhcp snooping information option` is needed in this design: otherwise the access switch inserts option 82 with an empty giaddr and the IOS DHCP server drops the request. Unused ports are shut down and parked in VLAN 999. `python -m campus lint` checks all of this on any configuration file.

## 11. Wireless (modules 12 and 13)

A lightweight AP on A1 builds a CAPWAP tunnel to the controller WLC1, which hangs off D2 on a trunk carrying VLAN 40 (client traffic) and VLAN 99 (management). The WLC is configured in its web interface, not by this tool: an interface on VLAN 40 with the gateway 10.20.40.1 and the DHCP server 10.20.254.1, and the WLAN `HU01-STAFF` with WPA2-Enterprise against a RADIUS server (the module 13 procedure). The AP's switch port is an access port in VLAN 99 with `maximum 1`.

## 12. Static routing (modules 14 to 16)

R1 has four kinds of static route, each from module 15:

| Route | Kind |
| --- | --- |
| `ip route 0.0.0.0 0.0.0.0 203.0.113.1` | default route to the ISP |
| `ip route 10.20.0.0 255.255.128.0 10.20.255.2` | one summary for all campus VLANs, through D1 |
| `ip route 10.20.0.0 255.255.128.0 10.20.255.6 10` | floating static: the same route through D2 with administrative distance 10. It stays out of the routing table while the AD 1 route exists, and appears when D1's link goes down |
| `ip route 10.20.99.20 255.255.255.255 10.20.255.6` | host route: longest match wins over the /17, so traffic to the WLC goes straight to D2 |

The same set exists for IPv6 (a /56 summary and a /128 host route to the WLC), written as **fully specified** routes (SRWE 15.2.6): `ipv6 route 2001:db8:acad::/56 GigabitEthernet0/1 FE80::2`. Every routed link has manual link-local addresses (FE80::1 on R1, FE80::2 on D1, FE80::3 on D2, FE80::A on the ISP), and a link-local address is only unique on its own link, so IOS needs the exit interface with it. IPv4 keeps recursive routes (next hop only), so both forms appear in the design. D1 and D2 have a default route to R1, and a floating default route (AD 10) through each other over VLAN 99. Module 16's troubleshooting is the lab-day failover test: shut D1's uplink, watch the floating route appear with `show ip route`, bring it back.

### The outside world and the simulator

Outside the campus there is now an ISP router and a dual-stack server (64.100.1.10, 2001:db8:100:1::a), the topology of SRWE 15.1.5. Without NAT (next course) the ISP needs static routes back to the campus summaries. With a target outside, the default routes can be tested end to end from every VLAN, in IPv4 and IPv6.

`python -m campus trace` and `paths` do that test before the lab. They build each device's routing table from the generated configuration the way IOS does: a connected route for every interface that is up, a static route only if its next hop is reachable, the lowest administrative distance for each prefix, then the longest match. Taking an interface down removes its connected route on both ends of the link, so R1's AD 1 route through D1 disappears and the floating AD 10 route through D2 is installed, exactly as `show ip route` will show.

The first run found a gap. With D1's uplink down, R1's floating route brought return traffic in through D2, but outbound traffic from VLANs 10, 20 and 99 still went to D1, the HSRP active router, and D1 had no route out: HSRP watches the other router, not the uplink. The fix is a floating default route on each distribution switch through the other one over VLAN 99. [PATHS.md](PATHS.md) shows every VLAN in five scenarios: everything up, either uplink down, either switch off.

## 13. The tool

| Command | Does |
| --- | --- |
| `check` | the design: subnets overlap? gateway outside its subnet? a port in two roles, or forgotten? EtherChannel ends with different member counts? each access switch with an uplink to both distribution switches? STP priorities valid? reservation outside the pool? |
| `generate` | the five configurations from Jinja2 templates; passwords from `secrets.yaml` or placeholders |
| `lint` | the module 10/11 policy on any configuration file |
| `diff` | what a saved running-config is missing compared with the design (it understands that a running-config lists interfaces one by one, hides secrets and omits `no shutdown`) |
| `stp` | the spanning-tree prediction |
| `plan` | the addressing plan, docs/ADDRESSING.md |
| `trace` | one packet hop by hop through the routing tables, with links or devices down |
| `paths` | every VLAN to the dual-stack server and back in five failure scenarios, docs/PATHS.md |
| `verify` | the lab-day show outputs against the design: VLANs, EtherChannels, HSRP states, STP roles, R1's routes |

## Notes for the three tracks

- **Embedded systems.** The ESP32 node of the networking project joins VLAN 30 with a reserved address, a sticky single-MAC port and DHCP snooping: the way an industrial network admits a device.
- **FPGA to semiconductors.** Switch ASICs do the forwarding the configuration describes: MAC tables, VLAN tags, the hash that spreads frames over EtherChannel members.
- **Product security.** Module 11 in practice, and a lint that turns a security policy into a check that runs on every change.

## Possible extensions

- OSPF instead of static routes (the next course, ENSA)
- ACLs between the VLANs and NAT on R1 (also ENSA)
- push the configurations with Netmiko over SSH, and run `verify` directly on live output
