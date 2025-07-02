# podded - One script for Podman build, run & systemd

[![license: mit](https://img.shields.io/badge/license-MIT-purple)](https://github.com/icezyclon/podded?tab=License-1-ov-file#readme)
[![stars](https://img.shields.io/github/stars/icezyclon/podded?style=social)](https://github.com/icezyclon/podded)

**TL;DR:**

One script per container config - build, run, and deploy it the same way, every time.

**What is `podded`?**

`podded` saves your [podman](https://podman.io/) build instructions, run commands and systemd (quadlet) service files - all in a single script!

**Why?**

For simple container setups, Podman users typically need to manage three separate things:
* An (interactive) `podman run` **command** to test and tweak your container
* A **Containerfile** (or Dockerfile) to build your image
* A **quadlet file** to generate a `systemd` service for automatic deployment at boot

Usually these live in **different files**:
* A bash script for your `run command`
* A `Containerfile` for the build
* A `.container` quadlet file for systemd

Maintaining all three can be tedious - and mistakes creep in easily because the syntax and options are different for each.

**How does `podded` help?**

`podded` solves this inconvenience by storing/combining **all three** in a **single script**:
* Stores the **Containerfile** *inside* the script
* Saves and reuses your `podman run` command after the first use
* Automatically [generates](https://github.com/containers/podlet) and manages **quadlet files** for systemd

And since each `podded` script is just a plain Python file, you can easily tweak or extend it by hand whenever you need.

**One file per config**

With `podded`, you end up with one file per configuration (e.g., `webserver.dev` and `webserver.prod`).
The **script’s filename** defines the **container’s name and build tag** - so using the same name means only **one container instance** of that type will run at a time (which is perfect when they share ports or volumes).


## Usage

Imagine you have the following situation:
You have an nginx webserver that can be deployed with multiple configurations: development and production.

With `podded`, you keep everything in **one script per configuration**, instead of juggling separate `bash` scripts, `Containerfiles`, and quadlet files.

**Example project structure:**
```
project/
├── containers/
│   ├── webserver.dev[.py]  # this is a renamed copy of podded.py
│   ├── webserver.prod[.py]  # this is a renamed copy of podded.py
│   ├── Containerfile   # Shared between both
├── www/          # Shared web content (mounted as a volume)
└── ...
```

**Step 1: Save your build instructions (Optional)**

> The entire process of providing and building a Containerfile is only necessary if you don't use a base image.
> In that case, just continue with Step 2, providing the base image name instead of a custom one.

Write a normal `Containerfile` or `Dockerfile` once (e.g. `containers/Containerfile`).
Then save it into your `podded` script and build the image:

```bash
./containers/webserver.dev build containers/Containerfile
```

This does two things:
* It **saves** the content of the `Containerfile` *inside* of `webserver.dev`  no need to keep the separate file afterwards.
* Builds the container image using `podman build ...`

> You can also use `podded edit build` to write/modify your `Containerfile` with your editor!

By default, the image name is automatically derived from the script filename:

`webserver.dev.py` → image name: `webserver`

So, `webserver.prod.py` will build the same base image, but you can manage different run configs!


After saving it once, you won't need the Containerfile as a separate file anymore:
```bash
./containers/webserver.dev build
```

**Step 2: Run (interactively) with command the first time**

```bash
./containers/webserver.dev run-it -p 8080:80 -v ./www:/usr/share/nginx/html webserver:latest
```
You can pass any `podman run` command you want - `podded` records it for later!
> Use *absolute paths* or use *relative paths from the podded file*! Relative paths will be transformed to absolute ones for systemd (to be relative to the podded file)

> You can also use `podded edit command` to write/modify your `command` with your editor!

To run again there is no need to type the full command!
```bash
# run in interactive or detached mode
./containers/webserver.dev run[-it]
```
This reuses the saved `podman run` command.


**Step 3: Enable as a systemd service**

```bash
./containers/webserver.dev enable
```
This generates a quadlet file inside `~/.config/containers/systemd/`, enables it, and starts the systemd service immediately.

```bash
./containers/webserver.dev disable
```
This stops the service and removes the generated quadlet file again.

```bash
./containers/webserver.dev status
```
This checks the status of the running service.

```bash
./containers/webserver.dev quadlet
```
Or just generate the file and do with it as you like.

> Note that the global variable `QUADLET_TEMPLATE` will be merged into the generated file! By default this includes options such as `restart=always` and `OnStartupSec=60` - all of which can be configured or turned off in the file if not desirable!

**Step 4: Lock changes**

```bash
./containers/webserver.dev lock
```
This will lock the file to prevent accidentally changing the saved run command or Containerfile again.


## Installation

You can use `podded` in **two ways**:

* **Globally**: Install it once into a folder on your `$PATH` (e.g., `~/.local/bin`).
* **Per project**: Download it directly where you need it, renaming it for your container/script name.

**Global install**:
```bash
# Download to ~/.local/bin and make it executable
curl -o ~/.local/bin/podded https://raw.githubusercontent.com/icezyclon/podded/main/podded.py
chmod +x ~/.local/bin/podded
# lock the global script to prevent accidental changes
podded lock force # (or leave open to use `podded update` to update to newer versions)

# you can then create new scripts whenever you need them
podded new PATH/NAME
```

**Download for project**:
```bash
# Download and rename directly
curl -o containers/webserver.dev https://raw.githubusercontent.com/icezyclon/podded/main/podded.py
chmod +x containers/webserver.dev
```
Now you can edit the file in place to store your Containerfile, run command, and quadlet config.

Then you can copy the file like so:
```bash
./containers/webserver.dev copy containers/webserver.prod
# and modify containers/webserver.prod as required
```


## License

[MIT](LICENSE)
