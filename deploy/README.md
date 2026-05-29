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

## Sub-agent services

Install and start the POC strategist and presentation sub-agents:

    sudo cp deploy/oci-poc-strategist.service /etc/systemd/system/
    sudo cp deploy/oci-presentation.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now oci-poc-strategist oci-presentation

## View logs

    journalctl -u oci-bom -f

## Port map

| Service              | Port |
|----------------------|------|
| oci-agent            | 8080 |
| oci-diagram          | 8082 |
| oci-bom              | 8083 |
| oci-pov              | 8084 |
| oci-jep              | 8085 |
| oci-waf              | 8086 |
| oci-terraform        | 8087 |
| oci-tech-research    | 8088 |
| oci-sales-deck       | 8089 |
| poc_strategist sub-agent | 8090 |
| presentation sub-agent | 8091 |
