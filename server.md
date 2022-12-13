
Postgres users:
```
nacsos (admin): $v5aCh$kGE9ybF@XtE*%CETA&4bXt8vu$NTZUxi*J@f%
nacsos_user (user): HqVxmmuJgPR#4dYL8m%32JLG$c#%kkwqoWB4vQay@Rbr
```

UNIX users:
```
username: nacsos
password: none
home at /home/nacsos
(not sudo, no extra groups)
```

## Systemd Setup

### NACSOS-core
Create `/.config/systemd/user/nacsos-core.service`

```
[Unit]
Description=NACSOS core server
After=network.target

[Service]
User=nacsos
WorkingDirectory=/home/nacsos/nacsos-core
LimitNOFILE=4096
ExecStart=/home/nacsos/nacsos-core/venv/bin/python -m hypercorn main:app --config=config/hypercorn-server.toml
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable nacsos-core
sudo systemctl start nacsos-core
```


### NACSOS-pipes
Create `/etc/systemd/system/nacsos-pipes.service`

```
[Unit]
Description=NACSOS core server
After=network.target

[Service]
User=nacsos
WorkingDirectory=/home/nacsos/nacsos-core
LimitNOFILE=4096
ExecStart=/home/nacsos/nacsos-core/venv/bin/python -m hypercorn main:app --config=config/hypercorn-server.toml
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```