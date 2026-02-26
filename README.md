# bjarturinn
Export VM's from Proxmox to SMB share.

## Setup

### Install Python Dependencies

#### 1. Install python3-venv and python3-pip
```bash
# On Debian/Ubuntu
sudo apt-get update
sudo apt-get install python3-venv python3-pip

# On RHEL/CentOS
sudo yum install python3-venv python3-pip
```

#### 2. Create a Virtual Environment
```bash
python3 -m venv venv
```

#### 3. Activate the Virtual Environment
```bash
# On Linux/macOS
source venv/bin/activate

# On Windows
venv\Scripts\activate
```

#### 4. Install pip Requirements
```bash
pip install -r requirements.txt
```

## Export

### Export a Single VM
```bash
python3 vm-export-import.py --export --vmids 108 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --compress zstd --mode stop
```

### Export Multiple VMs
```bash
python3 vm-export-import.py --export --vmids 108,105,800 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --compress zstd --mode stop
```

### Export All VMspv2export.py 
```bash
python3 vm-export-import.py --export --vmids all --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --compress zstd --mode stop
```

## Import

### Import a Single VM
```bash
python3 vm-export-import.py --import --vmids 108 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --mode stop
```

### Import Multiple VMs
```bash
python3 vm-export-import.py --import --vmids 108,105,800 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --mode stop
```

### Import All VMs
```bash
python3 vm-export-import.py --import --vmids all --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --mode stop
```

## Multiple imports in one run

You can import **multiple VMs in a single command** by passing comma-separated VMIDs. They are restored **one after another** (sequentially). Example:

```bash
# Import VMIDs 789, 790 and 791 in one run (sequential)
python3 vm-export-import.py --import --vmids 789,790,791 --share //10.0.0.100/proxmox ...
```

The same applies to export: `--vmids 101,102,103` exports all three in sequence.

## Same VMID on two hypervisors

If both nodes have a VM with the same ID (e.g. 999), exporting both to the same SMB share would write to the same folder `999` and the second export would overwrite the first. Workarounds:

1. **Export**: rename the folder on the share after the first export (e.g. `999` → `999-nodeA`) so the second export can use `999` (or use a different share/path per node).
2. **Import**: use **`--new-vmid`** to restore as the next free VMID, or **`--as-vmid 1000`** (single VM) to restore from folder 999 as VMID 1000 so it doesn’t conflict with existing VM 999 on the target.

## Real example (import VMID 789 from 10.0.0.100)

Using host `10.0.0.100`, SMB share `proxmox`, and VMID `789`:

```bash
sudo python3 vm-export-import.py --import --vmids 789 \
  --share //10.0.0.100/proxmox \
  --mountpoint /mnt/pve_smb_migrate \
  --username YOURUSERNAME \
  --password
```

- The script mounts `//10.0.0.100/proxmox` and looks for a folder `789` containing a backup (e.g. `vzdump-qemu-789-*.vma.zst`).
- It restores that backup as VMID 789 on the local Proxmox node.
- To avoid ID conflict (e.g. target node already has VMID 789), reassign on import:
  - **`--new-vmid`**: restore as the next free VMID (e.g. 1000). Use for one or multiple VMs.
  - **`--as-vmid TARGET`**: restore this single VM as a specific VMID (e.g. import from folder 999 as VMID 1000).

```bash
# Next free VMID (e.g. 1000)
sudo python3 vm-export-import.py --import --vmids 789 \
  --share //10.0.0.100/proxmox --mountpoint /mnt/pve_smb_migrate \
  --username YOURUSERNAME --password --new-vmid

# Exact target VMID (single VM only)
sudo python3 vm-export-import.py --import --vmids 999 --as-vmid 1000 \
  --share //10.0.0.100/proxmox --mountpoint /mnt/pve_smb_migrate \
  --username YOURUSERNAME --password
```

- To set the storage for restored disks:

```bash
sudo python3 vm-export-import.py --import --vmids 789 \
  --share //10.0.0.100/proxmox \
  --mountpoint /mnt/pve_smb_migrate \
  --username YOURUSERNAME \
  --password \
  --storage local-lvm
```
