#!/bin/bash
# ============================================
# IP Watch AI - VPS Relay Setup Script
# Run this on the Vultr VPS (78.141.238.79)
# ============================================
set -e

echo "=== IP Watch AI - VPS Relay Setup ==="

# 1. Update system
echo "[1/5] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# 2. Install cloudflared
echo "[2/5] Installing cloudflared..."
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | tee /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq && apt-get install -y -qq cloudflared

# 3. Create config directory
echo "[3/5] Setting up cloudflared config..."
mkdir -p /etc/cloudflared

# 4. Create cloudflared config (points to localhost:8080 which will be the SSH tunnel)
cat > /etc/cloudflared/config.yml << 'EOF'
tunnel: ac15dd3b-36fa-4d60-8cf5-f71c4c78e0ec
credentials-file: /etc/cloudflared/ac15dd3b-36fa-4d60-8cf5-f71c4c78e0ec.json

ingress:
  - hostname: ipwatchai.com
    service: http://localhost:8080
    originRequest:
      noTLSVerify: true

  - hostname: www.ipwatchai.com
    service: http://localhost:8080
    originRequest:
      noTLSVerify: true

  - service: http_status:404
EOF

echo "[3/5] Config created. You still need to copy the credentials JSON file."

# 5. Create systemd service for cloudflared
echo "[4/5] Creating systemd service..."
cat > /etc/systemd/system/cloudflared-tunnel.service << 'EOF'
[Unit]
Description=Cloudflare Tunnel for IP Watch AI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cloudflared-tunnel

# 6. Configure SSH for reverse tunnel keepalive
echo "[5/5] Configuring SSH..."
cat >> /etc/ssh/sshd_config << 'EOF'

# IP Watch AI - Keep reverse tunnels alive
ClientAliveInterval 30
ClientAliveCountMax 3
GatewayPorts no
EOF
systemctl restart sshd

echo ""
echo "=== Setup complete! ==="
echo "Next steps:"
echo "  1. Copy tunnel credentials: scp cloudflared/*.json root@78.141.238.79:/etc/cloudflared/"
echo "  2. Start tunnel: systemctl start cloudflared-tunnel"
echo "  3. Run SSH reverse tunnel from your PC (see ssh_tunnel.ps1)"
