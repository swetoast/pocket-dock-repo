# Pocket Dock Repository

This repository contains the application catalogue and source packages used by **Pocket Dock**, an application browser and installer for the Anbernic RG40XX V official Linux firmware.

Applications are maintained together in one monorepo while keeping independent metadata, versions, icons and packages.

Pocket Dock reads `catalogue/catalogue.json` to display available applications and retrieve the corresponding package.

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

Each application has its own directory under:

```text
apps/<app-id>/
```

The directory contains:

```text
release.json
source/
```

The `source/` directory contains the application launcher and matching application folder:

```text
source/
├── <Launcher>.sh
└── <Application>/
    ├── app.json
    ├── main.py
    └── ...
```

Each application is self-contained and versioned independently.

## Application metadata

Each application uses two metadata files.

### `release.json`

Location:

```text
apps/<app-id>/release.json
```

This file describes the application in the repository and catalogue.

It contains:

- Application ID
- Display name
- Version
- Summary
- Category
- Search keywords
- Icon path
- Launcher name
- Application directory
- Entry point
- Capabilities
- Package identity

### `app.json`

Location:

```text
apps/<app-id>/source/<Application>/app.json
```

This file is included with the application package and provides the information Pocket Dock validates before installation.

It contains:

- Application ID
- Display name
- Version
- Optional author
- Summary
- Launcher name
- Application directory
- Entry point
- Capabilities

The application ID, version, launcher and application directory stay consistent across:

```text
release.json
app.json
catalogue/catalogue.json
```

## Catalogue

The Pocket Dock catalogue is stored at:

```text
catalogue/catalogue.json
```

The catalogue contains the information displayed by Pocket Dock:

- Application ID
- Name
- Version
- Summary
- Category
- Search keywords
- Icon
- Package location
- Publication state

The catalogue is available from:

```text
https://raw.githubusercontent.com/swetoast/pocket-dock-repo/main/catalogue/catalogue.json
```

Applications under preparation use:

```json
"published": false
```

Pocket Dock displays entries where:

```json
"published": true
```

## Icons

Application icons are stored under:

```text
catalogue/icons/
```

The icon filename matches the application ID:

```text
catalogue/icons/<app-id>.png
```

Icons use transparent PNG artwork designed to remain clear at Pocket Dock's list-icon size.

Catalogue icon paths are relative to `catalogue/catalogue.json`:

```json
"icon": "icons/<app-id>.png"
```

## Application packages

Pocket Dock installs application packages containing one launcher and its matching application directory:

```text
<Launcher>.sh
<Application>/
├── app.json
├── main.py
└── ...
```

Example:

```text
Example_App.sh
Example_App/
├── app.json
├── main.py
├── assets/
└── ...
```

Pocket Dock validates the catalogue entry, package metadata and package structure before installing the application under:

```text
/mnt/mmc/Roms/APPS
```

## Independent versions

Every application has its own version.

Updating one application does not require changing the versions of other applications in the monorepo.

An application version is recorded consistently in:

```text
apps/<app-id>/release.json
apps/<app-id>/source/<Application>/app.json
catalogue/catalogue.json
```

## Capabilities

Application metadata can describe the device features used by an application.

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

Pocket Dock displays these capabilities on the application details page.

## Repository content

Application source includes the files required to build and package each application.

Runtime data is created or downloaded by the application when required. This keeps the repository focused on source, metadata and packaged assets.

The repository includes:

```text
Application source
Launchers
Application manifests
Release metadata
Catalogue metadata
Catalogue icons
Packaged application assets
Tests belonging to each application
```

## Current catalogue

The catalogue currently contains:

```text
Diagnostics
RetroScrape
```

Both applications use the same repository structure and remain independently versioned.
