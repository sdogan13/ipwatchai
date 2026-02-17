#!/bin/bash
# ============================================
# IP Watch AI - Server Setup Script
# Run on a fresh Ubuntu 22.04/24.04 server
# Usage: bash deploy/setup-server.sh
# ============================================
set -e

echo "=== IP Watch AI Server Setup ==="

# Update system
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

# Install Docker
if ! command -v docker &> /dev/null; then
    echo "[+] Installing Docker..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo systemctl enable docker && sudo systemctl start docker
    sudo usermod -aG docker $USER
    echo "[OK] Docker installed"
else
    echo "[OK] Docker already installed"
fi

# Install Docker Compose plugin
if ! docker compose version &> /dev/null; then
    echo "[+] Installing Docker Compose plugin..."
    sudo apt-get install -y docker-compose-plugin
    echo "[OK] Docker Compose installed"
else
    echo "[OK] Docker Compose already installed"
fi

# Check for GPU and install NVIDIA container toolkit if present
HAS_GPU=false
if command -v nvidia-smi &> /dev/null; then
    HAS_GPU=true
    echo "[+] GPU detected, installing NVIDIA Container Toolkit..."
    distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null || true
    curl -s -L "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    echo "[OK] NVIDIA Container Toolkit installed"
else
    echo "[INFO] No GPU detected — using CPU mode"
fi

# Configure firewall
echo "[+] Configuring firewall..."
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
echo "[OK] Firewall configured"

# Configure Docker log rotation
echo "[+] Configuring Docker log rotation..."
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
    "log-driver": "json-file",
    "log-opts": { "max-size": "50m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
echo "[OK] Log rotation configured"

# Create app directory
sudo mkdir -p /opt/trademark-app
sudo chown $USER:$USER /opt/trademark-app

# Update AI_DEVICE based on GPU
if [ "$HAS_GPU" = true ]; then
    sed -i 's/AI_DEVICE=cpu/AI_DEVICE=cuda/' /opt/trademark-app/deploy/.env.prod 2>/dev/null || true
    sed -i 's/USE_FP16=false/USE_FP16=true/' /opt/trademark-app/deploy/.env.prod 2>/dev/null || true
    sed -i 's/USE_TF32=false/USE_TF32=true/' /opt/trademark-app/deploy/.env.prod 2>/dev/null || true
    echo "[OK] GPU mode enabled in .env.prod"
fi

echo ""
echo "=== Server setup complete ==="
echo "Next steps:"
echo "  1. Transfer app code to /opt/trademark-app/"
echo "  2. cd /opt/trademark-app && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build"
echo "  3. docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d"
