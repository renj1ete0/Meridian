# Deploying on podman: Fedora CoreOS, and a GPU machine on Windows

> The same compose files as on Docker ([ADR 0021](../adr/0021-the-stack-calls-the-gpu-machine.md),
> `B-226`). This page covers only what podman changes. Read [deployment.md](deployment.md) for
> everything else: `.env`, first boot, the smoke run, the egress check. Its §3c says how the
> two machines talk.

Two machines:

- **The stack** on a small always-on machine with Fedora CoreOS: Postgres, the crawl, the API,
  the web app, synthesis. Sized for 16 GB of RAM and a 4–6 core CPU.
- **The GPU machine** on Windows with Podman Desktop: the embedder and a local language model.
  It may be switched off; the stack waits for it (deployment.md §3c).

## Why compose on podman, and what that costs

Podman serves Docker's API on a socket, and the `docker-compose` binary drives it unchanged, so
there is one set of compose files for both runtimes. Five things differ, and each is handled
here:

| Podman does not | So | Where |
|---|---|---|
| ship a compose binary on CoreOS | Ignition installs `docker-compose` v5.6.0, checked by sha256 | `deploy/podman/meridian.bu` |
| have a daemon that restarts containers after a reboot | `meridian.service` runs `compose up -d` at boot | `deploy/podman/meridian.service` |
| offer Docker's API to Watchtower reliably | Watchtower is parked; a timer pulls and recreates through compose | `podman.yml`, `meridian-update.timer` |
| let a container read a bind mount labelled `var_t` (SELinux enforcing) | the data root is labelled `container_file_t` once; new files inherit it | `meridian-label.service` |
| match the egress rules' subnets when rootless | everything runs rootful, from systemd | all units |

The `docker-compose` binary is used, not `podman-compose`, because the split overlay uses
`!override`. Measured on podman 5.8 with compose 5.6, rootless: Postgres started healthy once
its directory was labelled, `migrate` waited on it and ran, the API answered `/health`
through the web container, `backup.sh` dumped, and an `internal` network had no route out.

## The stack on Fedora CoreOS

### 1. Install

Put your SSH public key in `deploy/podman/meridian.bu`, then compile it on any machine with
podman:

```bash
podman run --rm -i -v "$PWD/deploy:/deploy:ro,z" quay.io/coreos/butane:release \
  --pretty --strict --files-dir /deploy < deploy/podman/meridian.bu > meridian.ign
```

Boot the CoreOS live ISO on the mini PC, and install to its disk with that file:

```bash
sudo coreos-installer install /dev/nvme0n1 --ignition-file meridian.ign   # check the disk name
```

After the reboot, `ssh core@<mini pc>`. The units are installed and enabled; `meridian.service`
fails until the checkout and `.env` exist, which is expected.

### 2. The checkout and `.env`

CoreOS has no `git`; run it in a container. `/srv` is `/var/srv` here, and it is already
labelled for containers:

```bash
sudo podman run --rm -v /var/srv/meridian:/w:z docker.io/alpine/git:latest \
  clone https://github.com/<you>/Meridian.git /w/app
cd /srv/meridian/app
sudo cp .env.example .env && sudo chmod 600 .env
```

Fill `.env` as in deployment.md §2, and add:

```bash
COMPOSE_FILE=docker-compose.yml:deploy/split/remote-embedder.yml:deploy/podman/podman.yml
COMPOSE_PROFILES=autoupdate
DATA_ROOT=/srv/meridian
MERIDIAN_EMBEDDER_URL=http://<gpu machine>:8100     # and the rest of deployment.md §3c
```

`COMPOSE_FILE` and `COMPOSE_PROFILES` are read from `.env` by compose itself, so every command
below and every unit uses the same files without naming them.

For private images, log in once as root: `sudo podman login ghcr.io` with a read-only token.

### 3. First boot

Every command runs as root against podman's socket:

```bash
export DOCKER_HOST=unix:///run/podman/podman.sock
alias dc='sudo -E /usr/local/bin/docker-compose'
dc up -d postgres
dc run --rm datadirs
dc run --rm tools alembic upgrade head
dc run --rm tools python scripts/seed.py
sudo systemctl restart meridian
```

No `modelfetch` here: the weights live on the GPU machine. Then the smoke run and the egress
check from deployment.md §4 and §4b. The egress rules are already loaded
(`meridian-egress.service`); add the GPU machine's two ports to `lan_allow` in
`/etc/nftables/meridian.nft` and `sudo systemctl restart meridian-egress`.

### 4. Sizing for 16 GB

The defaults fit. With the embedder on the other machine, the largest users are Postgres
(`PG_SHARED_BUFFERS=2GB`, `PG_EFFECTIVE_CACHE_SIZE=6GB`), the browser in `crawl4ai` (its pool
and 1 GB of shared memory) and the worker (capped at 4 GB). On a 4-core CPU, leave
`MERIDIAN_WORKER_CONCURRENCY` at 4 and watch `uptime` during the first day; lower it before
anything else if the load stays above the core count.

### <a id="updates"></a>5. Updates

`meridian-update.timer` runs hourly: `compose pull`, then `compose up -d`, which recreates only
what changed. `migrate` is in the `autoupdate` profile, so a new tools image migrates first. An
image that is built locally and in no registry fails its pull and is kept. `systemctl start
meridian-update` applies a promotion at once.

Backups: `deploy/podman/meridian-backup.service` with `deploy/meridian-backup.timer`. Mount the
backup disk at `/var/mnt/backup` first, then `sudo systemctl enable --now meridian-backup.timer`.

## <a id="the-gpu-machine-on-windows"></a>The GPU machine on Windows

Not yet run on the card; each step below has a check, and the order matters.

1. **The NVIDIA driver** for Windows (it serves WSL too), and **Podman Desktop** with a podman
   machine and its compose extension.
2. **The GPU inside the podman machine**, by CDI:

   ```powershell
   podman machine ssh
   sudo curl -s -L -o /etc/yum.repos.d/nvidia-container-toolkit.repo `
     https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo
   sudo dnf install -y nvidia-container-toolkit
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   exit
   podman run --rm --device nvidia.com/gpu=all docker.io/library/ubuntu nvidia-smi -L   # lists the 3090
   ```

   Compose passes the same name through podman's Docker API (`podman-windows.yml`); without
   the CDI spec the error is `unresolvable CDI devices nvidia.com/gpu=all`.
3. **The node's files**: copy `deploy/embedder-node/` to the machine, `.env` from
   `env.example`, and add `COMPOSE_FILE=docker-compose.yml;gpu.yml;podman-windows.yml` (`;` on
   Windows). Leave `EMBEDDER_BIND=0.0.0.0`: the address is inside the podman machine, where the
   Windows LAN address does not exist.
4. **The weights**, once, into the named volume: `podman compose --profile tools run --rm
   modelfetch`. The language model's GGUF is fetched by llama.cpp on its first start (`LLM_HF`).
5. **Start at log on**: Task Scheduler → *At log on* → `powershell.exe -NoProfile
   -ExecutionPolicy Bypass -File <folder>\start-windows.ps1`. It starts the podman machine,
   pulls, and runs `compose --profile llm up -d` (`-NoLlm` for the embedder alone). Sleep keeps
   the containers; a reboot needs the script.
6. **Reachable from the LAN.** Published ports reach Windows' own `localhost`; the LAN needs a
   forward and a firewall rule, from an elevated PowerShell:

   ```powershell
   foreach ($p in 8100, 8080) {
     netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=127.0.0.1
   }
   New-NetFirewallRule -DisplayName "Meridian GPU node" -Direction Inbound -Protocol TCP `
     -LocalPort 8100,8080 -RemoteAddress <mini pc address> -Action Allow
   ```

   Check from the mini PC: `curl http://<gpu machine>:8100/health` and `:8080/health`. WSL's
   mirrored networking is the alternative to the port proxy on Windows 11.

Then `GET /api/admin/status` on the stack shows both as up.
