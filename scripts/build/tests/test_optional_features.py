#!/usr/bin/env python3
"""Offline checks: python3 -m unittest discover -s scripts/build/tests -v."""
import contextlib
import importlib.util
import io
import itertools
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ci_config", ROOT / "scripts/build/ci_config.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
FLAGS = "DROIDSPACES NTSYNC BBR ZRAM_LZ4 SUSFS ZEROMOUNT REKERNEL BASEBAND_GUARD UNICODE_FIX PTRACE_FIX NTFS3 FULL_TMPFS IPSET TTL_TARGET HIDE_IPV6_NAT".split()


class OptionalFeatures(unittest.TestCase):
    def test_gnu_host_tools_win_over_toolchain_applets(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            applets = temp / "applets"
            compiler = temp / "compiler"
            applets.mkdir()
            compiler.mkdir()
            for name in ("patch", "grep", "realpath"):
                executable = applets / name
                executable.write_text('#!/bin/sh\necho "unsupported host option" >&2\nexit 64\n')
                executable.chmod(0o755)
            clang = compiler / "clang"
            clang.write_text('#!/bin/sh\necho pinned-compiler\n')
            clang.chmod(0o755)
            command = '''source "$1"
CLANG_BIN="$2"
BUILDTOOLS_BIN="$3"
BUILDTOOLS_PATH_BIN="$3"
KERNEL_BUILD_TOOLS_BIN="$3"
export PATH="$3:$PATH"
if patch --version >/dev/null 2>&1; then exit 90; fi
setup_build_path
patch --version
printf '%s\\n' abc | grep -Po 'a.c'
realpath --relative-to="$4" "$4/child"
clang
'''
            result = subprocess.run(["bash", "-c", command, "test", str(ROOT / "scripts/build/common.sh"), str(compiler), str(applets), str(temp)], cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertIn("GNU patch", result.stdout)
            self.assertIn("abc\nchild\npinned-compiler", result.stdout)

    def test_feature_config_combinations(self):
        for values in itertools.product((False, True), repeat=5):
            bbg, ntfs, tmpfs, ipset, ttl = values
            config = ci.feature_options(*values)
            for key, value in (("BBG", bbg), ("NTFS3_FS", ntfs), ("NTFS3_LZX_XPRESS", ntfs), ("TMPFS_POSIX_ACL", tmpfs), ("TMPFS_INODE64", tmpfs), ("IP_SET", ipset), ("NETFILTER_XT_SET", ipset), ("NETFILTER_XT_TARGET_HL", ttl)):
                self.assertEqual(config[key], "y" if value else "n", (values, key))
            self.assertEqual("baseband_guard" in config["LSM"], bbg)
            self.assertEqual(config["TMPFS"], "y")
            self.assertEqual(config["TMPFS_XATTR"], "y")
            self.assertGreater(len([key for key in config if key.startswith("IP_SET_")]), 10)

    def test_actions_flags_and_config_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            for mode, enabled in itertools.product(("lkm", "resukisu"), (False, True)):
                env = {"BUILD_MODE": mode, "GITHUB_ENV": str(temp / "env"), "EXTRA_CONFIG": ""}
                env.update({"INPUT_" + name: str(enabled).lower() for name in FLAGS})
                with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                    ci.prepare(temp / "fragment")
                exported = dict(line.split("=", 1) for line in (temp / "env").read_text().splitlines())
                for name in ("APPLY_BBG", "APPLY_UNICODE_FIX", "APPLY_PTRACE_FIX", "APPLY_IPV6_NAT_FIX", "ENABLE_NTFS3", "ENABLE_FULL_TMPFS", "ENABLE_IPSET", "ENABLE_TTL_TARGET"):
                    self.assertEqual(exported[name], str(int(enabled)))
                config_path = temp / "config"
                config_path.write_text((ROOT / "arch/arm64/configs/gki_defconfig").read_text())
                ci.apply(str(config_path), str(temp / "fragment"))
                ci.verify(config_path, temp / "fragment")

    def test_patch_enable_disable_roundtrip(self):
        files = ("include/linux/ptrace.h", "include/linux/tracehook.h", "kernel/signal.c", "kernel/ptrace.c", "fs/unicode/utf8-norm.c")
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            originals = {}
            for name in files:
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                originals[name] = (ROOT / name).read_bytes()
                target.write_bytes(originals[name])
            script = ROOT / "scripts/build/apply_features.sh"
            command = 'source "$1"; PROJECT_ROOT="$2"; APPLY_PTRACE_FIX="$3"; APPLY_UNICODE_FIX="$3"; apply_ptrace_fix; apply_unicode_fix'
            for state in ("0", "1", "1", "0", "0"):
                subprocess.run(["bash", "-c", command, "test", str(script), str(temp), state], cwd=ROOT, check=True, capture_output=True)
                if state == "1":
                    for name in files:
                        self.assertNotEqual((temp / name).read_bytes(), originals[name], name)
                else:
                    for name in files:
                        self.assertEqual((temp / name).read_bytes(), originals[name], name)

    def test_config_report_uses_real_config_and_optional_hiding(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            config_path = temp / ".config"
            original = "CONFIG_IP6_NF_NAT=y\nCONFIG_IKCONFIG=y\nCONFIG_TMPFS=y\n"
            config_path.write_text(original)
            (temp / "kernel").mkdir()
            harness = temp / "Makefile"
            harness.write_text(f"obj := {temp / 'kernel'}\nKCONFIG_CONFIG := {config_path}\n"
                               "define filechk\n\t@set -e; $(filechk_$(1)) > $@\nendef\n"
                               f"include {ROOT / 'kernel/Makefile'}\n.PHONY: FORCE\nFORCE:\n")
            target = temp / "kernel/config_data"
            for state in ("0", "1", "0"):
                subprocess.run(["make", "-s", "-f", str(harness), str(target), "HIDE_IPV6_NAT=" + state], cwd=temp, check=True, capture_output=True)
                expected = original if state == "0" else original.replace("CONFIG_IP6_NF_NAT=y", "# CONFIG_IP6_NF_NAT is not set")
                self.assertEqual(target.read_text(), expected)
                self.assertEqual(config_path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
