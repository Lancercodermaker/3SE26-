# 远程 SSH、备份与硬件联调安全方案

本文用于在允许远程控制比赛 Ubuntu 主机前，建立一个可回滚、可审计、不过度暴露密码的操作流程。

## 1. 核心原则

- 不建议把长期 SSH 密码直接发在聊天里。
- 推荐使用一次性 SSH key 和临时 sudo 用户。
- 任何代码、系统服务、网络配置修改前先做备份。
- 能用 `tmux` 保持会话就不用裸 terminal。
- 所有关键命令和结果写入日志文件，避免只存在聊天上下文中。
- 联调只使用 debug 模式测试接收端解调效果，比赛模式另行验证。

## 2. 推荐授权方式

### 2.1 本机生成一次性 SSH key

在本机 Windows PowerShell 中生成一对临时 key：

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\sdr_codex_temp_ed25519 -C "sdr-codex-temp"
Get-Content $env:USERPROFILE\.ssh\sdr_codex_temp_ed25519.pub
```

把输出的公钥复制到 Ubuntu 主机。

### 2.2 Ubuntu 主机创建临时用户

在 Ubuntu 主机本地 terminal 执行：

```bash
sudo adduser codex_sdr
sudo usermod -aG sudo,dialout,plugdev,netdev codex_sdr
sudo install -d -m 700 -o codex_sdr -g codex_sdr /home/codex_sdr/.ssh
sudo nano /home/codex_sdr/.ssh/authorized_keys
```

把公钥粘贴进去，然后：

```bash
sudo chown codex_sdr:codex_sdr /home/codex_sdr/.ssh/authorized_keys
sudo chmod 600 /home/codex_sdr/.ssh/authorized_keys
```

如果需要无人值守执行 `sudo`，临时开启免密 sudo：

```bash
echo 'codex_sdr ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/codex_sdr
sudo chmod 440 /etc/sudoers.d/codex_sdr
```

这等价于给远程操作者完整 root 权限。联调结束后必须删除。

### 2.3 本机 SSH 配置

在本机 `~/.ssh/config` 中加入：

```text
Host radar-ubuntu
  HostName REPLACE_WITH_UBUNTU_IP
  User codex_sdr
  IdentityFile ~/.ssh/sdr_codex_temp_ed25519
  IdentitiesOnly yes
```

测试：

```powershell
ssh radar-ubuntu "hostname && whoami && ip -br addr"
```

## 3. 备份策略

### 3.1 最稳妥的整机备份

如果目标是“坏了能整机恢复原状”，最稳妥方案是赛前用 Clonezilla 做离线整盘镜像：

1. 插入外置硬盘。
2. 用 Clonezilla Live U 盘启动 Ubuntu 主机。
3. 选择 `device-image`。
4. 保存整盘镜像到外置硬盘。
5. 镜像目录命名为 `radar-host-pre-sdr-YYYYMMDD-HHMM`。
6. 记录主机型号、硬盘名、镜像路径。

恢复方法：

1. 用 Clonezilla Live U 盘启动。
2. 选择 `device-image`。
3. 选择之前保存的镜像。
4. 执行 restore disk。
5. 重启后系统回到镜像时刻。

优点：最接近真正整机快照。
缺点：需要物理操作和停机。

### 3.2 远程在线备份

如果只能通过 SSH 远程操作，在线备份不能保证像离线整盘镜像一样完美，但足够用于恢复代码、配置、ROS 工作区和大部分系统状态。

创建备份目录：

```bash
sudo mkdir -p /opt/sdr_backup/pre_remote_$(date +%Y%m%d_%H%M%S)
sudo chown -R "$USER":"$USER" /opt/sdr_backup
BACKUP_DIR=$(ls -dt /opt/sdr_backup/pre_remote_* | head -1)
echo "$BACKUP_DIR"
```

保存系统信息：

```bash
mkdir -p "$BACKUP_DIR/meta"
date -Is | tee "$BACKUP_DIR/meta/date.txt"
hostnamectl | tee "$BACKUP_DIR/meta/hostnamectl.txt"
uname -a | tee "$BACKUP_DIR/meta/uname.txt"
ip -br addr | tee "$BACKUP_DIR/meta/ip_addr.txt"
ip route | tee "$BACKUP_DIR/meta/ip_route.txt"
lsblk -f | tee "$BACKUP_DIR/meta/lsblk_f.txt"
sudo blkid | tee "$BACKUP_DIR/meta/blkid.txt"
dpkg --get-selections | tee "$BACKUP_DIR/meta/dpkg_selections.txt"
apt-mark showmanual | tee "$BACKUP_DIR/meta/apt_manual.txt"
systemctl list-unit-files | tee "$BACKUP_DIR/meta/systemd_unit_files.txt"
```

保存分区表，先确认系统盘名：

```bash
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
```

假设系统盘是 `/dev/nvme0n1`：

```bash
sudo sfdisk -d /dev/nvme0n1 | tee "$BACKUP_DIR/meta/partition_table.sfdisk"
```

备份关键配置：

```bash
sudo tar --xattrs --acls -cpf "$BACKUP_DIR/etc.tar" /etc
sudo tar --xattrs --acls -cpf "$BACKUP_DIR/udev_and_net.tar" /etc/udev /etc/netplan 2>/dev/null || true
```

备份 ROS 工作区和用户目录中的关键工程：

```bash
tar --xattrs --acls -cpf "$BACKUP_DIR/radar_ws.tar" ~/radar_ws 2>/dev/null || true
tar --xattrs --acls -cpf "$BACKUP_DIR/home_sdr_related.tar" \
  ~/sdr ~/sdr_receiver ~/sdr_receiver_ros2_package_pluto_channel_fix 2>/dev/null || true
```

如果工程是 Git 仓库，也保存状态：

```bash
find ~ -maxdepth 4 -type d -name .git 2>/dev/null | while read -r gitdir; do
  repo=$(dirname "$gitdir")
  safe=$(echo "$repo" | sed 's#/#_#g')
  mkdir -p "$BACKUP_DIR/git"
  git -C "$repo" status --short > "$BACKUP_DIR/git/${safe}_status.txt" || true
  git -C "$repo" rev-parse HEAD > "$BACKUP_DIR/git/${safe}_head.txt" || true
  git -C "$repo" bundle create "$BACKUP_DIR/git/${safe}.bundle" --all || true
done
```

生成校验：

```bash
cd "$BACKUP_DIR"
sha256sum * 2>/dev/null | tee SHA256SUMS.txt
```

### 3.3 在线备份恢复方法

恢复某个工程：

```bash
mkdir -p ~/restore_test
tar -xpf /opt/sdr_backup/pre_remote_YYYYMMDD_HHMMSS/radar_ws.tar -C ~/restore_test
```

恢复 `/etc` 中某个文件：

```bash
mkdir -p ~/restore_etc
sudo tar -xpf /opt/sdr_backup/pre_remote_YYYYMMDD_HHMMSS/etc.tar -C ~/restore_etc
sudo cp ~/restore_etc/etc/目标文件 /etc/目标文件
```

用 Git bundle 恢复仓库：

```bash
git clone /opt/sdr_backup/pre_remote_YYYYMMDD_HHMMSS/git/xxx.bundle restored_repo
```

如果系统被改坏但还能启动，优先恢复工作区和 `/etc` 中具体被改过的文件。
如果系统无法启动，使用 Live USB 挂载硬盘后从 `/opt/sdr_backup` 中取回 tar 包恢复。

## 4. 远程联调执行方式

远程进入后先开 tmux：

```bash
ssh radar-ubuntu
tmux new -s sdr_debug
```

建立联调日志目录：

```bash
mkdir -p ~/sdr_remote_logs/$(date +%Y%m%d_%H%M%S)
LOG_DIR=$(ls -dt ~/sdr_remote_logs/* | head -1)
echo "$LOG_DIR"
```

记录基础状态：

```bash
{
  date -Is
  hostname
  ip -br addr
  source /opt/ros/humble/setup.bash
  ros2 --version || true
  iio_info -u ip:192.168.2.1 | head -80 || true
} 2>&1 | tee "$LOG_DIR/baseline.txt"
```

## 5. 两份接收端代码的测试矩阵

需要测试：

1. 本对话生成的 `sdr_receiver`
2. 另一台雷达主机中的 `sdr_receiver_ros2_package_pluto_channel_fix`

只用 debug 模式测试接收端解调效果。

每一份代码都按同样流程记录：

```text
代码版本
编译结果
启动参数
SDR IP 与增益/带宽/频点
发射端场景
首次 AC 命中时间
首次 CRC16 成功时间
首次 0x0A06 key 时间
首次 0x0A01..0x0A05 INFO 时间
连续 60 秒成功率
```

## 6. 发射端与接收端联调步骤

当前硬件连接：

- 两台 SDR 发射板连接本机 Windows，作为发射端。
- 接收端 SDR 连接 Ubuntu 主机，作为接收端。

推荐流程：

1. 本机 Windows 启动需求文档中的发射端工程。
2. 一台发 INFO，另一台发 JAM。
3. Ubuntu 主机通过 SSH/tmux 启动接收端 debug 模式。
4. 接收端记录 `/sdr/status`、`/sdr/jam_code`、`/sdr/radar_wireless/raw_frame`。
5. 每次只改一个变量：频点、gain、rf_bw、digital_shift 或 filter。
6. 每轮测试至少持续 60 秒。
7. 达到“发射端发出后 1-2 秒内接收端稳定解调成功”再保存 profile。

建议同时记录 ROS2 bag：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 bag record \
  /sdr/status \
  /sdr/jam_code \
  /sdr/radar_wireless/raw_frame \
  -o "$LOG_DIR/sdr_debug_bag"
```

## 7. 1-2 秒目标如何判定

为了避免主观判断，建议使用带时间戳的日志：

- 发射端记录每次场景开始时间和 payload 内容。
- 接收端记录首次收到对应 key 或 INFO raw frame 的时间。
- Windows 与 Ubuntu 主机尽量接入同一网络并校时。

Ubuntu 校时检查：

```bash
timedatectl
```

如果允许安装：

```bash
sudo apt install -y chrony
sudo systemctl enable --now chrony
```

如果不能做精确跨机器校时，至少使用固定序列 payload。例如每 5 秒改变一次金币数或 key，观察接收端在变化后多久更新。

## 8. 操作权限回收

联调完成后删除临时 sudo 权限：

```bash
sudo rm -f /etc/sudoers.d/codex_sdr
```

删除临时用户：

```bash
sudo deluser --remove-home codex_sdr
```

本机删除临时私钥：

```powershell
Remove-Item $env:USERPROFILE\.ssh\sdr_codex_temp_ed25519
Remove-Item $env:USERPROFILE\.ssh\sdr_codex_temp_ed25519.pub
```

## 9. 上下文管理

不要只依赖聊天上下文保存联调状态。每个阶段都应写入仓库或主机日志：

- `REMOTE_BASELINE.md`
- `REMOTE_BACKUP_MANIFEST.md`
- `TEST_MATRIX.md`
- `LAST_KNOWN_GOOD_PROFILE.yaml`
- `sdr_remote_logs/YYYYMMDD_HHMMSS/`

如果聊天上下文变长，可以开启新对话，但新对话第一条应贴：

```text
1. 当前目标
2. 远程主机 IP 和 SSH Host 别名
3. 备份目录
4. 当前代码路径
5. 当前测试结论
6. 下一步要跑的命令
7. 禁止修改的目录或工程
```

这样即使发生上下文压缩，也不会丢掉真正重要的状态。
