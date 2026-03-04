#!/bin/bash
# ============================================
# IP Watch AI - Hetzner VPS Setup Script
# Run this on a fresh Ubuntu 22.04 VPS
# ============================================
set -e

echo "============================================"
echo "IP Watch AI - Cloud Server Setup"
echo "============================================"

# 1. Update system
echo "[1/7] Updating system packages..."
apt-get update && apt-get upgrade -y

# 2. Install Docker Engine
echo "[2/7] Installing Docker Engine..."
apt-get install -y ca-certificates curl gnupg lsb-release
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. Configure firewall
echo "[3/7] Configuring UFW firewall..."
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
# Allow Postgres from specific IP (UPDATE THIS with your local PC's public IP)
# ufw allow from YOUR_IP to any port 5433
echo "WARNING: Postgres port 5433 is NOT opened by default."
echo "Run: ufw allow from YOUR_PUBLIC_IP to any port 5433"
ufw --force enable

# 4. Create project directories
echo "[4/7] Creating project directories..."
mkdir -p /opt/ipwatch/{bulletins,clients,cache/huggingface,cache/torch,cache/easyocr}
mkdir -p /opt/ipwatch/app

# 5. Set up SSH key (if not already done)
echo "[5/7] SSH configuration..."
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload sshd || true

# 6. Optimize system for PostgreSQL
echo "[6/7] Optimizing system settings..."
cat >> /etc/sysctl.conf << 'EOF'
# PostgreSQL optimizations
vm.swappiness=10
vm.overcommit_memory=1
net.core.somaxconn=65535
EOF
sysctl -p

# 7. Set up swap (important for 8GB RAM)
echo "[7/7] Setting up 4GB swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo ""
echo "============================================"
echo "Server setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Clone your repo:    git clone <repo-url> /opt/ipwatch/app"
echo "  2. Copy credentials:   scp .env.cloud cloudflared/ to /opt/ipwatch/app/"
echo "  3. Import database:    See scripts/export_db.sh on your local PC"
echo "  4. Start services:     cd /opt/ipwatch/app && docker compose -f docker-compose.cloud.yml --env-file .env.cloud up -d"
echo "  5. Open Postgres port: ufw allow from YOUR_IP to any port 5433"
echo ""
