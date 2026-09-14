# Pocket Dock Repository

This repository contains the application catalogue and source packages used by Pocket Dock, an application browser and installer for the Anbernic RG40XX V official Linux firmware.

Applications are maintained in one monorepo while keeping independent metadata, versions, icons and source packages. Pocket Dock reads `catalogue/catalogue.json` to display published applications and locate their packages.

## Repository structure

```text
pocket-dock-repo/
├── apps/
│   └── <app-id>/
│       ├── release.json
│       └── source/
│           ├── <Launcher>.sh
│           └── <Application>/
│               ├── app.json
│               ├── main.py
│               └── ...
├── catalogue/
│   ├── catalogue.json
│   └── icons/
│       └── <app-id>.png
└── README.md
```

## Applications

Each application has an independent directory under `apps/<app-id>/` containing repository metadata and the complete installable source layout.

The `source/` directory contains exactly one launcher and its matching application directory:

```text
source/
├── <Launcher>.sh
└── <Application>/
    ├── app.json
    ├── main.py
    └── ...
```

## Metadata

Repository metadata is stored in `apps/<app-id>/release.json`. Package metadata is stored in `apps/<app-id>/source/<Application>/app.json`.

The following values must match across `release.json`, `app.json` and `catalogue/catalogue.json`:

- Application ID
- Display name
- Version
- Launcher
- Application directory
- Entry point
- Capabilities

## Catalogue

The catalogue is stored at `catalogue/catalogue.json`. Pocket Dock displays entries where `published` is `true`.

Catalogue icon paths are relative to the `catalogue/` directory:

```json
"icon": "icons/<app-id>.png"
```

Package paths are repository-relative and point to the application's `source/` directory.

## Icons

Application icons use transparent PNG artwork and are stored under `catalogue/icons/`. The filename matches the application ID.

## Installation layout

Pocket Dock installs the launcher and matching application directory under:

```text
/mnt/mmc/Roms/APPS
```

A valid application package has this root layout:

```text
<Launcher>.sh
<Application>/
├── app.json
├── main.py
└── ...
```

## Independent versions

Each application is versioned independently. Updating one application does not change the versions of other applications in the monorepo.

## Capabilities

Supported capability labels include:

```text
network
bluetooth
audio
input
system-information
persistent-data
external-process
```

## Current applications

### Pocket Terminal

Pocket Terminal is the first application in the repository.

- Application ID: `pocket-terminal`
- Version: `0.0.27`
- Category: `Utilities`
- Publication state: published
- Source: `apps/pocket-terminal/source/`
