# WireGuard VPN: Setting Up a Tunnel «Client A ↔ Server ↔ Client B»

> **Goal:** establish a secure tunnel between two machines (Peer 1 and Peer 2) via an intermediate WireGuard server.

---

## Connection Diagram

```
Computer A (Peer 1)           WireGuard Server           Computer B (Peer 2)
  10.0.1.2/24       <──────>   10.0.1.1/24   <──────>     10.0.1.3/24
                               (relay / router)
```

The server is **not** an endpoint of the communication — it only forwards traffic between Peer 1 and Peer 2 via IP forwarding.

---

## Address Plan

| Role       | VPN Address | Port  |
|------------|-------------|-------|
| Server     | 10.0.1.1/24 | 51820 |
| Peer 1 (A) | 10.0.1.2/24 | —     |
| Peer 2 (B) | 10.0.1.3/24 | —     |

---

## 1. Server Setup

### 1.1 Install WireGuard

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install wireguard-tools -y

# Fedora / CentOS / RHEL
sudo dnf install wireguard-tools -y
```

### 1.2 Generate a Key Pair

```bash
umask 077
wg genkey | tee privatekey | wg pubkey > publickey
```

> `umask 077` must be set before key generation — otherwise the private key will have insecure file permissions.

Display the keys:
```bash
cat privatekey
cat publickey
```

### 1.3 Enable IP Forwarding

Without this the server cannot relay packets between peers.

```bash
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

Verify that forwarding is active:
```bash
sysctl net.ipv4.ip_forward
# Expected output: net.ipv4.ip_forward = 1
```

### 1.4 Configuration `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <Server_Private_Key>
Address    = 10.0.1.1/24
ListenPort = 51820

# iptables rules: allow forwarding and NAT when the interface comes up
PostUp   = iptables -A FORWARD -i %i -j ACCEPT; \
           iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; \
           iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# ─── Peer 1 (Computer A) ──────────────────────────────────────────────────
[Peer]
PublicKey  = <Peer1_Public_Key>
AllowedIPs = 10.0.1.2/32      # Accept traffic only from this address

# ─── Peer 2 (Computer B) ──────────────────────────────────────────────────
[Peer]
PublicKey  = <Peer2_Public_Key>
AllowedIPs = 10.0.1.3/32      # Accept traffic only from this address
```

> **`eth0`** — replace with the actual name of the server's external network interface.  
> Find it with: `ip -o link show | awk '{print $2}' | grep -v lo`

### 1.5 Start and Enable Autostart

```bash
sudo systemctl enable --now wg-quick@wg0
```

Check status:
```bash
sudo wg show
sudo systemctl status wg-quick@wg0
```

---

## 2. Peer 1 Setup (Computer A)

### 2.1 Install WireGuard and Generate Keys

```bash
sudo apt install wireguard -y

umask 077
wg genkey | tee privatekey | wg pubkey > publickey
```

The public key (`publickey`) will need to be shared with the server.

### 2.2 Configuration `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <Peer1_Private_Key>
Address    = 10.0.1.2/24

[Peer]
PublicKey           = <Server_Public_Key>
Endpoint            = <Server_Public_IP>:51820
AllowedIPs          = 10.0.1.0/24   # All VPN subnet traffic goes through the server
PersistentKeepalive = 25            # Keep-alive interval in seconds; important behind NAT
```

> **`PersistentKeepalive`** — sends a keepalive packet every 25 seconds.  
> Required when the machine is behind NAT (most home and office networks).

### 2.3 Start and Enable Autostart

```bash
sudo systemctl enable --now wg-quick@wg0
```

> `enable --now` simultaneously **registers the service for autostart** and **starts it immediately**.  
> After this the tunnel will come up automatically on every system boot — no manual action required.

---

## 3. Peer 2 Setup (Computer B)

### 3.1 Install WireGuard and Generate Keys

```bash
sudo apt install wireguard -y

umask 077
wg genkey | tee privatekey | wg pubkey > publickey
```

### 3.2 Configuration `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <Peer2_Private_Key>
Address    = 10.0.1.3/24

[Peer]
PublicKey           = <Server_Public_Key>
Endpoint            = <Server_Public_IP>:51820
AllowedIPs          = 10.0.1.0/24
PersistentKeepalive = 25
```

### 3.3 Start and Enable Autostart

```bash
sudo systemctl enable --now wg-quick@wg0
```

> Same as Peer 1: the tunnel starts immediately and will come up automatically after every reboot.

---

## 4. Managing the Tunnel on the Client

### 4.1 Where Settings Are Stored

The configuration lives in `/etc/wireguard/wg0.conf`. This file is read every time the interface is brought up — changes take effect after restarting the tunnel.

```
/etc/wireguard/
├── wg0.conf          ← main config (interface + peer list)
├── peer1_private.key ← private key (root only, chmod 600)
└── peer1_public.key  ← public key (safe to share)
```

### 4.2 Service Management Commands

| Command | What it does | Persists across reboot? |
|---|---|---|
| `systemctl start wg-quick@wg0` | Start the tunnel right now | No |
| `systemctl stop wg-quick@wg0` | Stop the tunnel | — |
| `systemctl enable wg-quick@wg0` | Add to autostart (without starting immediately) | Yes |
| `systemctl disable wg-quick@wg0` | Remove from autostart | No |
| `systemctl enable --now wg-quick@wg0` | Start + add to autostart | Yes |
| `systemctl restart wg-quick@wg0` | Restart (apply config changes) | — |

> **`enable --now` is recommended** — the tunnel is active immediately and will start automatically after any reboot.

### 4.3 Checking the Status

```bash
# Brief systemd service status
sudo systemctl status wg-quick@wg0

# Detailed WireGuard status (peers, handshakes, traffic)
sudo wg show

# Confirm the interface is up
ip link show wg0
```

Signs of correct operation in `systemctl status` output:
```
● wg-quick@wg0.service - WireGuard via wg-quick(8) for wg0
     Loaded: loaded (/lib/systemd/system/wg-quick@.service; enabled; ...)  ← enabled = autostart is on
     Active: active (exited) since ...                                      ← active = tunnel is running
```

### 4.4 Applying Config Changes Without Stopping the Tunnel

If you need to update `/etc/wireguard/wg0.conf` (e.g. add a new peer) without dropping the connection:

```bash
sudo wg syncconf wg0 <(wg-quick strip wg0)
```

If a hot reload is not required — simply restart:

```bash
sudo systemctl restart wg-quick@wg0
```

### 4.5 Temporarily Disabling the Tunnel

```bash
# Stop the tunnel (autostart is preserved)
sudo systemctl stop wg-quick@wg0

# Stop AND remove from autostart
sudo systemctl disable --now wg-quick@wg0
```

---

## 5. NetworkManager and WireGuard (Fedora / KDE / GNOME)

On desktop systems with NetworkManager (Fedora, Ubuntu Desktop, KDE, GNOME) a conflict arises:
NetworkManager sees the `wg0` interface and allows the user to disconnect it via the GUI — which brings the tunnel down,
even if the `systemd` service is enabled.

There are two approaches depending on your needs:

---

### Approach A: Hide `wg0` from NetworkManager (recommended)

> Use this option if you want the tunnel to be **always on**, regardless of the GUI.  
> `wg-quick` + `systemd` manage the interface; NM neither sees it nor touches it.

**1. Create an exclusion file:**

```bash
sudo tee /etc/NetworkManager/conf.d/99-unmanaged-wireguard.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:wg0
EOF
```

**2. Apply:**

```bash
sudo systemctl reload NetworkManager
```

After this `wg0` disappears from the network applet — NM will no longer touch it.
The tunnel is managed exclusively by `systemd` via `wg-quick@wg0`.

> To exclude multiple interfaces, list them with commas:  
> `unmanaged-devices=interface-name:wg0,interface-name:wg1`

---

### Approach B: Hand WireGuard Management to NetworkManager (native Fedora way)

> Use this option if you want to manage the tunnel **from the GUI** (KDE / GNOME Network Manager)
> while still having it come up automatically.

> ⚠️ With this approach **do not use** `wg-quick@wg0` (systemd) — two managers will conflict.  
> If the service was previously enabled, disable it:
> ```bash
> sudo systemctl disable --now wg-quick@wg0
> ```

**1. Create the connection via nmcli:**

```bash
# Replace <PRIVATE_KEY> with the contents of /etc/wireguard/peer1_private.key
sudo nmcli connection add \
  type wireguard \
  con-name wg0 \
  ifname wg0 \
  autoconnect yes \
  wireguard.private-key "<PRIVATE_KEY>"
```

**2. Assign the IP address:**

```bash
# For Peer 1:
sudo nmcli connection modify wg0 \
  ipv4.method manual \
  ipv4.addresses 10.0.1.2/24

# For Peer 2:
sudo nmcli connection modify wg0 \
  ipv4.method manual \
  ipv4.addresses 10.0.1.3/24
```

**3. Add a peer (the server):**

```bash
sudo nmcli connection modify wg0 \
  +wireguard.peers "public-key=<SERVER_PUBLIC_KEY>,endpoint=<SERVER_IP>:51820,allowed-ips=10.0.1.0/24,persistent-keepalive=25"
```

**4. Bring the tunnel up:**

```bash
sudo nmcli connection up wg0
```

The connection will appear in the network applet. The `autoconnect yes` parameter means NM will
bring it up automatically on the next system boot.

**The connection profile is stored here:**

```
/etc/NetworkManager/system-connections/wg0.nmconnection
```

> ⚠️ **Important:** if the user **manually disconnects** the connection via the GUI (not deletes, but disconnects),
> NM will not reconnect until the next reboot or a manual `nmcli connection up wg0`.  
> If you need to fully ignore manual disconnections — use **Approach A**.

---

### Comparison of Approaches

| | Approach A (`wg-quick` + systemd) | Approach B (NetworkManager) |
|---|---|---|
| Tunnel visible in GUI | No | Yes |
| Resistant to manual GUI disconnect | Yes | No |
| Autostart on boot | Yes (`systemctl enable`) | Yes (`autoconnect yes`) |
| Suitable for servers and headless machines | ✅ | ❌ |
| Suitable for desktop (KDE/GNOME) | ✅ (hidden from GUI) | ✅ (managed from GUI) |

---

## 6. Adding Peers to the Server (Hot Update)

Once both peers are configured and their public keys are known, add them to the running interface without a restart:

```bash
sudo wg set wg0 peer <Peer1_Public_Key> allowed-ips 10.0.1.2/32
sudo wg set wg0 peer <Peer2_Public_Key> allowed-ips 10.0.1.3/32
```

Save the changes to the config file:
```bash
sudo wg-quick save wg0
```

---

## 7. Verification and Diagnostics

### Tunnel Status

```bash
sudo wg show
```

Expected output when the connection is established:
```
interface: wg0
  public key: <key>
  private key: (hidden)
  listening port: 51820

peer: <Peer1 key>
  endpoint: <IP>:<port>
  allowed ips: 10.0.1.2/32
  latest handshake: X seconds ago    ← indicates a live connection
  transfer: X MiB received, X MiB sent

peer: <Peer2 key>
  ...
```

### Connectivity Check Between Peers

```bash
# From Peer 1 → to Peer 2
ping 10.0.1.3

# From Peer 2 → to Peer 1
ping 10.0.1.2

# From any peer → to the server
ping 10.0.1.1
```

### Common Problems

| Symptom | Likely Cause | Fix |
|---|---|---|
| No `latest handshake` | Server unreachable or port closed | Check `ufw`/`firewalld`, open UDP 51820 |
| Ping to server works, peer-to-peer does not | IP forwarding not enabled | `sysctl net.ipv4.ip_forward` must return `1` |
| Connection drops behind NAT | Missing keepalive | Add `PersistentKeepalive = 25` to the peer config |
| `RTNETLINK answers: Operation not permitted` | Running without sudo | Use `sudo` |

### Opening the Port on the Server (if UFW is used)

```bash
sudo ufw allow 51820/udp
sudo ufw reload
```

---

## Final Checklist

- [ ] Server: WireGuard installed, keys generated
- [ ] Server: IP forwarding enabled (`net.ipv4.ip_forward=1`)
- [ ] Server: config contains both peers with correct keys and `AllowedIPs`
- [ ] Server: UDP port 51820 open in the firewall
- [ ] Peer 1: keys generated, config points to the server, service added to autostart (`systemctl enable --now`)
- [ ] Peer 2: keys generated, config points to the server, service added to autostart (`systemctl enable --now`)
- [ ] Peer public keys added to the server
- [ ] `wg show` shows `latest handshake` for both peers
- [ ] `ping 10.0.1.2` from Peer 2 (and vice versa) succeeds
