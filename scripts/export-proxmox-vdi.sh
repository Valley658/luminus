#!/usr/bin/env bash
# Run on the Proxmox host, not inside the Windows guest.
set -Eeuo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: bash export-proxmox-vdi.sh [--vmid 102] [--disk sata0] [--output-dir /path]

Exports one stopped Proxmox VM disk as a standalone VirtualBox VDI.
Defaults: VM 102, boot disk (or sole disk), /var/lib/vz/export.
The VM is never stopped, started, removed, or reconfigured by this script.
For multiple disks, run once for each --disk, keeping the VM stopped throughout.
EOF
}

VMID=102
DISK=''
OUTPUT_DIR=/var/lib/vz/export
while (($#)); do
  case "$1" in
    --vmid|--disk|--output-dir)
      (($# >= 2)) || { usage >&2; exit 2; }
      case "$1" in
        --vmid) VMID=$2 ;;
        --disk) DISK=$2 ;;
        --output-dir) OUTPUT_DIR=$2 ;;
      esac
      shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ $EUID == 0 ]] || { echo 'Run as root on Proxmox.' >&2; exit 1; }
[[ $VMID =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid VM ID.' >&2; exit 2; }
[[ -z $DISK || $DISK =~ ^(sata|scsi|virtio|ide)[0-9]+$ ]] || { echo 'Invalid disk slot.' >&2; exit 2; }
[[ $OUTPUT_DIR == /* ]] || { echo 'Output directory must be absolute.' >&2; exit 2; }
for tool in qm pvesm qemu-img python3 sha256sum flock df realpath; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done

# Prevent two copies of this exporter running against the same VM.
exec 9>"/run/lock/luminus-vdi-export-${VMID}.lock"
flock -n 9 || { echo 'Another export of this VM is already running.' >&2; exit 1; }
assert_stopped() {
  [[ $(qm status "$VMID") == 'status: stopped' ]] || {
    echo "VM $VMID must be fully shut down first. No export was accepted." >&2
    exit 1
  }
}
assert_stopped
CONFIG=$(qm config "$VMID")
if grep -q '^lock:' <<<"$CONFIG"; then
  echo 'VM has an existing Proxmox lock. Finish that operation first.' >&2
  exit 1
fi

# Read the current storage mapping instead of assuming a particular LVM path.
SELECTION=$(python3 -c '
import re,sys
config=sys.stdin.read()
slots={}
for line in config.splitlines():
    m=re.match(r"^((?:sata|scsi|virtio|ide)\d+):\s*(.*)$",line)
    if m and "media=cdrom" not in m[2] and not m[2].startswith("none"):
        slots[m[1]]=m[2]
selected=sys.argv[1]
if not selected:
    boot=re.search(r"^boot:\s*order=([^\n]+)",config,re.M)
    order=boot[1].split(",")[0].split(";") if boot else []
    selected=next((slot for slot in order if slot in slots),"")
    if not selected and len(slots)==1:
        selected=next(iter(slots))
if selected not in slots:
    sys.exit("Cannot select a disk. Specify --disk; available: "+", ".join(slots))
value=slots[selected]
if "file=" in value:
    sys.exit("Unsupported disk configuration; inspect qm config manually.")
print(selected)
print(value.split(",",1)[0])
' "$DISK" <<<"$CONFIG")
DISK=$(head -n 1 <<<"$SELECTION")
VOLUME=$(tail -n 1 <<<"$SELECTION")
SOURCE=$(pvesm path "$VOLUME")
[[ -e $SOURCE ]] || { echo 'Resolved disk source does not exist.' >&2; exit 1; }
SOURCE_INFO=$(qemu-img info --output=json "$SOURCE")
VIRTUAL_SIZE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])' <<<"$SOURCE_INFO")

mkdir -p -- "$OUTPUT_DIR"
OUTPUT_DIR=$(realpath -- "$OUTPUT_DIR")
AVAILABLE=$(df -B1 --output=avail "$OUTPUT_DIR" | tail -n 1 | tr -d ' ')
# First try a conservative full-size check. For a stopped raw source, a
# read-only zero-block scan gives a tighter bound for dynamic VDI allocation.
REQUIRED=$((VIRTUAL_SIZE + 536870912))
if ((AVAILABLE < REQUIRED)); then
  SOURCE_FORMAT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["format"])' <<<"$SOURCE_INFO")
  if [[ $SOURCE_FORMAT == raw ]]; then
    echo 'Checking nonzero 1 MiB blocks to estimate dynamic VDI size (read-only)...'
    assert_stopped
    ALLOCATED=$(python3 - "$SOURCE" "$VIRTUAL_SIZE" <<'PY'
import sys
source, size = sys.argv[1], int(sys.argv[2])
block = 1024 * 1024
zero = bytes(block)
allocated = scanned = 0
with open(source, 'rb', buffering=0) as disk:
    while scanned < size:
        data = disk.read(min(block, size - scanned))
        if not data:
            sys.exit('Unexpected end of source disk')
        if data != zero[:len(data)]:
            allocated += block
        scanned += len(data)
        if scanned % (1024**3) == 0:
            print(f'\rScanned {scanned // (1024**3)} / {(size + 1024**3 - 1) // (1024**3)} GiB',
                  end='', file=sys.stderr, flush=True)
print(file=sys.stderr)
print(allocated)
PY
)
    assert_stopped
    # VDI defaults to 1 MiB dynamic clusters. Add 1 GiB for metadata and margin.
    REQUIRED=$((ALLOCATED + 1073741824))
    AVAILABLE=$(df -B1 --output=avail "$OUTPUT_DIR" | tail -n 1 | tr -d ' ')
    echo "Dynamic VDI budget including margin: $REQUIRED bytes; available: $AVAILABLE."
  fi
fi
if ((AVAILABLE < REQUIRED)); then
  echo "Insufficient safe free space: need $REQUIRED bytes, available $AVAILABLE." >&2
  echo 'Use --output-dir pointing to a larger mounted filesystem.' >&2
  exit 1
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$OUTPUT_DIR/vm-${VMID}-${DISK}-${STAMP}"
mkdir -- "$DEST" # Refuse to overwrite an earlier export.
NAME="vm-${VMID}-${DISK}.vdi"
PARTIAL="$DEST/$NAME.partial"
trap 'echo "Export failed. Any incomplete file remains in: $DEST" >&2' ERR
printf '%s\n' "$CONFIG" >"$DEST/proxmox-vm.conf"
printf '%s\n' "$SOURCE_INFO" >"$DEST/source-disk-info.json"
echo "VM $VMID / $DISK / $VOLUME"
echo "Destination: $DEST/$NAME"
echo 'Keep the VM shut down until this script completes.'
assert_stopped
qemu-img convert -p -O vdi "$SOURCE" "$PARTIAL"
assert_stopped
qemu-img info --output=json "$PARTIAL" | python3 -c '
import json,sys
info=json.load(sys.stdin)
if info["format"]!="vdi" or info["virtual-size"]!=int(sys.argv[1]) or info.get("backing-filename"):
    sys.exit("Export validation failed")
' "$VIRTUAL_SIZE"
echo 'Comparing exported disk content with source (this may take a while)...'
qemu-img compare -p "$SOURCE" "$PARTIAL"
assert_stopped
mv -n -- "$PARTIAL" "$DEST/$NAME"
(cd "$DEST" && sha256sum "$NAME" >SHA256SUMS)
cat >"$DEST/IMPORT.txt" <<EOF
VM: $VMID; source disk slot: $DISK; source volume: $VOLUME
The VDI contains the complete selected disk, including Windows, site files,
database, user uploads, and credentials. Keep this backup private.

Copy the VDI and SHA256SUMS to the destination PC. Verify the checksum:
  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\VMs\$NAME'

VirtualBox: create a VM and choose "Use an existing virtual hard disk".
Attach this VDI to a SATA/AHCI controller. Match BIOS/UEFI to proxmox-vm.conf
(SeaBIOS = EFI off; OVMF = EFI on). Do not install Windows over this disk.
Firmware, TPM, network, and other VM hardware settings are not converted.
BitLocker/encrypted systems may require their recovery key.
Install VirtualBox Guest Additions after boot. Adjust networking and SSH/RDP
firewall rules for the destination environment before using the migrated site.
Keep the source and migrated VM from running the same site/tunnel together.
EOF
echo 'Export complete; source VM and disk are unchanged.'
du -h -- "$DEST/$NAME"
echo "Checksums: $DEST/SHA256SUMS"
