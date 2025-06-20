import subprocess
import smtplib
from email.message import EmailMessage
from datetime import datetime
from pathlib import Path
from proxmoxer import ProxmoxAPI
import time
import json
import urllib.parse
import paramiko
import secrets
import string

# === Konfigurasi Proxmox ===
PROXMOX_HOST = "192.168.56.2"
USERNAME = "root@pam"
PASSWORD = "samuel1234"
VERIFY_SSL = False
NODE = "sam"
TEMPLATE_ID = 101
STORAGE = "local-lvm"
BRIDGE = "vmbr0"
DNS_SERVER = "8.8.8.8"
SSH_IP = "192.168.56.133"
SSH_USER = "root"
SSH_PORT = 22
SSH_PASSWORD = "12345678"
# === Konfigurasi Email ===
EMAIL_FROM = "sender@gmail.com"
EMAIL_TO = "target@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "sender@gmail.com"
SMTP_PASSWORD = "tjxqomgfvvjwpwec"  # Ganti dengan password aplikasi khusus jika 2FA aktif



# === Konfigurasi VM Default ===
DEFAULT_RAM_MB = 2048  # 2GB
DEFAULT_CORES = 2
DEFAULT_DISK_SIZE = "+5G"  # tambah dari template

# === Fungsi: Ambil IP dari Pool JSON ===
def get_available_ip(vmid):
    with open("ip-pool.json", "r") as f:
        pool = json.load(f)
    for entry in pool:
        if not entry.get("used"):
            entry["used"] = True
            entry["vmid"] = vmid  # Reset VMID
            with open("ip-pool.json", "w") as f:
                json.dump(pool, f, indent=2)
            return entry["ip"], entry["gateway"], entry["ipraw"]
    raise Exception("No available IP in pool")


def release_unused_ips(proxmox):
    pool_path = Path("ip-pool.json")
    with pool_path.open("r", encoding="utf-8") as f:
        pool = json.load(f)

    active_vmids = [int(vm['vmid']) for vm in proxmox.cluster.resources.get(type='vm')]
    changed = False

    for entry in pool:
        if entry.get("used") and entry.get("vmid") not in active_vmids:
            print(f"ℹ️  VMID {entry.get('vmid')} tidak ditemukan, melepas IP {entry['ip']}")
            entry["used"] = False
            entry["vmid"] = None
            changed = True

    if changed:
        with pool_path.open("w", encoding="utf-8") as f:
            json.dump(pool, f, indent=2)
# === Fungsi: Generate VMID Unik ===
def get_next_vmid(proxmox):
    vmids = [int(vm['vmid']) for vm in proxmox.cluster.resources.get(type='vm')]
    for vmid in range(100, 9999):
        if vmid not in vmids:
            return vmid
    raise Exception("No available VMID found")

# === Fungsi: Generate Hostname ===
def generate_hostname(prefix="user"):
    timestamp = datetime.now().strftime("%H%M%d%Y%m%S%f")[:-3]
    return f"{prefix}{timestamp}"

# === Fungsi: Generate SSH Keypair ===
def generate_ssh_keypair(path):
    subprocess.run([
        "ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(path / "id_rsa"),
        "-N", ""
    ], check=True)

# === Fungsi: Kirim Email Private Key Saja ===
def send_private_key_only(recipient, private_key_path, ip):
    msg = EmailMessage()
    msg["Subject"] = "Private SSH Key for Your VPS"
    msg["From"] = EMAIL_FROM
    msg["To"] = recipient
    msg.set_content(f"Terlampir private key SSH Anda. Gunakan untuk login ke server. Jangan sebarkan ke siapa pun. \nIP publik server anda adalah : {ip}")
    with open(private_key_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="octet-stream", filename="id_rsa")

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)

# === Fungsi: Inject IP, SSH Key, Ganti Password Root ===
def inject_ssh_key_and_ip(ip, ip2, username, passwordssh, pubkey, hostname, ipconf):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    print(f"Menggunakan IP :{ip}")
    for i in range(15):
        try:
            ssh.connect(hostname=ip, username=username, password=passwordssh, timeout=30)
            print("Tekoneksi")
            ssh.exec_command(f"mkdir -p /root/.ssh && echo '{pubkey}' > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && chmod 700 /root/.ssh")
            ssh.exec_command("sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl restart ssh")
            ssh.exec_command(f"echo '{hostname}' > /etc/hostname")
            ssh.exec_command(f"echo 'root:{new_password}' | chpasswd")
            ssh.exec_command(f"sed -i 's/127.0.1.1.*/127.0.1.1\t{hostname}/' /etc/hosts || echo '127.0.1.1\t{hostname}' >> /etc/hosts")
            ssh.exec_command(f"hostnamectl set-hostname {hostname}")
            ssh.exec_command(f"echo '{ipconf}' > /etc/network/interfaces ")
            ssh.exec_command(f"systemctl restart sshd && systemctl restart networking && systemctl reboot")
            ssh.close()
            return
        except Exception as e:
            print(f"⌛ Menunggu SSH tersedia... ({i+1}/30)")
            time.sleep(5)
    print(f"Menggunakan IP :{ip2}")       
    print("Menggunakan IP berbeda ... mencoba lagi...")
    for po in range(15):
        try:
            ssh.connect(hostname=ip2, username=username, password=passwordssh, timeout=30)
            print("Tekoneksi")
            ssh.exec_command(f"mkdir -p /root/.ssh && echo '{pubkey}' > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && chmod 700 /root/.ssh")
            ssh.exec_command("sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl restart ssh")
            ssh.exec_command(f"echo '{hostname}' > /etc/hostname")
            ssh.exec_command(f"echo 'root:{new_password}' | chpasswd")
            ssh.exec_command(f"sed -i 's/127.0.1.1.*/127.0.1.1\t{hostname}/' /etc/hosts || echo '127.0.1.1\t{hostname}' >> /etc/hosts")
            ssh.exec_command(f"hostnamectl set-hostname {hostname}")
            ssh.exec_command(f"echo '{ipconf}' > /etc/network/interfaces ")
            ssh.exec_command(f"systemctl restart sshd && systemctl restart networking && systemctl reboot")
            ssh.close()
            return
        except Exception as e:
            print(f"⌛ Menunggu SSH tersedia... ({po+1}/30)")
            time.sleep(5)
                
    raise Exception("Gagal konek ke VM untuk inject SSH key dan konfigurasi IP")  
    

# === Proses Otomatisasi ===
def main():
    proxmox = ProxmoxAPI(PROXMOX_HOST, user=USERNAME, password=PASSWORD, verify_ssl=VERIFY_SSL, timeout=3600)
    release_unused_ips(proxmox)
    vmid = get_next_vmid(proxmox)
    hostname = generate_hostname()
    ip_address, gateway, ipraw = get_available_ip(vmid)

    ssh_dir = Path(f"./ssh_keys/{hostname}")
    ssh_dir.mkdir(parents=True, exist_ok=True)
    generate_ssh_keypair(ssh_dir)

    with open(ssh_dir / "id_rsa.pub", "r") as f:
        ssh_pubkey = f.read().strip()

    ipconf = f"source /etc/network/interfaces.d/*\n\n\n\nauto lo\niface lo inet loopback\n\nallow-hotplug ens18\n\nauto ens18\niface ens18 inet static\n  address {ip_address}\n  netmask 255.255.255.0\n  gateway {gateway}\n dns-nameservers 8.8.8.8"

    proxmox.nodes(NODE).qemu(TEMPLATE_ID).clone.create(
        newid=vmid,
        name=hostname,
        full=1,
        storage=STORAGE,
        format="qcow2"
    )
    print(f"🌀 VM {hostname} (ID: {vmid}) sedang dikloning...")

    print("⌛ Menunggu VM tersedia dan tidak terkunci...")
    for _ in range(300):
        try:
            status = proxmox.nodes(NODE).qemu(vmid).status.current.get()
            if status.get("lock") is None:
                break
        except:
            pass
        time.sleep(2)
    else:
        raise Exception("❌ VM masih terkunci setelah 10 Menit.")

    proxmox.nodes(NODE).qemu(vmid).config.post(
        **{
            "cores": DEFAULT_CORES,
            "memory": DEFAULT_RAM_MB,
            "name": hostname,
            "nameserver": DNS_SERVER
        }
    )

    proxmox.nodes(NODE).qemu(vmid).status.stop.post()
    for _ in range(30):
        status = proxmox.nodes(NODE).qemu(vmid).status.current.get()
        if status["status"] == "stopped":
            break
    time.sleep(2)
    proxmox.nodes(NODE).qemu(vmid).resize.put(
    disk='scsi0',  # atau 'virtio0', tergantung template-mu
    size=DEFAULT_DISK_SIZE  # contoh: "+5G"
)

    proxmox.nodes(NODE).qemu(vmid).status.start.post()
    print(f"🚀 VM {hostname} telah dinyalakan di IP {ip_address}")

    inject_ssh_key_and_ip(SSH_IP, ipraw, SSH_USER, SSH_PASSWORD, ssh_pubkey, hostname, ipconf)
    print("🔐 Public key & IP telah disisipkan ke dalam VM")

    send_private_key_only(EMAIL_TO, ssh_dir / "id_rsa", ipraw)
    print(f"📧 Private key telah dikirim ke {EMAIL_TO}")

if __name__ == "__main__":
    main()
