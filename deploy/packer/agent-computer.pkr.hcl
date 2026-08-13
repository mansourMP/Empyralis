# Empyralis Agent Computer — DigitalOcean image build (MAN-128)
#
# WHY THIS EXISTS
# ---------------
# Provisioning used to install the entire runtime at first boot: cloud-init
# fetched scripts/install-agent-computer.sh over the network and that script
# ran apt, added the NodeSource repo, installed Node 20, downloaded the gateway
# tarball, then started the service. Every one of those steps is a network call
# on a box that has existed for thirty seconds, and every failure mode collapses
# into the same symptom the control plane sees: silence. Four independent bugs
# hid behind each other that way (a stale installer, a `curl | bash` that exits
# 0 when curl dies, production running day-old code, and a bash->sh regression
# that killed the script — including its own failure beacons — on line 2).
#
# This template moves all of that to BUILD time. A build failure is loud,
# attributable to one log line, and retryable for free. A first-boot failure is
# a customer sitting in front of a spinner.
#
# WHAT COMES OUT
# --------------
# A DigitalOcean snapshot containing Node 20, every system dependency, the
# service user, the directory tree, the launcher, the gateway artifact itself,
# and an enabled-but-not-started systemd unit. It contains NO pairing token, NO
# API URL, NO customer data, NO SSH keys and NO machine identity. See
# scripts/90-cleanup.sh and scripts/80-verify.sh — the latter fails the build if
# any of those leak in.
#
# HOW TO RUN IT
# -------------
#   export DIGITALOCEAN_ACCESS_TOKEN=...     # needs droplet + image write scope
#   packer init   deploy/packer
#   packer validate deploy/packer
#   packer build  deploy/packer
#
# CI runs exactly that: .github/workflows/build-agent-computer-image.yml.

packer {
  required_plugins {
    digitalocean = {
      version = ">= 1.4.1"
      source  = "github.com/digitalocean/digitalocean"
    }
  }
}

# ── Inputs ──────────────────────────────────────────────────────────────────

variable "do_api_token" {
  type        = string
  sensitive   = true
  default     = env("DIGITALOCEAN_ACCESS_TOKEN")
  description = <<-EOT
    DigitalOcean personal access token with WRITE scope on droplets and images
    (the build creates a throwaway droplet, snapshots it, transfers the snapshot
    to the other regions, then destroys the droplet). Never checked in; supplied
    by the DIGITALOCEAN_ACCESS_TOKEN environment variable / repo secret.
  EOT
}

variable "base_image" {
  type    = string
  default = "ubuntu-24-04-x64"
  # MUST match PROVIDER_CONFIGS["digitalocean"].default_image in
  # server_modules/vps_provisioning_service.py (line ~320). Baking on a
  # different base than the one the fallback installer path targets would mean
  # the two paths diverge silently. install-agent-computer.sh's detect_ubuntu()
  # accepts 22.04 and 24.04; this is the 24.04 the provisioner actually asks for.
  description = "DigitalOcean base image slug to build on top of."
}

variable "build_region" {
  type        = string
  default     = "nyc3"
  description = "Region the throwaway build droplet runs in. Also the region the snapshot is first created in, before transfer."
}

variable "build_size" {
  type    = string
  default = "s-1vcpu-1gb"
  # DO NOT raise this casually. A DigitalOcean snapshot images the WHOLE disk of
  # the droplet it was taken from, and a droplet can only be created from a
  # snapshot if its disk is at least as large as the source droplet's. Build on
  # an 80GB plan and the resulting image simply cannot be used for any droplet
  # smaller than 80GB.
  #
  # The size picker (_normalize_digitalocean_plans in
  # server_modules/vps_provisioning_service.py) drops anything under 1024MB RAM,
  # so the smallest plan a customer can select is s-1vcpu-1gb = 25GB disk. That
  # is the floor this build must sit at or below. Nothing here compiles — the
  # gateway arrives prebuilt — so 1 vCPU / 1GB is ample for the build itself.
  description = "Droplet size for the build. Sets the minimum disk size of every droplet created from the resulting image."
}

variable "snapshot_regions" {
  type = list(string)
  default = [
    "nyc3",
    "sfo3",
    "lon1",
    "fra1",
    "sgp1",
    "blr1",
  ]
  # Must cover every region in PROVIDER_CONFIGS["digitalocean"].regions
  # (vps_provisioning_service.py ~line 322). A DO snapshot only exists in the
  # regions it has been transferred to; asking for a droplet from an image that
  # is not present in the target region fails at create time. Including
  # build_region here is harmless — the plugin de-duplicates it (step_snapshot.go
  # seeds its region set with c.Region before iterating).
  #
  # COST NOTE: DigitalOcean bills snapshot storage per region copy. Six regions
  # means six copies of the image. Trim this list if a region is not actually
  # offered to customers.
  description = "Regions the finished snapshot is transferred to."
}

variable "gateway_version" {
  type    = string
  default = "latest"
  # The version segment of the R2/empyralis.ai release path that
  # .github/workflows/release-gateway-linux.yml publishes to. "latest" is a
  # moving pointer at BUILD time only: whatever it resolves to during the build
  # is frozen into the image, and the exact bytes are recorded (with their
  # sha256) in /etc/empyralis/image-manifest.json on the image itself, so a
  # running box can always say what it is.
  description = "Gateway release version to bake into the image."
}

variable "artifact_base_url" {
  type        = string
  default     = "https://empyralis.ai/releases/agent-computer"
  description = "Base URL of the published gateway releases (Cloudflare Worker route in front of the empyralis-agent-computer-releases R2 bucket)."
}

variable "verify_artifact_checksum" {
  type    = bool
  default = true
  # release-gateway-linux.yml publishes a .sha256 sidecar next to every tarball.
  # Verifying it turns a truncated or wrong-version download into a build
  # failure with a name, rather than a droplet that boots and half-works.
  description = "Require and verify the .sha256 sidecar for the gateway artifact."
}

variable "zero_fill_free_space" {
  type    = bool
  default = false
  # DigitalOcean's own marketplace cleanup script zero-fills free space before
  # snapshotting (dd if=/dev/zero of=/zerofile). It shrinks the snapshot and
  # scrubs deleted-file remnants, but it deliberately fills the disk to 100% and
  # adds real time to every build. We are producing a private base image, not a
  # Marketplace submission, and nothing sensitive is ever written on the build
  # droplet in the first place, so this defaults off. Flip it on if snapshot
  # storage cost becomes a concern.
  description = "Zero-fill free disk space before snapshotting (slower build, smaller snapshot)."
}

variable "image_name_prefix" {
  type        = string
  default     = "empyralis-agent-computer"
  description = "Prefix for the snapshot name and the build droplet name."
}

variable "source_commit" {
  type        = string
  default     = ""
  # CI passes github.sha. Recorded in /etc/empyralis/image-manifest.json so a
  # running box can say which commit produced the image it booted from.
  description = "Git commit this image was built from."
}

# ── Derived ─────────────────────────────────────────────────────────────────

locals {
  timestamp     = formatdate("YYYYMMDD-hhmmss", timestamp())
  snapshot_name = "${var.image_name_prefix}-${var.gateway_version}-${local.timestamp}"
  droplet_name  = "packer-${var.image_name_prefix}-${local.timestamp}"
}

# ── Build droplet ───────────────────────────────────────────────────────────

source "digitalocean" "agent_computer" {
  api_token = var.do_api_token
  image     = var.base_image
  region    = var.build_region
  size      = var.build_size

  # DO's Ubuntu cloud image logs in as root; Packer injects its own ephemeral
  # keypair, and 90-cleanup.sh removes the resulting authorized_keys before the
  # snapshot is taken.
  ssh_username = "root"

  droplet_name  = local.droplet_name
  snapshot_name = local.snapshot_name

  snapshot_regions = var.snapshot_regions
  # Block until the cross-region transfers finish, so the workflow only reports
  # an image ID that is actually usable in every region it names.
  wait_snapshot_transfer = true
  snapshot_timeout       = "30m"
  transfer_timeout       = "60m"
  state_timeout          = "10m"

  tags          = ["empyralis", "packer-build", "agent-computer-image"]
  snapshot_tags = ["empyralis-agent-computer"]

  monitoring = false
  ipv6       = false

  # Deliberately no user_data. Nothing about Empyralis' runtime config belongs
  # on the build droplet — the image is shared across every customer.
}

build {
  name    = "agent-computer"
  sources = ["source.digitalocean.agent_computer"]

  # Create the upload target explicitly rather than relying on the file
  # provisioner's directory-creation behaviour. One inline command is cheaper
  # than debugging "file not found" from inside a build droplet.
  provisioner "shell" {
    inline = ["mkdir -p /tmp/empyralis-image-files"]
  }

  # Files next, so the install scripts can just move them into place. The
  # trailing slash on source means "the CONTENTS of files/", not "the files/
  # directory itself".
  provisioner "file" {
    source      = "${path.root}/files/"
    destination = "/tmp/empyralis-image-files/"
  }

  # Explicit `bash`, explicit flags. On Ubuntu /bin/sh is dash; dash aborts on
  # `set -o pipefail`, which is precisely how a previous revision of the boot
  # installer silently ran nothing at all. Never leave the interpreter to chance
  # in this codebase.
  provisioner "shell" {
    execute_command = "{{ .Vars }} bash -o errexit -o nounset -o pipefail '{{ .Path }}'"

    environment_vars = [
      "DEBIAN_FRONTEND=noninteractive",
      "EMPYRALIS_GATEWAY_VERSION=${var.gateway_version}",
      "EMPYRALIS_ARTIFACT_BASE_URL=${var.artifact_base_url}",
      "EMPYRALIS_VERIFY_CHECKSUM=${var.verify_artifact_checksum ? "1" : "0"}",
      "EMPYRALIS_ZERO_FILL=${var.zero_fill_free_space ? "1" : "0"}",
      "EMPYRALIS_BASE_IMAGE=${var.base_image}",
      "EMPYRALIS_SOURCE_COMMIT=${var.source_commit}",
    ]

    scripts = [
      "${path.root}/scripts/00-wait-for-cloud-init.sh",
      "${path.root}/scripts/10-system-deps.sh",
      "${path.root}/scripts/20-node20.sh",
      "${path.root}/scripts/30-user-and-dirs.sh",
      "${path.root}/scripts/40-gateway-artifact.sh",
      "${path.root}/scripts/50-launcher-and-systemd.sh",
      "${path.root}/scripts/60-docker.sh",
      "${path.root}/scripts/70-channel-transport.sh",
      "${path.root}/scripts/80-verify.sh",
      "${path.root}/scripts/90-cleanup.sh",
    ]
  }

  # The manifest is how CI learns the snapshot ID. The DigitalOcean artifact id
  # is "<region>,<region>,...:<numeric image id>"; the workflow splits on ":".
  post-processor "manifest" {
    # path.root, NOT a bare filename: the manifest post-processor writes
    # relative to packer's working directory, and CI invokes
    # `packer build deploy/packer` from the repo root. A bare filename would
    # land the manifest somewhere the workflow does not look.
    output     = "${path.root}/packer-manifest.json"
    strip_path = true
    custom_data = {
      gateway_version = var.gateway_version
      base_image      = var.base_image
      build_size      = var.build_size
      snapshot_name   = local.snapshot_name
      source_commit   = var.source_commit
    }
  }
}
