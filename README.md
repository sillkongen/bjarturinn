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
python3 pv2export.py --export --vmids 108 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --compress zstd --mode stop
```

### Export Multiple VMs
```bash
python3 pv2export.py --export --vmids 108,105,800 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --compress zstd --mode stop
```

### Export All VMs
```bash
python3 pv2export.py --export --vmids all --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --compress zstd --mode stop
```

## Import

### Import a Single VM
```bash
python3 pv2export.py --import --vmids 108 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --mode stop
```

### Import Multiple VMs
```bash
python3 pv2export.py --import --vmids 108,105,800 --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --mode stop
```

### Import All VMs
```bash
python3 pv2export.py --import --vmids all --share //SERVER/share --mountpoint /mnt/samba --domain WORKGROUP --vers 3.0 --username YOURUSERNAME --password --mode stop
```