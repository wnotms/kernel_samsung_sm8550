#!/usr/bin/env python3
"""Prepare Actions config fragments, check Kconfig results, and verify Image."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = Path(__file__).with_name("reference-zimage.json")
ASSIGNMENT = re.compile(r'CONFIG_([A-Za-z0-9_]+)=(y|m|n|0x[0-9a-fA-F]+|-?[0-9]+|"[^"\n\\]*")')
DISABLED = re.compile(r'# CONFIG_([A-Za-z0-9_]+) is not set')
PROTECTED = {"LOCALVERSION", "LOCALVERSION_AUTO", "SMP", "PREEMPT", "PREEMPT_NONE", "PREEMPT_VOLUNTARY", "PREEMPT_RT", "LTO_NONE", "LTO_CLANG", "LTO_CLANG_FULL", "LTO_CLANG_THIN", "CFI_CLANG", "KSU", "KSU_SUSFS", "ZEROMOUNT"}


def parse(text):
    result = {}
    for line in text.splitlines():
        match = ASSIGNMENT.fullmatch(line.strip())
        disabled = DISABLED.fullmatch(line.strip())
        if match:
            result[match[1]] = match[2]
        elif disabled:
            result[disabled[1]] = "n"
        elif line.strip() and not line.lstrip().startswith("#"):
            raise ValueError(f"Invalid config line: {line}")
    return result


def flag(name):
    value = os.environ.get(name, "false")
    if value not in ("true", "false"):
        raise ValueError(f"Invalid boolean {name}: {value}")
    return value == "true"


def prepare(destination):
    mode = os.environ["BUILD_MODE"]
    if mode not in ("lkm", "resukisu"):
        raise ValueError("Unknown build mode")
    config = {}

    def switch(names, enabled):
        for name in names.split():
            config[name] = "y" if enabled else "n"

    droid = flag("INPUT_DROIDSPACES")
    switch("SYSVIPC POSIX_MQUEUE IPC_NS PID_NS DEVTMPFS", droid)
    if droid:
        switch("NAMESPACES UTS_NS NET_NS TMPFS TMPFS_XATTR TMPFS_POSIX_ACL CGROUPS NETFILTER NETFILTER_ADVANCED NETFILTER_XTABLES NETFILTER_XT_MATCH_ADDRTYPE NETFILTER_XT_MATCH_RECENT", True)
    switch("NTSYNC", flag("INPUT_NTSYNC"))
    bbr = flag("INPUT_BBR")
    switch("TCP_CONG_BBR DEFAULT_BBR", bbr)
    switch("TCP_CONG_CUBIC", True)
    switch("DEFAULT_CUBIC", not bbr)
    if bbr:
        switch("TCP_CONG_ADVANCED NET_SCH_FQ", True)
    config["DEFAULT_TCP_CONG"] = '"bbr"' if bbr else '"cubic"'
    lz4 = flag("INPUT_ZRAM_LZ4")
    switch("ZRAM", True)
    switch("ZRAM_DEF_COMP_LZ4", lz4)
    switch("ZRAM_DEF_COMP_LZORLE", not lz4)
    switch("ZRAM_DEF_COMP_LZO ZRAM_DEF_COMP_ZSTD", False)
    config["ZRAM_DEF_COMP"] = '"lz4"' if lz4 else '"lzo-rle"'
    if lz4:
        switch("CRYPTO_LZ4", True)
    susfs = mode == "resukisu" and flag("INPUT_SUSFS")
    zeromount = susfs and flag("INPUT_ZEROMOUNT")
    switch("KSU", mode == "resukisu")
    switch("KSU_SUSFS", susfs)
    switch("ZEROMOUNT", zeromount)
    switch("REKERNEL", flag("INPUT_REKERNEL"))
    switch("REKERNEL_LEGACY_NETLINK", False)
    extra = parse(os.environ.get("EXTRA_CONFIG", "").replace(";", "\n"))
    if PROTECTED & extra.keys():
        raise ValueError(f"Use the workflow controls for protected options: {sorted(PROTECTED & extra.keys())}")
    config.update(extra)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text("".join(f"CONFIG_{key}={value}\n" for key, value in sorted(config.items())))
    metadata = json.loads(REFERENCE.read_text())
    environment = {
        "KERNEL_RELEASE_OVERRIDE": metadata["release"],
        "KBUILD_BUILD_USER": metadata["build_user"],
        "KBUILD_BUILD_HOST": metadata["build_host"],
        "KBUILD_BUILD_VERSION": metadata["build_version"],
        "KBUILD_BUILD_TIMESTAMP": metadata["build_timestamp"],
        "CONFIG_FRAGMENT_FILE": str(Path(destination).resolve()),
        "APPLY_DROIDSPACES": str(int(droid)),
        "APPLY_NTSYNC": str(int(flag("INPUT_NTSYNC"))),
        "APPLY_BBR": str(int(bbr)),
        "APPLY_ZRAM": str(int(lz4)),
        "APPLY_SUSFS": str(int(susfs)),
        "APPLY_ZEROMOUNT": str(int(zeromount)),
        "ENABLE_REKERNEL": str(int(flag("INPUT_REKERNEL"))),
    }
    with open(os.environ["GITHUB_ENV"], "a") as output:
        for key, value in environment.items():
            output.write(f"{key}={value}\n")
    print(Path(destination).read_text())
    if mode == "lkm" or not susfs:
        print("SUSFS/ZeroMount require built-in KSU and SUSFS; unavailable options are forced off.")


def apply(config_path, fragment):
    for key, value in parse(Path(fragment).read_text()).items():
        args = [str(ROOT / "scripts/config"), "--file", config_path]
        if value in ("y", "m", "n"):
            args += [{"y": "--enable", "m": "--module", "n": "--disable"}[value], key]
        elif value.startswith('"'):
            args += ["--set-str", key, value[1:-1]]
        else:
            args += ["--set-val", key, value]
        subprocess.run(args, check=True)


def verify(config_path, fragment):
    actual = parse(Path(config_path).read_text())
    mismatches = []
    for key, value in parse(Path(fragment).read_text()).items():
        # Disabled dependency-gated symbols may be absent from .config entirely.
        if actual.get(key, "n") != value:
            mismatches.append(f"CONFIG_{key}: requested {value}, resolved {actual.get(key, 'n/absent')}")
    if mismatches:
        raise ValueError("Kconfig did not accept requested options:\n" + "\n".join(mismatches))


def verify_image(out_dir):
    out = Path(out_dir)
    metadata = json.loads(REFERENCE.read_text())
    release = (out / "include/config/kernel.release").read_text().strip()
    if release != metadata["release"]:
        raise ValueError(f"Release mismatch: {release}")
    image = (out / "arch/arm64/boot/Image").read_bytes()
    banners = [m.decode() for m in re.findall(rb'Linux version [^\x00\n]+', image)]
    if metadata["banner"] not in banners:
        raise ValueError(f"Image banner differs from reference: {banners}")
    manifest = dict(metadata, actual_source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
    manifest["note"] = "Displayed release is copied from the reference Image; actual LTS sources are not downgraded and vendor module ABI compatibility is not guaranteed."
    (out / "build-metadata.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(metadata["banner"])


if __name__ == "__main__":
    commands = {"prepare": prepare, "apply": apply, "verify": verify, "verify-image": verify_image}
    commands[sys.argv[1]](*sys.argv[2:])
