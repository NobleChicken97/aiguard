#!/bin/bash
# EC2 first-boot bring-up for the free-tier single-box deploy.
# Rendered at launch time with placeholders filled (the rendered file with
# secrets is NEVER committed): __DOMAIN__, __LLM_API_KEY__, __SESSION_SECRET__.
# Logs to /var/log/netsentry-bringup.log on the box.
set -eux
exec > /var/log/netsentry-bringup.log 2>&1

# Swap: the 1 GB box needs breathing room (NER model + PG + app).
fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile swap swap defaults 0 0' >> /etc/fstab

# Docker + compose.
dnf update -y
dnf install -y docker git curl
systemctl enable --now docker
if ! dnf install -y docker-compose-plugin; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -SL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# App source + secrets (root-only file, never in the repo).
mkdir -p /opt/netsentry && cd /opt/netsentry
git clone https://github.com/NobleChicken97/agentic_guardrails.git .
cat > .env <<'ENVEOF'
LLM_PROVIDER=groq
LLM_API_KEY=__LLM_API_KEY__
SESSION_SECRET=__SESSION_SECRET__
SESSION_COST_BUDGET_USD=0.50
LOG_LEVEL=INFO
LOG_FORMAT=json
DATABASE_URL=postgresql://admin:password@postgres:5432/agentic_db
ENVEOF
chmod 600 .env

# Bring up app + postgres + redis + caddy, then seed demo rows
# (schema auto-creates on boot; seed is a separate explicit step).
export DOMAIN="__DOMAIN__"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import sys; sys.path.insert(0,'.'); from db.database import get_connection; get_connection().close()"; then
    break
  fi
  sleep 5
done
docker compose exec -T app python -m db.seed
echo "BRINGUP COMPLETE"
