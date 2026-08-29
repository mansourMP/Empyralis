"""A SKILL THAT NAMES A TOOL THE MACHINE DOES NOT HAVE IS A LIE.

`agent_job_skills.py` tells an agent to run `pdftotext -layout`, to reach for
duckdb above ~50 MB, to read .xlsx through pandas. Every one of those is a
promise about a machine this repo also provisions. The two files sit in
different languages, in different directories, and nothing at runtime connects
them — so a body that grows a dependency the installer does not install fails
only on a customer's box, at the moment somebody asked for something, with the
agent reporting a shell error it cannot explain.

The expected set and the actual set come from DIFFERENT PLACES, which is the
only arrangement that can catch anything: the expected set is parsed out of
the real skill bodies (what an agent will actually be told to run), the actual
set out of `install_data_toolchain`'s own source in the installer. Neither
file is asked to confirm itself, and neither is compared against a
hand-written list here that would need updating to stay true.

Both scans carry a canary. A scan that silently stops reaching real content
reports "passed" forever, which is the failure mode this repo has documented
for `preflight._check_rls` and every source-scanning test since.
"""

from __future__ import annotations

import os
import re
import unittest

from server_modules import agent_job_skills

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALLER_PATH = os.path.join(REPO_ROOT, "scripts", "install-agent-computer.sh")


def _installer_source() -> str:
    with open(INSTALLER_PATH, encoding="utf-8") as fh:
        return fh.read()


def _toolchain_function_source(src: str) -> str:
    """Just install_data_toolchain's body — not the whole installer.

    Scanning the whole file would pass on a mention anywhere in it, including
    inside a comment in an unrelated function, which is how a drift test comes
    to enforce nothing while looking strict.
    """
    start = src.index("install_data_toolchain() {")
    # The next top-level `name() {` at column 0 ends it.
    rest = src[start + len("install_data_toolchain() {"):]
    end = re.search(r"^\}$", rest, re.M)
    assert end is not None, "install_data_toolchain has no closing brace at column 0"
    return rest[: end.start()]


def _install_and_verify_halves(toolchain: str) -> tuple:
    """(what it INSTALLS, what it VERIFIES), split at the verification block.

    The first version of this test scanned the whole function body and PASSED
    a deliberate break — poppler-utils removed from the apt line — because
    `pdftotext` still appeared in the verification loop three lines down.
    That is the "check that looks strict and enforces nothing" trap, caught
    only by running the break. The two halves are now asserted separately:
    installing without verifying is a silent failure on the box, and verifying
    without installing is a guaranteed one.
    """
    marker = "# Verify what is actually there"
    assert marker in toolchain, "the verification block's own comment moved; this split is stale"
    head, tail = toolchain.split(marker, 1)
    # Comments are stripped from the INSTALL half, or a package named only in
    # the explanatory comment above the apt list would satisfy the check that
    # it is on the apt list.
    head_code = "\n".join(line for line in head.splitlines() if not line.strip().startswith("#"))
    return head_code, tail


# The ONE fact here that cannot be derived from either source, stated once
# with its reason: a Debian package name is not the name of the binary it
# ships, and no amount of reading either file reveals that `pdftotext` comes
# from `poppler-utils`. Every Python import CAN be derived (apt spells it
# `python3-<name>`, pip spells it `<name>`), so none of those are listed.
_BINARY_DEBIAN_PACKAGE = {"pdftotext": "poppler-utils"}


class ToolchainDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.installer = _installer_source()
        self.toolchain = _toolchain_function_source(self.installer)

    # ── canaries ────────────────────────────────────────────────────────
    def test_canary_the_installer_was_actually_read(self) -> None:
        self.assertIn("apt_install_system_deps", self.installer)
        self.assertIn("apt-get", self.toolchain)
        self.assertGreater(len(self.toolchain), 500, "the extracted function body is not a stub")

    def test_canary_the_skill_bodies_were_actually_read(self) -> None:
        bodies = self._all_bodies()
        self.assertGreater(len(bodies), 2000, "skill bodies were read and are real prose")

    # ── the contract ────────────────────────────────────────────────────
    def _all_bodies(self) -> str:
        return "\n".join(
            entry["body"] for entries in agent_job_skills.JOB_SKILLS.values() for entry in entries
        )

    def test_every_binary_a_skill_names_is_actually_installed(self) -> None:
        install, _verify = _install_and_verify_halves(self.toolchain)
        for binary in sorted(agent_job_skills.REQUIRED_BINARIES):
            package = _BINARY_DEBIAN_PACKAGE.get(binary, binary)
            with self.subTest(binary=binary, package=package):
                self.assertIn(
                    package,
                    install,
                    f"a skill body tells an agent to run {binary!r}, which comes from "
                    f"{package!r}, and install_data_toolchain does not install it",
                )

    def test_every_python_import_a_skill_names_is_actually_installed(self) -> None:
        install, _verify = _install_and_verify_halves(self.toolchain)
        for module in sorted(agent_job_skills.REQUIRED_PYTHON_IMPORTS):
            with self.subTest(module=module):
                self.assertTrue(
                    f"python3-{module}" in install or re.search(rf"\b{module}\b", install),
                    f"a skill body imports {module!r} and install_data_toolchain "
                    f"installs neither python3-{module} nor {module}",
                )

    def test_every_required_tool_is_verified_on_the_box_after_install(self) -> None:
        """Installing without checking is how a box comes up reporting success
        while an agent on it cannot import a thing. The verification loop is
        what turns that into a beacon somebody can read."""
        _install, verify = _install_and_verify_halves(self.toolchain)
        for tool in sorted(
            agent_job_skills.REQUIRED_BINARIES | agent_job_skills.REQUIRED_PYTHON_IMPORTS
        ):
            with self.subTest(tool=tool):
                self.assertIn(tool, verify, f"{tool!r} is installed but never verified")

    def test_the_declared_requirements_are_the_ones_the_bodies_actually_name(self) -> None:
        """REQUIRED_BINARIES/REQUIRED_PYTHON_IMPORTS is the list the installer
        is held to. If a body names a tool that never made it onto that list,
        the check above passes while the promise is still broken — so the list
        is verified against the prose, not trusted."""
        bodies = self._all_bodies()
        for binary in sorted(agent_job_skills.REQUIRED_BINARIES):
            with self.subTest(binary=binary):
                self.assertIn(binary, bodies, f"{binary!r} is declared required but no body uses it")
        for module in sorted(agent_job_skills.REQUIRED_PYTHON_IMPORTS):
            with self.subTest(module=module):
                self.assertIn(module, bodies)

    def test_a_body_naming_an_uninstalled_tool_is_caught(self) -> None:
        """The check that matters, driven directly rather than trusted.

        A behavioural test cannot see this: a body naming `awk` compiles,
        stores, delivers and reads perfectly, and only fails on a box.
        """
        candidates = ("jq", "libreoffice", "tesseract", "gnumeric", "ocrmypdf")
        bodies = self._all_bodies()
        for tool in candidates:
            if tool in bodies:
                with self.subTest(tool=tool):
                    self.assertIn(
                        tool,
                        self.toolchain,
                        f"a skill body reaches for {tool!r} and the installer does not provide it",
                    )

    def test_the_toolchain_step_is_wired_into_main(self) -> None:
        """Built-and-never-called is this repo's most common defect, and a
        shell function nothing invokes is its purest form: it parses, it
        passes `bash -n`, and it never runs."""
        self.assertIn("install_data_toolchain ||", self.installer)
        self.assertIn("set_phase data_toolchain", self.installer)

    def test_the_toolchain_step_is_non_fatal(self) -> None:
        """A box that cannot install pandas must still finish provisioning
        with everything it had before this existed. `fail` aborts the whole
        install; this function may never call it."""
        self.assertNotIn("fail ", self.toolchain)
        self.assertIn("report_beacon 0", self.toolchain)

    def test_it_verifies_as_the_service_user_not_as_root(self) -> None:
        """Root's PATH and root's python are not the ones the agent gets.
        Verifying as root would pass on a box where the agent still cannot
        reach a single one of these."""
        self.assertIn('sudo -u "${SERVICE_USER}"', self.toolchain)


class DockerCopyHonestyTests(unittest.TestCase):
    """Docker chooses HOW a command runs, never WHETHER (founder, 2026-08-22).

    install_docker's failure beacons predated that ruling and still told an
    operator that shell.execute "will stay unavailable" — which stopped being
    true the day the ruling landed, and is the exact reporting-failure-on-
    success shape the outcome-honesty law names as the worst case: it sends
    someone to fix a box that is working.
    """

    def setUp(self) -> None:
        self.installer = _installer_source()

    def test_canary(self) -> None:
        self.assertIn("install_docker() {", self.installer)
        self.assertIn("report_beacon", self.installer)

    def test_no_docker_failure_claims_shell_execute_is_unavailable(self) -> None:
        for line in self.installer.splitlines():
            stripped = line.strip()
            if not stripped.startswith("report_beacon") and "|| log " not in stripped:
                continue
            lowered = stripped.lower()
            if "docker" not in lowered:
                continue
            with self.subTest(line=stripped[:90]):
                self.assertNotIn("stay unavailable", lowered)
                self.assertNotIn("will be unavailable", lowered)


if __name__ == "__main__":
    unittest.main()
