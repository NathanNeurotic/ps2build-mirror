# ps2build-mirror

A checksummed mirror of **techwritescode's ps2build PS2 SDK** for CI, so builds in
[RiptOPL](https://github.com/NathanNeurotic/RiptOPL), [Launchstation](https://github.com/NathanNeurotic/Launchstation)
and the other NathanNeurotic projects keep working when `git.techwritescode.dev` is down (HTTP 521,
downloads cut off partway, connection resets).

Everything here is built and published by **techwritescode** at <https://git.techwritescode.dev/ps2>; this
repository only re-hosts it. Use the official installer for normal development:
<https://git.techwritescode.dev/ps2/install>.

## What a release contains

Each release `sdk-<ps2build version>-<hash>` is one exact suite, named by a hash of every component version:

| asset | contents |
|---|---|
| `ps2build-sdk-…-linux-amd64-base.tar.zst` | the tree `ps2build update` installs: ps2build, the GCC toolchain, tools, vcl, ninja, cmake, sdk-core and sdk-world (gcc and llvm package sets), licenses |
| `ps2build-sdk-…-linux-amd64-llvm.tar.zst` | `llvm-toolchain/` from `ps2build update llvm-toolchain` |
| `*.files.sha256` | SHA-256 of every file in each archive, to compare any install with the mirror |
| `ogg-*.zip`, `vorbis-*.zip` | the official ogg/vorbis world packages, byte for byte (ps2build does not install them) |
| `src-*.tar.gz`, `SOURCES.md` | corresponding source for the GPL/LGPL parts (see below) |
| `versions.yaml` | the suite's component versions |
| `pin.env` | the suite's exact versions and `SHA256SUMS` hash; consumers read the newest release's copy as their fallback |
| `SHA256SUMS` | SHA-256 of every asset; consumers pin the hash of this file |

The archives are not rebuilt or patched. A fix that the SDK needs (for example the LLVM libjpeg symbol tables)
belongs upstream or in the consuming repository, never in the mirror.

## Using it

Copy [`consumer/ps2build_sdk.sh`](consumer/ps2build_sdk.sh) into the repository and add `.github/ps2build-sdk.env`
naming the mirror and the components (bare names follow the newest release; `name=version` holds one back):

```sh
PS2BUILD_SDK_MIRROR=NathanNeurotic/ps2build-mirror
PS2BUILD_COMPONENTS="cmake ninja ps2build sdk-core sdk-world toolchain tools vcl"
PS2BUILD_LLVM_TOOLCHAIN=latest
```

In a workflow:

```yaml
- name: Resolve the latest ps2build SDK
  id: sdk
  run: sh tools/ps2build_sdk.sh --resolve --llvm "$RUNNER_TEMP/ps2build-sdk.env"
- name: Restore the ps2build SDK
  id: sdk-cache
  uses: actions/cache/restore@v4
  with:
    path: ${{ github.workspace }}/.ps2dev
    key: ps2build-sdk-${{ runner.os }}-llvm-${{ steps.sdk.outputs.key }}
    restore-keys: ps2build-sdk-${{ runner.os }}-llvm-
- name: Install the ps2build SDK
  run: sh tools/ps2build_sdk.sh --llvm "$GITHUB_WORKSPACE/.ps2dev"
- name: Cache the ps2build SDK
  if: ${{ steps.sdk-cache.outputs.cache-hit != 'true' && env.PS2BUILD_SDK_STALE != 'true' }}
  uses: actions/cache/save@v4
  with:
    path: ${{ github.workspace }}/.ps2dev
    key: ps2build-sdk-${{ runner.os }}-llvm-${{ steps.sdk.outputs.key }}
```

`--resolve` asks `git.techwritescode.dev`'s release API for the newest version of each component (or, when the
API is down, takes them from this mirror's newest release, with a warning) and keys the cache on them, so a new
upstream release is picked up by the next build. The install step then uses, in order: the cached SDK if it holds
those versions; an older cached SDK updated in place with `ps2build update <component> --version <tag>`, or a
fresh install, from the official server; and only when that fails (an HTTP error, a cut-off download, or the
attempt running past `PS2BUILD_SDK_OFFICIAL_BUDGET` seconds, default 900), this mirror's newest release, with
every file checked against its `SHA256SUMS`. If that release is older than the latest SDK, the job warns and
names the components that differ, and the workflow above does not cache it under the latest versions.
`PS2BUILD_SDK_NO_FALLBACK=1` makes it fail instead of using the mirror.

Following the newest release means an upstream release that breaks a build breaks it on the next run; hold that
component back with `name=version` in `.github/ps2build-sdk.env` until it is fixed.

## Keeping it current

[`refresh`](.github/workflows/refresh.yml) installs the newest official suite and, if its tag is not released
yet, verifies and publishes it, so the fallback stays close to the latest SDK.

- A **daily** run asks the release API first and builds only when the newest suite is not mirrored yet. A
  **weekly** run always builds, in case the API is down. Both publish unless the repository variable
  `AUTO_PUBLISH` is `false`.
- **Run it manually** (Actions → refresh → Run workflow) to publish at once.

Old releases are kept so older commits can still be rebuilt with an older SDK.

## Sizes

Suite v2026.10.15 (measured 2026-10-28): base 127 MB and LLVM 295 MB compressed, 1.7 GB installed. GitHub
allows release assets up to 2 GiB each; a release including sources is roughly 1 GB.

## Licenses and attribution

The binaries are redistributed unmodified under their own licenses, which ship inside the archives
(`licenses/`, `toolchain/LICENSES/`, `llvm-toolchain/LICENSES/`):

| component | license |
|---|---|
| ps2build, toolchain build scripts, tools | CC0-1.0 (techwritescode) |
| sdk-core, sdk-world (PS2SDK) | AFL-2.0, plus each world package's own license in `licenses/world/` |
| GCC, binutils (EE, IOP, DVP) | GPL-3.0-or-later, GCC Runtime Library Exception |
| newlib, libgloss | BSD-style, per-file (`COPYING.NEWLIB`) |
| pthread-embedded | LGPL-2.1 |
| LLVM, clang, lld, compiler-rt, libc++ | Apache-2.0 with LLVM Exceptions |
| ogg, vorbis | BSD-3-Clause |

Because GPL and LGPL binaries are conveyed here, each release also carries their **corresponding source**:
archives of the exact repositories and commits named by the toolchain's build configuration at the released
tag (`SOURCES.md` lists them, and flags any ref that is a branch rather than a tag). LLVM's source is not
mirrored because its license does not require it; its commit is recorded.

The scripts in this repository are CC0-1.0, matching ps2build.
