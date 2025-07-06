#!/bin/bash

set -e

USER=thermo
CURDIR=$(pwd)
GROUP=users

if ! id -u $USER 2>/dev/null >/dev/null ; then
  echo "Adding user $USER..."
  useradd -G $GROUP,sudo,dialout $USER
else
  usermod -G $GROUP,sudo,dialout $USER
fi
if [ ! -d /home/$USER ] ; then
  mkdir -p /home/$USER
  chown -R $USER:$GROUP /home/$USER
  chmod 0700 /home/$USER
fi

echo "Installing curl, git and python3-pip..."
apt-get install -y curl git python3-pip pkg-config

echo "Installing Mosquitto..."
apt-get install -y mosquitto mosquitto-clients

mosquitto_passwd -b -c /etc/mosquitto/pwfile $USER $USER
tee /etc/mosquitto/conf.d/zigbee2mqtt.conf <<EOF
listener 1883
password_file /etc/mosquitto/pwfile
EOF

systemctl start mosquitto
systemctl enable mosquitto

if [ ! -f /etc/apt/sources.list.d/nodesource.list ]; then
  echo "Installing NodeJS repository..."
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
fi

apt-get install -y nodejs git make g++ gcc libsystemd-dev
su $USER -c "corepack enable"

if [ ! -d /opt/zigbee2mqtt ] ; then
  mkdir -p /opt/zigbee2mqtt
fi

chown -R $USER:$GROUP /opt/zigbee2mqtt

echo "Installing Zigbee2MQTT..."
if [ ! -f /opt/zigbee2mqtt/index.js ] ; then
  git clone --depth 1 --branch 2.5.1 https://github.com/Koenkk/zigbee2mqtt.git /opt/zigbee2mqtt 
fi

if [ ! -f /etc/sysctl.d/50-unprivileged-ports.conf ] ; then
  echo 'net.ipv4.ip_unprivileged_port_start=0' > /etc/sysctl.d/50-unprivileged-ports.conf
  sysctl --system
fi

su $USER -c "cd /opt/zigbee2mqtt && echo Y | pnpm install --frozen-lockfile"

tee /etc/systemd/system/zigbee2mqtt.service <<EOF
[Unit]
Description=zigbee2mqtt
After=network.target mosquitto.service

[Service]
Environment=NODE_ENV=production
Type=notify
ExecStart=/usr/bin/node index.js
WorkingDirectory=/opt/zigbee2mqtt
StandardOutput=null
StandardError=inherit
WatchdogSec=10s
Restart=always
RestartSec=10s
User=$USER

[Install]
WantedBy=multi-user.target
EOF

echo "Enabling multicast DNS for discovery..."
sed -ie 's/^#MulticastDNS=yes/MulticastDNS=yes/' /etc/systemd/resolved.conf
sed -ie 's/^#LLMNR=yes/LLMNR=no/' /etc/systemd/resolved.conf

for iface in $(basename -a /sys/class/net/* |grep -v "^lo$")
do
  if [ ! -d /etc/systemd/network/10-netplan-${iface}.network.d ] ; then
    mkdir -p /etc/systemd/network/10-netplan-${iface}.network.d
    tee /etc/systemd/network/10-netplan-${iface}.network.d/mdns.conf <<EOF
[Network]
MulticastDNS=yes
EOF
  fi
  tee /etc/systemd/system/multicast-dns-${iface}.service << EOF
[Unit]
Description=Enable MulticastDNS on $iface network link
After=systemd-resolved.service

[Service]
ExecStart=resolvectl mdns $iface on

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable multicast-dns-${iface}
  systemctl start multicast-dns-${iface}
done
systemctl restart systemd-resolved

if [ ! -d /etc/systemd/dnssd ] ; then
  mkdir -p /etc/systemd/dnssd
fi

tee /etc/systemd/dnssd/http.service <<EOF
[Service]
Name=%H
Type=_http._tcp
Port=80
TxtText=path=/
EOF


systemctl daemon-reload
systemctl start zigbee2mqtt
systemctl enable zigbee2mqtt

echo "Setting systemd-journald only for in-memory logs..."
sed -ie 's/^#Storage=auto/Storage=volatile/' /etc/systemd/journald.conf
sed -ie 's/^#RuntimeMaxUse=/RuntimeMaxUse=128M/' /etc/systemd/journald.conf
systemctl restart systemd-journald

echo "Disabling Debian madness for Python..."
if [ -f /usr/lib/python3.*/EXTERNALLY-MANAGED ] ; then
  rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED
fi

echo "Installing Python Thermostat code and web application..."
apt-get install -y nginx

if [ ! -d /opt/zigbee-thermostat-connector ] ; then
  git clone https://github.com/rosmo/zigbee-thermostat-connector.git /opt/zigbee-thermostat-connector
fi
cd /opt/zigbee-thermostat-connector
git reset --hard origin/main
git pull origin main

chmod -R 0755 /opt/zigbee-thermostat-connector
pip3 install -r requirements.txt

cd web/thermostat
npm install
npm run build
cd ../..

if [ -e /etc/nginx/sites-enabled/default ] ; then
  rm -f /etc/nginx/sites-enabled/default 
fi

cp /opt/zigbee-thermostat-connector/nginx.conf /etc/nginx/sites-enabled/connector.conf
systemctl restart nginx

tee /etc/systemd/system/zigbee-thermostat-connector.service <<EOF
[Unit]
Description=zigbee thermostat connector
After=network.target mosquitto.service zigbee2mqtt.service

[Service]
Type=notify
ExecStart=python3 main.py thermostat.yaml 
WorkingDirectory=/opt/zigbee-thermostat-connector
StandardOutput=inherit
StandardError=inherit
WatchdogSec=10s
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl restart zigbee-thermostat-connector
systemctl enable zigbee-thermostat-connector



