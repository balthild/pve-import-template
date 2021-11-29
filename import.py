#!/usr/bin/env python3

import sys
import os
import re
import contextlib
import urllib.request
import subprocess


def exit_missing_dep():
    print('Some dependencies are missing.')
    print('Please install them with the following command:')
    print('# apt install python3-tqdm python3-yaml libguestfs-tools')
    sys.exit(2)


try:
    import tqdm
    import yaml
except ImportError:
    exit_missing_dep()


class DownloadProgressBar(tqdm.tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def run(cmd: str, **kwargs):
    print(f'# {cmd}')
    subprocess.run(cmd, env=kwargs, shell=isinstance(cmd, str))


def get_storage_type(name: str) -> str:
    # References of the config file format:
    # https://github.com/proxmox/pve-storage/blob/b4616e5/PVE/Storage/Plugin.pm#L418
    # https://github.com/proxmox/pve-common/blob/3efa9ec/src/PVE/SectionConfig.pm#L291

    with open('/etc/pve/storage.cfg') as file:
        lines = file.readlines()

    # https://pve.proxmox.com/wiki/Storage
    file_re = re.compile(rf'^(zfspool|dir|btrfs|nfs|cifs|glusterfs|cephfs):\s*({name})\s*$')
    block_re = re.compile(rf'^(lvm|lvmthin|iscsi|iscsidirect|rbd|zfs):\s*({name})\s*$')

    for line in lines:
        file_match = file_re.match(line)
        if file_match is not None:
            return 'file'

        block_match = block_re.match(line)
        if block_match is not None:
            return 'block'


def vm_exists(vmid: int):
    return os.path.exists(f'/etc/pve/qemu-server/{vmid}.conf')


def build_customize_args(customize: dict) -> list:
    if customize is None:
        return []

    args = []

    for upload in customize.get('uploads', []):
        args.extend(['--upload', upload])

    for command in customize.get('commands', []):
        args.extend(['--run-command', command])

    return args


def import_template(template: dict, storage: str, storage_type: str):
    vmid, name, url = [template[k] for k in ('vmid', 'name', 'url')]

    if vm_exists(vmid):
        print(f'VM {vmid} exists, skipping.')
        return

    print(f'Importing {vmid} ({name}) from {url}')

    filename = f'./cloud_img/{name}.img'

    # Delete and re-download the image
    with contextlib.suppress(FileNotFoundError):
        os.remove(filename)

    with DownloadProgressBar(unit='B', unit_scale=True, miniters=1) as t:
        urllib.request.urlretrieve(url, filename=filename, reporthook=t.update_to)

    customize_args = build_customize_args(template.get('customize'))
    if len(customize_args) != 0:
        customize_cmd = ['virt-customize', '-a', filename, *customize_args]
        # https://libguestfs.org/guestfs-faq.1.html#permission-denied-when-running-libguestfs-as-root
        run(customize_cmd, LIBGUESTFS_BACKEND='direct')

    # https://pve.proxmox.com/wiki/Cloud-Init_Support#_preparing_cloud_init_templates
    run(f'qm create {vmid} --name {name} --memory 512 --net0 virtio,bridge=vmbr0')
    run(f'qm importdisk {vmid} {filename} {storage} -format qcow2')

    disk = {
        'file': f'{vmid}/vm-{vmid}-disk-0.qcow2',
        'block': f'vm-{vmid}-disk-0',
    }[storage_type]
    run(f'qm set {vmid} --scsihw virtio-scsi-pci --scsi0 {storage}:{disk}')

    run(f'qm set {vmid} --ide2 {storage}:cloudinit')
    run(f'qm set {vmid} --boot c --bootdisk scsi0')

    run(f'qm set {vmid} --serial0 socket')
    run(f'qm set {vmid} --ciuser root')

    run(f'qm template {vmid}')

    print(f'Deleting {filename}')
    os.remove(filename)

    print('Done')
    print()


def main():
    try:
        subprocess.call(['virt-customize', '--version'], stdout=subprocess.DEVNULL)
    except FileNotFoundError:
        exit_missing_dep()

    try:
        storage = sys.argv[1]
        storage_type = get_storage_type(storage)
    except IndexError:
        print('Usage: python3 import.py <storage-name>')
        sys.exit(1)

    os.makedirs("./cloud_img", exist_ok=True)

    with open('templates.yaml') as f:
        templates = yaml.load(f)
        print(templates)

    for template in templates['templates']:
        import_template(template, storage, storage_type)


if __name__ == '__main__':
    main()
