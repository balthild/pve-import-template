apt update
apt install -y software-properties-common

codename=$(lsb_release --codename --short)
add-apt-repository "deb http://download.proxmox.com/debian/pve $codename pve-no-subscription"

apt update
apt install -y git python3-tqdm python3-yaml libguestfs-tools
