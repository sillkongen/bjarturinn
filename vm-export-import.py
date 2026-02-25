#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from getpass import getpass
from pathlib import Path
from typing import List, Optional
#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from getpass import getpass
from pathlib import Path
from typing import List, Optional

from tqdm import tqdm

BACKUP_RE = re.compile(r"vzdump-qemu-(\d+)-.*\.(vma|vma\.lzo|vma\.gz|vma\.zst)$")
PERCENT_RE = re.compile(r"(\d{1,3})%")

def require_root():
    if os.geteuid() != 0:
        print("ERROR: run as root (needed for mount + Proxmox commands).", file=sys.stderr)
        sys.exit(1)

def run(cmd, check=True, capture=False):
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    print(f"+ {' '.join(shlex.quote(c) for c in cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )

def list_qemu_vmids() -> List[int]:
    # qm list:
    # VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID
    p = run(["qm", "list"], capture=True)
    vmids = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split()
        if parts and parts[0].isdigit():
            vmids.append(int(parts[0]))
    return sorted(vmids)

def parse_vmids(vmids_str: str) -> List[int]:
    if vmids_str.strip().lower() == "all":
        vmids = list_qemu_vmids()
        if not vmids:
            raise ValueError("No QEMU VMs found (qm list returned none).")
        return vmids

    vmids = set()
    for part in vmids_str.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"Invalid VMID: {part}")
        vmids.add(int(part))

    if not vmids:
        raise ValueError("No valid VMIDs provided")
    return sorted(vmids)

def is_mounted(mountpoint: str) -> bool:
    mp = str(Path(mountpoint).resolve())
    with open("/proc/mounts", "r") as f:
        return any(line.split()[1] == mp for line in f)

def mount_smb(share, mountpoint, username, password, domain=None, vers="3.0"):
    mountpoint = Path(mountpoint)
    mountpoint.mkdir(parents=True, exist_ok=True)

    cred_dir = Path("/run") if Path("/run").exists() else Path(tempfile.gettempdir())
    cred_path = cred_dir / f"pve_smb_creds_{os.getpid()}"
    creds = f"username={username}\npassword={password}\n"
    if domain:
        creds += f"domain={domain}\n"
    cred_path.write_text(creds)
    os.chmod(cred_path, 0o600)

    try:
        opts = [
            f"credentials={cred_path}",
            f"vers={vers}",
            "rw",
            "uid=0", "gid=0",
            "file_mode=0770", "dir_mode=0770",
            "nounix",
            "serverino",
        ]
        run(["mount", "-t", "cifs", share, str(mountpoint), "-o", ",".join(opts)])
    finally:
        try:
            cred_path.unlink(missing_ok=True)
        except Exception:
            pass

def umount_smb(mountpoint):
    run(["umount", "-f", str(mountpoint)], check=False)

def find_backups_in_dir(d: Path):
    found = []
    if not d.exists():
        return found
    for p in sorted(d.iterdir()):
        if p.is_file():
            m = BACKUP_RE.match(p.name)
            if m:
                found.append((int(m.group(1)), p))
    return found

def newest_backup_for_vmid(vm_dir: Path, vmid: int) -> Optional[Path]:
    candidates = [p for vid, p in find_backups_in_dir(vm_dir) if vid == vmid]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.stat().st_mtime)
    return candidates[-1]

def write_manifest(vm_dir: Path, vmid: int, backup_file: Path):
    manifest = {
        "vmid": vmid,
        "created_epoch": int(time.time()),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node": os.uname().nodename,
        "backup_file": backup_file.name,
        "size_bytes": backup_file.stat().st_size,
        "backup_mtime_epoch": int(backup_file.stat().st_mtime),
    }
    out = vm_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"Manifest written: {out}")

def next_free_vmid() -> int:
    p = run(["pvesh", "get", "/cluster/nextid"], capture=True)
    s = (p.stdout or "").strip()
    if s.isdigit():
        return int(s)

    # fallback
    used = set(list_qemu_vmids())
    vmid = 100
    while vmid in used:
        vmid += 1
    return vmid

def vzdump_with_progress(vmid: int, dumpdir: Path, compress="zstd", mode="stop", verbose=False):
    # NOTE: Do NOT use --notes-template; some Proxmox versions require --storage for that.
    cmd = [
        "vzdump", str(vmid),
        "--dumpdir", str(dumpdir),
        "--mode", mode,
        "--compress", compress,
    ]

    print(f"Starting export for VM {vmid} -> {dumpdir}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    pbar = tqdm(total=100, desc=f"VM {vmid}", unit="%", leave=True, dynamic_ncols=True)
    last_percent = 0
    tail = []  # keep last lines for debugging if it fails

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line:
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)

        m = PERCENT_RE.search(line)
        if m:
            pct = int(m.group(1))
            pct = 100 if pct > 100 else pct
            if pct > last_percent:
                pbar.update(pct - last_percent)
                last_percent = pct

        if verbose and line:
            tqdm.write(line)  # prints cleanly without corrupting the progress bar

    proc.wait()

    if proc.returncode == 0 and last_percent < 100:
        pbar.update(100 - last_percent)

    pbar.close()

    if proc.returncode != 0:
        if tail:
            print("\n--- vzdump output (tail) ---", file=sys.stderr)
            for l in tail:
                print(l, file=sys.stderr)
            print("--- end tail ---\n", file=sys.stderr)
        raise RuntimeError(f"vzdump failed for VM {vmid} (exit {proc.returncode})")

def qmrestore_backup(backup_path: Path, target_vmid: int, storage: Optional[str] = None):
    cmd = ["qmrestore", str(backup_path), str(target_vmid)]
    if storage:
        cmd += ["--storage", storage]
    run(cmd)

def main():
    require_root()

    ap = argparse.ArgumentParser(
        description="Export/import Proxmox QEMU VMs via SMB (one folder per VMID)."
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", action="store_true", help="Export VMs to SMB (vzdump).")
    mode.add_argument("--import", dest="do_import", action="store_true", help="Import VMs from SMB (qmrestore).")

    ap.add_argument("--vmids", required=True, help="Comma-separated VMIDs (e.g. 101,102) or 'all'")

    ap.add_argument("--share", required=True, help="SMB share, e.g. //server/share")
    ap.add_argument("--mountpoint", default="/mnt/pve_smb_migrate", help="Local mountpoint.")
    ap.add_argument("--username", required=True, help="SMB username")
    ap.add_argument("--password", default=None, help="SMB password (omit to be prompted)")
    ap.add_argument("--domain", default=None, help="SMB domain (optional)")
    ap.add_argument("--vers", default="3.0", help="SMB protocol version (default 3.0)")

    ap.add_argument("--compress", default="zstd", choices=["zstd", "lzo", "gzip", "0"],
                    help="vzdump compression (default zstd)")
    ap.add_argument("--mode", default="stop", choices=["stop", "snapshot", "suspend"],
                    help="vzdump mode (default stop).")

    ap.add_argument("--storage", default=None,
                    help="Target Proxmox storage for restored disks (qmrestore --storage).")
    ap.add_argument("--new-vmid", action="store_true",
                    help="Allocate new VMIDs on import to avoid conflicts.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print vzdump output lines (won't break progress bar).")

    args = ap.parse_args()

    try:
        vmids = parse_vmids(args.vmids)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if not args.password:
        args.password = getpass("SMB password: ")

    print(f"VMIDs: {', '.join(map(str, vmids))}")

    mounted_here = False
    try:
        if not is_mounted(args.mountpoint):
            mount_smb(args.share, args.mountpoint, args.username, args.password,
                      domain=args.domain, vers=args.vers)
            mounted_here = True

        rootdir = Path(args.mountpoint)

        if args.export:
            for vmid in vmids:
                vm_dir = rootdir / str(vmid)
                vm_dir.mkdir(parents=True, exist_ok=True)

                before = {p.name for vid, p in find_backups_in_dir(vm_dir) if vid == vmid}

                vzdump_with_progress(
                    vmid, vm_dir,
                    compress=args.compress,
                    mode=args.mode,
                    verbose=args.verbose
                )

                # identify the new backup in this VMID folder
                after = [p for vid, p in find_backups_in_dir(vm_dir) if vid == vmid and p.name not in before]
                if after:
                    after.sort(key=lambda x: x.stat().st_mtime)
                    backup_file = after[-1]
                else:
                    backup_file = newest_backup_for_vmid(vm_dir, vmid)
                    if backup_file is None:
                        raise RuntimeError(f"Could not find backup file for VMID {vmid} in {vm_dir}")

                write_manifest(vm_dir, vmid, backup_file)
                print(f"Exported VM {vmid} -> {backup_file.name}")

            print("Export complete.")

        else:
            for vmid in vmids:
                vm_dir = rootdir / str(vmid)
                backup = newest_backup_for_vmid(vm_dir, vmid)
                if backup is None:
                    raise RuntimeError(f"No backup found for VMID {vmid} in {vm_dir}")

                target_vmid = next_free_vmid() if args.new_vmid else vmid
                print(f"Restoring {backup.name} -> VMID {target_vmid}")
                qmrestore_backup(backup, target_vmid, storage=args.storage)

            print("Import complete.")

    finally:
        if mounted_here and is_mounted(args.mountpoint):
            umount_smb(args.mountpoint)

if __name__ == "__main__":
    main()
from tqdm import tqdm

BACKUP_RE = re.compile(r"vzdump-qemu-(\d+)-.*\.(vma|vma\.lzo|vma\.gz|vma\.zst)$")
PERCENT_RE = re.compile(r"(\d{1,3})%")

def require_root():
    if os.geteuid() != 0:
        print("ERROR: run as root (needed for mount + Proxmox commands).", file=sys.stderr)
        sys.exit(1)

def run(cmd, check=True, capture=False):
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    print(f"+ {' '.join(shlex.quote(c) for c in cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )

def list_qemu_vmids() -> List[int]:
    # qm list:
    # VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID
    p = run(["qm", "list"], capture=True)
    vmids = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split()
        if parts and parts[0].isdigit():
            vmids.append(int(parts[0]))
    return sorted(vmids)

def parse_vmids(vmids_str: str) -> List[int]:
    if vmids_str.strip().lower() == "all":
        vmids = list_qemu_vmids()
        if not vmids:
            raise ValueError("No QEMU VMs found (qm list returned none).")
        return vmids

    vmids = set()
    for part in vmids_str.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"Invalid VMID: {part}")
        vmids.add(int(part))

    if not vmids:
        raise ValueError("No valid VMIDs provided")
    return sorted(vmids)

def is_mounted(mountpoint: str) -> bool:
    mp = str(Path(mountpoint).resolve())
    with open("/proc/mounts", "r") as f:
        return any(line.split()[1] == mp for line in f)

def mount_smb(share, mountpoint, username, password, domain=None, vers="3.0"):
    mountpoint = Path(mountpoint)
    mountpoint.mkdir(parents=True, exist_ok=True)

    cred_dir = Path("/run") if Path("/run").exists() else Path(tempfile.gettempdir())
    cred_path = cred_dir / f"pve_smb_creds_{os.getpid()}"
    creds = f"username={username}\npassword={password}\n"
    if domain:
        creds += f"domain={domain}\n"
    cred_path.write_text(creds)
    os.chmod(cred_path, 0o600)

    try:
        opts = [
            f"credentials={cred_path}",
            f"vers={vers}",
            "rw",
            "uid=0", "gid=0",
            "file_mode=0770", "dir_mode=0770",
            "nounix",
            "serverino",
        ]
        run(["mount", "-t", "cifs", share, str(mountpoint), "-o", ",".join(opts)])
    finally:
        try:
            cred_path.unlink(missing_ok=True)
        except Exception:
            pass

def umount_smb(mountpoint):
    run(["umount", "-f", str(mountpoint)], check=False)

def find_backups_in_dir(d: Path):
    found = []
    if not d.exists():
        return found
    for p in sorted(d.iterdir()):
        if p.is_file():
            m = BACKUP_RE.match(p.name)
            if m:
                found.append((int(m.group(1)), p))
    return found

def newest_backup_for_vmid(vm_dir: Path, vmid: int) -> Optional[Path]:
    candidates = [p for vid, p in find_backups_in_dir(vm_dir) if vid == vmid]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.stat().st_mtime)
    return candidates[-1]

def write_manifest(vm_dir: Path, vmid: int, backup_file: Path):
    manifest = {
        "vmid": vmid,
        "created_epoch": int(time.time()),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node": os.uname().nodename,
        "backup_file": backup_file.name,
        "size_bytes": backup_file.stat().st_size,
        "backup_mtime_epoch": int(backup_file.stat().st_mtime),
    }
    out = vm_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"Manifest written: {out}")

def next_free_vmid() -> int:
    p = run(["pvesh", "get", "/cluster/nextid"], capture=True)
    s = (p.stdout or "").strip()
    if s.isdigit():
        return int(s)

    # fallback
    used = set(list_qemu_vmids())
    vmid = 100
    while vmid in used:
        vmid += 1
    return vmid

def vzdump_with_progress(vmid: int, dumpdir: Path, compress="zstd", mode="stop", verbose=False):
    # NOTE: Do NOT use --notes-template; some Proxmox versions require --storage for that.
    cmd = [
        "vzdump", str(vmid),
        "--dumpdir", str(dumpdir),
        "--mode", mode,
        "--compress", compress,
    ]

    print(f"Starting export for VM {vmid} -> {dumpdir}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    pbar = tqdm(total=100, desc=f"VM {vmid}", unit="%", leave=True, dynamic_ncols=True)
    last_percent = 0
    tail = []  # keep last lines for debugging if it fails

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line:
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)

        m = PERCENT_RE.search(line)
        if m:
            pct = int(m.group(1))
            pct = 100 if pct > 100 else pct
            if pct > last_percent:
                pbar.update(pct - last_percent)
                last_percent = pct

        if verbose and line:
            tqdm.write(line)  # prints cleanly without corrupting the progress bar

    proc.wait()

    if proc.returncode == 0 and last_percent < 100:
        pbar.update(100 - last_percent)

    pbar.close()

    if proc.returncode != 0:
        if tail:
            print("\n--- vzdump output (tail) ---", file=sys.stderr)
            for l in tail:
                print(l, file=sys.stderr)
            print("--- end tail ---\n", file=sys.stderr)
        raise RuntimeError(f"vzdump failed for VM {vmid} (exit {proc.returncode})")

def qmrestore_backup(backup_path: Path, target_vmid: int, storage: Optional[str] = None):
    cmd = ["qmrestore", str(backup_path), str(target_vmid)]
    if storage:
        cmd += ["--storage", storage]
    run(cmd)

def main():
    require_root()

    ap = argparse.ArgumentParser(
        description="Export/import Proxmox QEMU VMs via SMB (one folder per VMID)."
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", action="store_true", help="Export VMs to SMB (vzdump).")
    mode.add_argument("--import", dest="do_import", action="store_true", help="Import VMs from SMB (qmrestore).")

    ap.add_argument("--vmids", required=True, help="Comma-separated VMIDs (e.g. 101,102) or 'all'")

    ap.add_argument("--share", required=True, help="SMB share, e.g. //server/share")
    ap.add_argument("--mountpoint", default="/mnt/pve_smb_migrate", help="Local mountpoint.")
    ap.add_argument("--username", required=True, help="SMB username")
    ap.add_argument("--password", default=None, help="SMB password (omit to be prompted)")
    ap.add_argument("--domain", default=None, help="SMB domain (optional)")
    ap.add_argument("--vers", default="3.0", help="SMB protocol version (default 3.0)")

    ap.add_argument("--compress", default="zstd", choices=["zstd", "lzo", "gzip", "0"],
                    help="vzdump compression (default zstd)")
    ap.add_argument("--mode", default="stop", choices=["stop", "snapshot", "suspend"],
                    help="vzdump mode (default stop).")

    ap.add_argument("--storage", default=None,
                    help="Target Proxmox storage for restored disks (qmrestore --storage).")
    ap.add_argument("--new-vmid", action="store_true",
                    help="Allocate new VMIDs on import to avoid conflicts.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print vzdump output lines (won't break progress bar).")

    args = ap.parse_args()

    try:
        vmids = parse_vmids(args.vmids)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if not args.password:
        args.password = getpass("SMB password: ")

    print(f"VMIDs: {', '.join(map(str, vmids))}")

    mounted_here = False
    try:
        if not is_mounted(args.mountpoint):
            mount_smb(args.share, args.mountpoint, args.username, args.password,
                      domain=args.domain, vers=args.vers)
            mounted_here = True

        rootdir = Path(args.mountpoint)

        if args.export:
            for vmid in vmids:
                vm_dir = rootdir / str(vmid)
                vm_dir.mkdir(parents=True, exist_ok=True)

                before = {p.name for vid, p in find_backups_in_dir(vm_dir) if vid == vmid}

                vzdump_with_progress(
                    vmid, vm_dir,
                    compress=args.compress,
                    mode=args.mode,
                    verbose=args.verbose
                )

                # identify the new backup in this VMID folder
                after = [p for vid, p in find_backups_in_dir(vm_dir) if vid == vmid and p.name not in before]
                if after:
                    after.sort(key=lambda x: x.stat().st_mtime)
                    backup_file = after[-1]
                else:
                    backup_file = newest_backup_for_vmid(vm_dir, vmid)
                    if backup_file is None:
                        raise RuntimeError(f"Could not find backup file for VMID {vmid} in {vm_dir}")

                write_manifest(vm_dir, vmid, backup_file)
                print(f"Exported VM {vmid} -> {backup_file.name}")

            print("Export complete.")

        else:
            for vmid in vmids:
                vm_dir = rootdir / str(vmid)
                backup = newest_backup_for_vmid(vm_dir, vmid)
                if backup is None:
                    raise RuntimeError(f"No backup found for VMID {vmid} in {vm_dir}")

                target_vmid = next_free_vmid() if args.new_vmid else vmid
                print(f"Restoring {backup.name} -> VMID {target_vmid}")
                qmrestore_backup(backup, target_vmid, storage=args.storage)

            print("Import complete.")

    finally:
        if mounted_here and is_mounted(args.mountpoint):
            umount_smb(args.mountpoint)

if __name__ == "__main__":
    main()
