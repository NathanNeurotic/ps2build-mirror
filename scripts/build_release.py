#!/usr/bin/env python3
"""Build one mirror release of the ps2build SDK from the official server.

Installs the newest official suite with ps2build itself (so the tree is exactly what "ps2build update"
produces), packs it, downloads the extra port packages and the GPL/LGPL corresponding source, and writes
SHA256SUMS plus the pin snippet consumers copy. Nothing is modified: the archives are the installed
tree byte for byte, and extra packages and sources are the upstream files as served.

Usage: build_release.py OUT_DIR [--work DIR]
Writes OUT_DIR/<assets>, OUT_DIR/release.json (tag, versions) and OUT_DIR/notes.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = "https://git.techwritescode.dev"
API = HOST + "/api/v1/repos/ps2"
EXTRAS = ("ogg", "vorbis")          # world packages ps2build does not install; RiptOPL needs them
TOOLCHAINS = ("gcc", "llvm")
# Corresponding source for the GPL/LGPL parts of the binaries: which config.sh in which repo names
# which upstream repositories. LLVM itself is Apache-2.0 and is not mirrored (its commit is recorded).
SOURCE_CONFIGS = {
    "toolchain": ("ee/config.sh", "iop/config.sh", "dvp/config.sh"),
    "llvm-toolchain": ("config.sh",),
}
SKIP_SOURCE = {"llvm"}


def download(url, path=None):
    request = urllib.request.Request(url, headers={"User-Agent": "ps2build-mirror/1.0"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                if path is None:
                    return response.read()
                with open(path, "wb") as out:
                    shutil.copyfileobj(response, out, 1 << 20)
                return path
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code not in (408, 429):
                raise
            if attempt == 5:
                raise
            print(f"retrying {url}: {error}", flush=True)
            time.sleep(5 << attempt)


def api(path):
    return json.loads(download(f"{API}/{path}"))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*args, timeout=None, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(args, check=True, timeout=timeout, **kwargs)


def install_sdk(sdk):
    release = api("ps2build/releases/latest")
    tag = release["tag_name"]
    name = f"ps2build-{tag}-linux-amd64"
    assets = [a for a in release["assets"] if a["name"] == name]
    if len(assets) != 1:
        raise SystemExit(f"ps2build {tag} has no unique {name}")
    if sdk.exists():
        shutil.rmtree(sdk)
    sdk.mkdir(parents=True)
    download(assets[0]["browser_download_url"], sdk / "ps2build")
    (sdk / "ps2build").chmod(0o755)
    for attempt in range(3):
        try:
            # ps2build has no download timeout of its own; a stalled transfer would hang the job.
            run(sdk / "ps2build", "update", timeout=1800, env={**os.environ, "PS2DEV_INSTALL_DIR": str(sdk)})
            run(sdk / "ps2build", "update", "llvm-toolchain", timeout=1800,
                env={**os.environ, "PS2DEV_INSTALL_DIR": str(sdk)})
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(60)
    shutil.rmtree(sdk / ".update-staging", ignore_errors=True)
    versions = {}
    in_components = False
    for line in (sdk / "versions.yaml").read_text().splitlines():
        if line.startswith("components:"):
            in_components = True
        elif in_components and (m := re.match(r"^ +([\w.-]+): (\S+)$", line)):
            versions[m.group(1)] = m.group(2)
        elif not line.startswith(" "):
            in_components = False
    if versions.get("ps2build") != tag or "llvm-toolchain" not in versions:
        raise SystemExit(f"unexpected versions.yaml: {versions}")
    return versions


def pack(sdk, out, name, members):
    """Deterministic tar (sorted, fixed owner and mtime) compressed with zstd."""
    archive = out / name
    tar = subprocess.Popen(["tar", "-C", str(sdk), "--sort=name", "--owner=0", "--group=0", "--numeric-owner",
                            "--mtime=@0", "-cf", "-", *members], stdout=subprocess.PIPE)
    with open(archive, "wb") as f:
        subprocess.run(["zstd", "-19", "-T0", "-q", "-c"], stdin=tar.stdout, stdout=f, check=True)
    if tar.wait() != 0:
        raise SystemExit(f"tar failed for {name}")
    return archive


def manifest(sdk, out, name, members):
    """Per-file SHA-256 of the installed tree, so any install can be compared with the mirror."""
    lines = []
    for member in members:
        root = sdk / member
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())
        for path in files:
            lines.append(f"{sha256(path)}  {path.relative_to(sdk).as_posix()}")
    (out / name).write_text("\n".join(sorted(lines, key=lambda l: l[66:])) + "\n")


def extras(out):
    versions = {}
    for package in EXTRAS:
        release = api(f"{package}/releases/latest")
        tag = release["tag_name"]
        versions[package] = tag
        for toolchain in TOOLCHAINS:
            name = f"{package}-{tag}-{toolchain}.zip"
            assets = [a for a in release["assets"] if a["name"] == name]
            if len(assets) != 1:
                raise SystemExit(f"{package} {tag} has no unique {name}")
            download(assets[0]["browser_download_url"], out / name)
    return versions


def resolve(repo, ref):
    commits = api(f"{repo}/commits?sha={urllib.parse.quote(ref, safe='')}&limit=1&stat=false&verification=false&files=false")
    return commits[0]["sha"]


def sources(out, versions):
    rows = []
    wanted = {"toolchain": versions["toolchain"], "llvm-toolchain": versions["llvm-toolchain"]}
    pairs = {}
    for repo, tag in wanted.items():
        pairs[(repo, tag)] = repo
        for config in SOURCE_CONFIGS[repo]:
            text = download(f"{API}/{repo}/raw/{config}?ref={urllib.parse.quote(tag, safe='')}").decode()
            urls = dict(re.findall(r'^(\w+)_URL="([^"]+)"', text, re.M))
            refs = dict(re.findall(r'^(\w+)_REF="([^"]+)"', text, re.M))
            for key, url in urls.items():
                name = url.rstrip("/").removesuffix(".git").rsplit("/", 1)[1]
                pairs[(name, refs[key])] = f"{repo}/{config}"
    for (name, ref), origin in sorted(pairs.items()):
        commit = resolve(name, ref)
        is_tag_like = ref != commit and not re.fullmatch(r"(master|main|platform_agnostic|r5900-ee)", ref)
        if name in SKIP_SOURCE:
            rows.append((name, ref, commit, origin, "not mirrored (Apache-2.0 with LLVM exception)"))
            continue
        archive = f"src-{name}-{re.sub(r'[^A-Za-z0-9.]+', '_', ref)}-{commit[:12]}.tar.gz"
        download(f"{HOST}/ps2/{name}/archive/{commit}.tar.gz", out / archive)
        note = archive if is_tag_like else f"{archive} (branch ref, resolved when this release was built)"
        rows.append((name, ref, commit, origin, note))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--work", type=Path, default=Path("work"))
    args = parser.parse_args()
    out, sdk = args.out.resolve(), (args.work / "sdk").resolve()
    out.mkdir(parents=True, exist_ok=True)

    versions = install_sdk(sdk)
    extra_versions = extras(out)
    everything = {**versions, **{f"extra:{k}": v for k, v in extra_versions.items()}}
    digest = hashlib.sha256(json.dumps(everything, sort_keys=True).encode()).hexdigest()[:8]
    tag = f"sdk-{versions['ps2build']}-{digest}"

    base_members = sorted(p.name for p in sdk.iterdir() if p.name != "llvm-toolchain")
    stem = f"ps2build-{tag}-linux-amd64"
    pack(sdk, out, f"{stem}-base.tar.zst", base_members)
    pack(sdk, out, f"{stem}-llvm.tar.zst", ["llvm-toolchain"])
    manifest(sdk, out, f"{stem}-base.files.sha256", base_members)
    manifest(sdk, out, f"{stem}-llvm.files.sha256", ["llvm-toolchain"])
    shutil.copy(sdk / "versions.yaml", out / "versions.yaml")
    source_rows = sources(out, versions)

    table = "\n".join(f"| {n} | `{r}` | `{c[:12]}` | {o} | {a} |" for n, r, c, o, a in source_rows)
    (out / "SOURCES.md").write_text(
        "# Corresponding source\n\nThe toolchains in this release are built by techwritescode's build scripts "
        "from these repositories. GPL and LGPL sources are attached to this release.\n\n"
        "| repository | ref | commit | named by | archive |\n|---|---|---|---|---|\n" + table + "\n")

    sums = sorted(p.name for p in out.iterdir() if p.is_file() and p.name not in ("SHA256SUMS", "release.json", "notes.md"))
    (out / "SHA256SUMS").write_text("".join(f"{sha256(out / n)}  {n}\n" for n in sums))
    sums_sha = sha256(out / "SHA256SUMS")

    components = " ".join(f"{k}={v}" for k, v in versions.items() if k != "llvm-toolchain")
    pin = (f"PS2BUILD_SDK_MIRROR={os.environ.get('GITHUB_REPOSITORY', 'NathanNeurotic/ps2build-mirror')}\n"
           f"PS2BUILD_SDK_TAG={tag}\nPS2BUILD_SDK_SUMS_SHA256={sums_sha}\n"
           f'PS2BUILD_COMPONENTS="{components}"\nPS2BUILD_LLVM_TOOLCHAIN={versions["llvm-toolchain"]}\n'
           + "".join(f"{k.upper()}_VERSION={v}\n" for k, v in extra_versions.items()))
    (out / "pin.env").write_text(pin)
    sizes = "\n".join(f"| {n} | {(out / n).stat().st_size / 1e6:.1f} MB |" for n in sums)
    (out / "notes.md").write_text(
        f"Unmodified ps2build SDK suite, mirrored from {HOST}/ps2 so CI does not depend on that server being up.\n\n"
        "All binaries were built and published by **techwritescode** (ps2build, toolchain, SDK packages). "
        "The archives are the tree `ps2build update` installs, byte for byte; licenses are inside them "
        "(`licenses/`, `toolchain/LICENSES/`, `llvm-toolchain/LICENSES/`). See SOURCES.md for the corresponding source.\n\n"
        "## Versions\n\n| component | version |\n|---|---|\n"
        + "\n".join(f"| {k} | {v} |" for k, v in everything.items())
        + f"\n\n## Pin\n\nCopy into the consuming repository's `.github/ps2build-sdk.env`:\n\n```sh\n{pin}```\n\n"
        f"## Assets\n\n| file | size |\n|---|---|\n{sizes}\n")
    (out / "release.json").write_text(json.dumps({"tag": tag, "versions": everything, "sums_sha256": sums_sha}, indent=2))
    print(f"tag={tag}\nsums_sha256={sums_sha}")


if __name__ == "__main__":
    main()
