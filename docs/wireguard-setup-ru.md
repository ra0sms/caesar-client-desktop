# WireGuard VPN: Настройка туннеля «Клиент A ↔ Сервер ↔ Клиент B»

> **Цель:** организовать защищённый туннель между двумя машинами (Peer 1 и Peer 2) через промежуточный WireGuard-сервер.

---

## Схема подключения

```
Компьютер A (Peer 1)          Сервер WireGuard          Компьютер B (Peer 2)
  10.0.1.2/24       <──────>   10.0.1.1/24   <──────>     10.0.1.3/24
                              (посредник/роутер)
```

Сервер **не является** конечной точкой связи — он только пересылает трафик между Peer 1 и Peer 2 через IP-форвардинг.

---

## Адресный план

| Роль       | VPN-адрес   | Порт  |
|------------|-------------|-------|
| Сервер     | 10.0.1.1/24 | 51820 |
| Peer 1 (A) | 10.0.1.2/24 | —     |
| Peer 2 (B) | 10.0.1.3/24 | —     |

---

## 1. Настройка сервера

### 1.1 Установка WireGuard

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install wireguard -y

# Fedora / CentOS / RHEL
sudo dnf install wireguard-tools -y
```

### 1.2 Генерация ключевой пары

```bash
umask 077
wg genkey | tee privatekey | wg pubkey > publickey
```

> `umask 077` — обязательно перед генерацией, иначе приватный ключ получит небезопасные права доступа.

Посмотреть ключи:
```bash
cat privatekey
cat publickey
```

### 1.3 Включение IP-форвардинга

Без этого сервер не сможет передавать пакеты между пирами.

```bash
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

Проверить, что форвардинг включён:
```bash
sysctl net.ipv4.ip_forward
# Ожидаемый вывод: net.ipv4.ip_forward = 1
```

### 1.4 Конфигурация `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <Приватный_Ключ_Сервера>
Address    = 10.0.1.1/24
ListenPort = 51820

# Правила iptables: разрешить форвардинг и NAT при старте интерфейса
PostUp   = iptables -A FORWARD -i %i -j ACCEPT; \
           iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; \
           iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# ─── Peer 1 (Компьютер A) ─────────────────────────────────────────────────
[Peer]
PublicKey  = <Публичный_Ключ_Peer1>
AllowedIPs = 10.0.1.2/32      # Трафик только с этого адреса

# ─── Peer 2 (Компьютер B) ─────────────────────────────────────────────────
[Peer]
PublicKey  = <Публичный_Ключ_Peer2>
AllowedIPs = 10.0.1.3/32      # Трафик только с этого адреса
```

> **`eth0`** — замените на реальное имя внешнего интерфейса сервера.  
> Узнать его: `ip -o link show | awk '{print $2}' | grep -v lo`

### 1.5 Запуск и автозапуск

```bash
sudo systemctl enable --now wg-quick@wg0
```

Проверить статус:
```bash
sudo wg show
sudo systemctl status wg-quick@wg0
```

---

## 2. Настройка Peer 1 (Компьютер A)

### 2.1 Установка и генерация ключей

```bash
sudo apt install wireguard -y

umask 077
wg genkey | tee privatekey | wg pubkey > publickey
```

Публичный ключ (`publickey`) нужно будет передать на сервер.

### 2.2 Конфигурация `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <Приватный_Ключ_Peer1>
Address    = 10.0.1.2/24

[Peer]
PublicKey           = <Публичный_Ключ_Сервера>
Endpoint            = <Публичный_IP_Сервера>:51820
AllowedIPs          = 10.0.1.0/24   # Весь трафик в VPN-подсети идёт через сервер
PersistentKeepalive = 25            # Поддерживать соединение (сек); важно за NAT
```

> **`PersistentKeepalive`** — отправляет keepalive-пакет каждые 25 секунд.  
> Необходим, если машина находится за NAT (большинство домашних/офисных сетей).

### 2.3 Запуск и автозапуск

```bash
sudo systemctl enable --now wg-quick@wg0
```

> `enable --now` — одновременно **регистрирует службу в автозапуске** и **запускает её прямо сейчас**.  
> После этого туннель будет подниматься автоматически при каждой загрузке системы — никаких ручных действий не потребуется.

---

## 3. Настройка Peer 2 (Компьютер B)

### 3.1 Установка и генерация ключей

```bash
sudo apt install wireguard -y

umask 077
wg genkey | tee privatekey | wg pubkey > publickey
```

### 3.2 Конфигурация `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <Приватный_Ключ_Peer2>
Address    = 10.0.1.3/24

[Peer]
PublicKey           = <Публичный_Ключ_Сервера>
Endpoint            = <Публичный_IP_Сервера>:51820
AllowedIPs          = 10.0.1.0/24
PersistentKeepalive = 25
```

### 3.3 Запуск и автозапуск

```bash
sudo systemctl enable --now wg-quick@wg0
```

> Аналогично Peer 1: туннель запустится сейчас и будет подниматься автоматически после перезагрузки.

---

## 4. Управление туннелем на клиенте

### 4.1 Где хранятся настройки

Конфигурация хранится в файле `/etc/wireguard/wg0.conf`. Этот файл читается каждый раз при старте интерфейса — изменения в нём вступают в силу после перезапуска туннеля.

```
/etc/wireguard/
├── wg0.conf          ← основной конфиг (интерфейс + список пиров)
├── peer1_private.key ← приватный ключ (только для root, chmod 600)
└── peer1_public.key  ← публичный ключ (можно передавать)
```

### 4.2 Режимы работы службы

| Команда | Что делает | Сохраняется после перезагрузки? |
|---|---|---|
| `systemctl start wg-quick@wg0` | Запустить туннель прямо сейчас | Нет |
| `systemctl stop wg-quick@wg0` | Остановить туннель | — |
| `systemctl enable wg-quick@wg0` | Добавить в автозапуск (без немедленного старта) | Да |
| `systemctl disable wg-quick@wg0` | Убрать из автозапуска | Нет |
| `systemctl enable --now wg-quick@wg0` | Запустить + добавить в автозапуск | Да |
| `systemctl restart wg-quick@wg0` | Перезапустить (применить изменения конфига) | — |

> **Рекомендуется использовать `enable --now`** — туннель сразу активен и будет подниматься автоматически после любой перезагрузки.

### 4.3 Проверка состояния

```bash
# Краткий статус systemd-службы
sudo systemctl status wg-quick@wg0

# Детальный статус WireGuard (пиры, рукопожатия, трафик)
sudo wg show

# Убедиться, что интерфейс поднят
ip link show wg0
```

Признаки корректной работы в выводе `systemctl status`:
```
● wg-quick@wg0.service - WireGuard via wg-quick(8) for wg0
     Loaded: loaded (/lib/systemd/system/wg-quick@.service; enabled; ...)  ← enabled = автозапуск включён
     Active: active (exited) since ...                                      ← active = туннель работает
```

### 4.4 Применение изменений конфига без остановки туннеля

Если нужно обновить `/etc/wireguard/wg0.conf` (например, добавить нового пира) без разрыва соединения:

```bash
sudo wg syncconf wg0 <(wg-quick strip wg0)
```

Если горячее обновление не нужно — просто перезапустить:

```bash
sudo systemctl restart wg-quick@wg0
```

### 4.5 Полное отключение туннеля (временно)

```bash
# Остановить туннель (автозапуск при этом сохраняется)
sudo systemctl stop wg-quick@wg0

# Остановить И убрать из автозапуска
sudo systemctl disable --now wg-quick@wg0
```

---

## 5. NetworkManager и WireGuard (Fedora / KDE / GNOME)

На десктопных системах с NetworkManager (Fedora, Ubuntu Desktop, KDE, GNOME) возникает конфликт:
NetworkManager видит интерфейс `wg0` и позволяет отключить его через GUI — после чего туннель пропадает,
даже если `systemd`-служба включена.

Есть два подхода в зависимости от задачи:

---

### Подход A: Спрятать `wg0` от NetworkManager (рекомендуется)

> Используйте этот вариант, если хотите, чтобы туннель работал **всегда**, независимо от GUI.
> `wg-quick` + `systemd` управляют интерфейсом, NM его не видит и не трогает.

**1. Создать файл исключения:**

```bash
sudo tee /etc/NetworkManager/conf.d/99-unmanaged-wireguard.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:wg0
EOF
```

**2. Применить:**

```bash
sudo systemctl reload NetworkManager
```

После этого `wg0` исчезнет из графического апплета сети — NM больше не будет его трогать.
Туннелем управляет только `systemd` через `wg-quick@wg0`.

> Чтобы исключить несколько интерфейсов, перечислите через запятую:
> `unmanaged-devices=interface-name:wg0,interface-name:wg1`

---

### Подход Б: Передать управление WireGuard в NetworkManager (нативный способ для Fedora)

> Используйте этот вариант, если хотите управлять туннелем **из GUI** (KDE / GNOME Network Manager),
> но при этом он должен подниматься автоматически.

> ⚠️ При этом подходе **не используйте** `wg-quick@wg0` (systemd) — два менеджера будут конфликтовать.
> Если служба была включена — отключите её:
> ```bash
> sudo systemctl disable --now wg-quick@wg0
> ```

**1. Создать соединение через nmcli:**

```bash
# Заменить <ПРИВАТНЫЙ_КЛЮЧ> на содержимое файла /etc/wireguard/peer1_private.key
sudo nmcli connection add \
  type wireguard \
  con-name wg0 \
  ifname wg0 \
  autoconnect yes \
  wireguard.private-key "<ПРИВАТНЫЙ_КЛЮЧ>"
```

**2. Назначить IP-адрес:**

```bash
# Для Peer 1:
sudo nmcli connection modify wg0 \
  ipv4.method manual \
  ipv4.addresses 10.0.1.2/24

# Для Peer 2:
sudo nmcli connection modify wg0 \
  ipv4.method manual \
  ipv4.addresses 10.0.1.3/24
```

**3. Добавить пир (сервер):**

```bash
sudo nmcli connection modify wg0 \
  +wireguard.peers "public-key=<ПУБЛИЧНЫЙ_КЛЮЧ_СЕРВЕРА>,endpoint=<IP_СЕРВЕРА>:51820,allowed-ips=10.0.1.0/24,persistent-keepalive=25"
```

**4. Поднять туннель:**

```bash
sudo nmcli connection up wg0
```

Соединение появится в графическом апплете сети. Параметр `autoconnect yes` означает, что NM
поднимет его при следующей загрузке системы автоматически.

**Профиль соединения хранится здесь:**

```
/etc/NetworkManager/system-connections/wg0.nmconnection
```

> ⚠️ **Важно:** если пользователь **вручную отключил** соединение через GUI (не удалил, а именно отключил),
> NM не будет переподключаться до следующей перезагрузки или ручного `nmcli connection up wg0`.
> Если нужно полностью игнорировать ручные отключения — используйте **Подход A**.

---

### Сравнение подходов

| | Подход A (`wg-quick` + systemd) | Подход Б (NetworkManager) |
|---|---|---|
| Туннель виден в GUI | Нет | Да |
| Устойчив к ручному отключению через GUI | Да | Нет |
| Автозапуск при загрузке | Да (`systemctl enable`) | Да (`autoconnect yes`) |
| Подходит для серверов и headless-машин | ✅ | ❌ |
| Подходит для десктопа (KDE/GNOME) | ✅ (скрыт из GUI) | ✅ (управляется из GUI) |

---

## 6. Добавление пиров на сервер (горячее обновление)

После того как оба пира настроены и их публичные ключи известны, можно добавить их в работающий интерфейс без перезапуска:

```bash
sudo wg set wg0 peer <Публичный_Ключ_Peer1> allowed-ips 10.0.1.2/32
sudo wg set wg0 peer <Публичный_Ключ_Peer2> allowed-ips 10.0.1.3/32
```

Сохранить изменения в конфиг:
```bash
sudo wg-quick save wg0
```

---

## 7. Проверка и диагностика

### Статус туннеля

```bash
sudo wg show
```

Ожидаемый вывод при установленном соединении:
```
interface: wg0
  public key: <ключ>
  private key: (hidden)
  listening port: 51820

peer: <ключ Peer1>
  endpoint: <IP>:<порт>
  allowed ips: 10.0.1.2/32
  latest handshake: X seconds ago    ← признак живого соединения
  transfer: X МiB received, X МiB sent

peer: <ключ Peer2>
  ...
```

### Проверка связи между пирами

```bash
# С Peer 1 → на Peer 2
ping 10.0.1.3

# С Peer 2 → на Peer 1
ping 10.0.1.2

# С любого пира → на сервер
ping 10.0.1.1
```

### Частые проблемы

| Симптом | Возможная причина | Решение |
|---|---|---|
| `latest handshake` отсутствует | Сервер недоступен или закрыт порт | Проверить `ufw`/`firewalld`, открыть UDP 51820 |
| Пинг до сервера есть, между пирами нет | Не включён IP-форвардинг | `sysctl net.ipv4.ip_forward` → должно быть `1` |
| Соединение обрывается за NAT | Нет keepalive | Добавить `PersistentKeepalive = 25` в конфиг пира |
| `RTNETLINK answers: Operation not permitted` | Запуск без sudo | Использовать `sudo` |

### Открытие порта на сервере (если используется UFW)

```bash
sudo ufw allow 51820/udp
sudo ufw reload
```

---

## Итоговый чеклист

- [ ] Сервер: WireGuard установлен, ключи сгенерированы
- [ ] Сервер: IP-форвардинг включён (`net.ipv4.ip_forward=1`)
- [ ] Сервер: конфиг содержит обоих пиров с правильными ключами и `AllowedIPs`
- [ ] Сервер: порт UDP 51820 открыт в firewall
- [ ] Peer 1: ключи сгенерированы, конфиг указывает на сервер, служба добавлена в автозапуск (`systemctl enable --now`)
- [ ] Peer 2: ключи сгенерированы, конфиг указывает на сервер, служба добавлена в автозапуск (`systemctl enable --now`)
- [ ] Публичные ключи пиров добавлены на сервер
- [ ] `wg show` показывает `latest handshake` для обоих пиров
- [ ] `ping 10.0.1.2` с Peer 2 (и наоборот) проходит
