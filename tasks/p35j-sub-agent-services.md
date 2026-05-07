# Task p35j: Systemd Service Files for All Sub-Agents

## Goal

Create a systemd service file for each of the six sub-agent processes so they
start automatically with the system and restart on failure. This mirrors the
existing `deploy/oci-agent.service` pattern. Each sub-agent becomes an
independently managed service — no process supervision logic needed in the main
server or in SkillForge.

---

## Prerequisite Check

```bash
ls deploy/oci-agent.service    # template — must exist
cat config.yaml | grep -A8 "sub_agents:"
```

Expected ports from config.yaml:
- diagram:   8082
- bom:       8083
- pov:       8084
- jep:       8085
- waf:       8086
- terraform: 8087

---

## Scope

**Only create** (all under `deploy/`):
- `deploy/oci-bom.service`
- `deploy/oci-diagram.service`
- `deploy/oci-pov.service`
- `deploy/oci-jep.service`
- `deploy/oci-waf.service`
- `deploy/oci-terraform.service`

**Do NOT modify any existing file.**

---

## Service file template

Base each file on `deploy/oci-agent.service`. The only differences per service
are `Description`, the uvicorn module path, and the port.

### `deploy/oci-bom.service`

```ini
[Unit]
Description=OCI Archie BOM Sub-Agent
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/drawing-agent
EnvironmentFile=-/home/opc/.drawing-agent.env
ExecStart=/bin/bash -lc 'set -a; [ -f /home/opc/.drawing-agent.env ] && . /home/opc/.drawing-agent.env; exec /usr/bin/python3.11 -m uvicorn sub_agents.bom.server:app --host 0.0.0.0 --port 8083'
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### `deploy/oci-diagram.service`

Same pattern, module `sub_agents.diagram.server:app`, port `8082`.

### `deploy/oci-pov.service`

Same pattern, module `sub_agents.pov.server:app`, port `8084`.

### `deploy/oci-jep.service`

Same pattern, module `sub_agents.jep.server:app`, port `8085`.

### `deploy/oci-waf.service`

Same pattern, module `sub_agents.waf.server:app`, port `8086`.

### `deploy/oci-terraform.service`

Same pattern, module `sub_agents.terraform.server:app`, port `8087`.

---

## Deployment instructions (add to `deploy/README.md`)

Create `deploy/README.md` with:

```markdown
# Deployment

## Install all services

Copy service files to systemd and enable:

    sudo cp deploy/oci-*.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable oci-agent oci-bom oci-diagram oci-pov oci-jep oci-waf oci-terraform
    sudo systemctl start  oci-agent oci-bom oci-diagram oci-pov oci-jep oci-waf oci-terraform

## Check status

    sudo systemctl status oci-bom oci-diagram oci-pov oci-jep oci-waf oci-terraform

## Restart a single sub-agent

    sudo systemctl restart oci-bom

## View logs

    journalctl -u oci-bom -f

## Port map

| Service          | Port |
|------------------|------|
| oci-agent        | 8080 |
| oci-diagram      | 8082 |
| oci-bom          | 8083 |
| oci-pov          | 8084 |
| oci-jep          | 8085 |
| oci-waf          | 8086 |
| oci-terraform    | 8087 |
```

---

## Acceptance Criteria

1. `ls deploy/oci-*.service | wc -l` — output is `7`
2. `grep "port 808" deploy/oci-bom.service` — matches `8083`
3. `grep "port 808" deploy/oci-diagram.service` — matches `8082`
4. `grep "sub_agents.bom.server:app" deploy/oci-bom.service` — matches
5. `grep "Restart=always" deploy/oci-terraform.service` — matches
6. `ls deploy/README.md` — exists

---

## Do NOT Do

- Do not modify `drawing_agent_server.py` or any Python file
- Do not add process-spawning logic to SkillForge or archie_loop
- Do not change ports — use the values from `config.yaml`

---

## Commit Message

```
p35j: add systemd service files for all six sub-agents (ports 8082–8087)
```
