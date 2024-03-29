#!/usr/bin/env python3

import sys
import os
import re
import contextlib
import urllib.request
import subprocess
import json


def exit_missing_dep():
    print('Some dependencies are missing.')
    print('Please run setup.sh to install them.')
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


class StorageInfo:
    class Base:
        def __init__(self, name) -> None:
            self.name = name

        def format_disk_name(vmid: int):
            # https://pve.proxmox.com/pve-docs/chapter-pvesm.html
            # Should not reach here
            pass

    class Dir(Base):
        def format_disk_name(self, vmid: int):
            return f'{vmid}/vm-{vmid}-disk-0.qcow2'

    class Raw(Base):
        def format_disk_name(self, vmid: int):
            return f'vm-{vmid}-disk-0'


def run(cmd: str, **kwargs):
    print(f'# {cmd}')
    subprocess.run(cmd, env=kwargs, shell=isinstance(cmd, str))


def check_storage(name: str) -> StorageInfo.Base:
    # https://github.com/proxmox/pve-storage/blob/b4616e5/PVE/Storage/Plugin.pm#L424
    # No need to check if name == 'local' anymore.
    # The `pvesh` API output will always contain local storage properly.
    # if name == 'local':
    #    return StorageInfo.Dir(name)

    output = subprocess.check_output(['pvesh', 'get', '/storage', '--output-format=json-pretty'])
    storages = json.loads(output)

    for storage in storages:
        if storage['storage'] != name:
            continue

        # https://pve.proxmox.com/wiki/Storage#_common_storage_properties
        content = storage['content'].split(',')
        if 'images' not in content:
            raise Exception(f'PVE storage {name} does not support VM images.')

        # https://pve.proxmox.com/pve-docs/chapter-pvesm.html
        typ = storage['type']
        if typ in ['dir', 'nfs', 'glusterfs']:
            return StorageInfo.Dir(name)
        elif typ in ['zfspool', 'lvm', 'lvmthin']:
            return StorageInfo.Raw(name)
        else:
            raise Exception(f'Unsupported PVE storage type {typ}.')

    raise Exception(f'PVE storage {name} does not exist.')


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


def import_template(template: dict, storage: StorageInfo.Base):
    vmid, name, url = [template[k] for k in ('vmid', 'name', 'url')]

    if vm_exists(vmid):
        print(f'VM {vmid} exists, skipping.')
        return

    print(f'Importing {vmid} ({name}) from {url}')

    filename_dl = f'./cloud_img/{name}.img.download'
    filename_img = f'./cloud_img/{name}.img'

    # Delete and re-download the image
    with contextlib.suppress(FileNotFoundError):
        os.remove(filename_dl)
        os.remove(filename_img)

    with DownloadProgressBar(unit='B', unit_scale=True, miniters=1) as t:
        urllib.request.urlretrieve(url, filename=filename_dl, reporthook=t.update_to)

    unpack = template.get('unpack')
    if unpack:
        run(unpack.replace('{dl}', filename_dl).replace('{img}', filename_img))
    else:
        os.rename(filename_dl, filename_img)

    customize_args = build_customize_args(template.get('customize'))
    if len(customize_args) != 0:
        customize_cmd = ['virt-customize', '-a', filename_img, *customize_args]
        # https://libguestfs.org/guestfs-faq.1.html#permission-denied-when-running-libguestfs-as-root
        run(customize_cmd, LIBGUESTFS_BACKEND='direct')

    # https://pve.proxmox.com/wiki/Cloud-Init_Support#_preparing_cloud_init_templates
    run(f'qm create {vmid} --name {name} --memory 512 --net0 virtio,bridge=vmbr0')
    run(f'qm importdisk {vmid} {filename_img} {storage.name} -format qcow2')

    disk = storage.format_disk_name(vmid)
    run(f'qm set {vmid} --scsihw virtio-scsi-pci --scsi0 {storage.name}:{disk}')
    run(f'qm set {vmid} --boot c --bootdisk scsi0')

    run(f'qm set {vmid} --serial0 socket')

    if template['cloud_init']:
        run(f'qm set {vmid} --ide2 {storage.name}:cloudinit')
        run(f'qm set {vmid} --ciuser root')

    run(f'qm template {vmid}')

    print(f'Deleting {filename_img}')
    with contextlib.suppress(FileNotFoundError):
        os.remove(filename_dl)
        os.remove(filename_img)

    print('Done')
    print()


def main():
    try:
        subprocess.call(['virt-customize', '--version'], stdout=subprocess.DEVNULL)
        subprocess.call(['unzip'], stdout=subprocess.DEVNULL)
    except FileNotFoundError:
        exit_missing_dep()

    try:
        storage_name = sys.argv[1]
        storage_info = check_storage(storage_name)

        vm_name = sys.argv[2] if len(sys.argv) > 2 else None
    except IndexError:
        print('Usage: python3 import.py <storage-name> [vm-name]')
        print('If [vm-name] is specified, only the template with that name will be imported.')
        sys.exit(1)

    os.makedirs("./cloud_img", exist_ok=True)

    with open('templates.yaml') as f:
        templates = yaml.safe_load(f)

    for template in templates['templates']:
        if vm_name is None or vm_name == template['name']:
            import_template(template, storage_info)


if __name__ == '__main__':
    main()
