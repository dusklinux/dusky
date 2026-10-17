//! Read LE connection RSSI without discovery, subprocesses, or controller changes.
//! Linux management protocol: Get Connections (0x0015), Get Connection Info (0x0031).
use std::{
    io,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    time::{Duration, Instant},
};
use tokio::io::unix::AsyncFd;

const GET_CONNECTIONS: u16 = 0x0015;
const GET_INFO: u16 = 0x0031;
const REPLY_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Debug)]
pub struct Target {
    pub key: String,
    pub address: [u8; 6],
    pub epoch: u64,
}
#[derive(Debug)]
pub struct Reading {
    pub key: String,
    pub epoch: u64,
    pub rssi: Option<i16>,
    pub observed: Instant,
}

pub fn address(mac: &str) -> io::Result<[u8; 6]> {
    let mut result = [0; 6];
    let parts: Vec<_> = mac.split(':').collect();
    if parts.len() != 6 {
        return Err(invalid("invalid Bluetooth address"));
    }
    for (dst, src) in result.iter_mut().rev().zip(parts) {
        if src.len() != 2 {
            return Err(invalid("invalid Bluetooth address"));
        }
        *dst = u8::from_str_radix(src, 16).map_err(|_| invalid("invalid Bluetooth address"))?;
    }
    Ok(result)
}
pub fn adapter_index(path: &str) -> io::Result<u16> {
    path.strip_prefix("/org/bluez/hci")
        .and_then(|s| s.parse().ok())
        .filter(|&i| i != u16::MAX)
        .ok_or_else(|| invalid("invalid BlueZ controller path"))
}
fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

pub struct Reader(AsyncFd<OwnedFd>);
impl Reader {
    pub fn open() -> io::Result<Self> {
        #[repr(C)]
        struct SockAddrHci {
            family: libc::sa_family_t,
            device: u16,
            channel: u16,
        }
        // SAFETY: standard Linux Bluetooth socket; successful fd becomes uniquely owned.
        let fd = unsafe {
            libc::socket(
                libc::AF_BLUETOOTH,
                libc::SOCK_RAW | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
                1,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let fd = unsafe { OwnedFd::from_raw_fd(fd) };
        let addr = SockAddrHci {
            family: libc::AF_BLUETOOTH as _,
            device: u16::MAX,
            channel: 3,
        };
        // SAFETY: repr(C) sockaddr_hci with its exact size and a live descriptor.
        if unsafe {
            libc::bind(
                fd.as_raw_fd(),
                (&addr as *const SockAddrHci).cast(),
                size_of::<SockAddrHci>() as _,
            )
        } < 0
        {
            let err = io::Error::last_os_error();
            return Err(io::Error::new(
                err.kind(),
                format!(
                    "opening Bluetooth management socket: {err}; run proximity_trigger/build.sh to install CAP_NET_ADMIN"
                ),
            ));
        }
        Ok(Self(AsyncFd::new(fd)?))
    }
    async fn send(&self, packet: &[u8]) -> io::Result<()> {
        loop {
            let mut guard = self.0.writable().await?;
            match guard.try_io(|fd| {
                // SAFETY: live fd and packet buffer of the supplied length.
                let n = unsafe {
                    libc::send(
                        fd.as_raw_fd(),
                        packet.as_ptr().cast(),
                        packet.len(),
                        libc::MSG_NOSIGNAL,
                    )
                };
                if n < 0 {
                    Err(io::Error::last_os_error())
                } else if n as usize != packet.len() {
                    Err(invalid("short management send"))
                } else {
                    Ok(())
                }
            }) {
                Ok(result) => return result,
                Err(_) => continue,
            }
        }
    }
    async fn receive(&self, buffer: &mut [u8]) -> io::Result<usize> {
        loop {
            let mut guard = self.0.readable().await?;
            match guard.try_io(|fd| {
                // MSG_TRUNC reports the full datagram length, so truncation is detectable.
                // SAFETY: writable buffer and live fd; recv cannot write beyond buffer.len().
                let n = unsafe {
                    libc::recv(
                        fd.as_raw_fd(),
                        buffer.as_mut_ptr().cast(),
                        buffer.len(),
                        libc::MSG_TRUNC,
                    )
                };
                if n < 0 {
                    Err(io::Error::last_os_error())
                } else if n == 0 || n as usize > buffer.len() {
                    Err(invalid("truncated management datagram"))
                } else {
                    Ok(n as usize)
                }
            }) {
                Ok(result) => return result,
                Err(_) => continue,
            }
        }
    }
    async fn command(&self, index: u16, opcode: u16, payload: &[u8]) -> io::Result<(u8, Vec<u8>)> {
        let mut packet = Vec::with_capacity(6 + payload.len());
        packet.extend_from_slice(&opcode.to_le_bytes());
        packet.extend_from_slice(&index.to_le_bytes());
        packet.extend_from_slice(&(payload.len() as u16).to_le_bytes());
        packet.extend_from_slice(payload);
        tokio::time::timeout(REPLY_TIMEOUT, async {
            self.send(&packet).await?;
            let mut buffer = [0u8; 65541];
            loop {
                let n = self.receive(&mut buffer).await?;
                if let Some(result) = reply(&buffer[..n], index, opcode)? {
                    return Ok(result);
                }
            }
        })
        .await
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::TimedOut,
                format!("Bluetooth management command {opcode:#06x} timed out"),
            )
        })?
    }
    pub async fn sample(&self, index: u16, targets: &[Target]) -> io::Result<Vec<Reading>> {
        let (status, data) = self.command(index, GET_CONNECTIONS, &[]).await?;
        if status == 0x0f {
            return Ok(Vec::new());
        } // Radio went off during the request.
        check_status(status)?;
        let connections = connections(&data)?;
        let mut readings = Vec::with_capacity(targets.len());
        for target in targets {
            // BR/EDR HCI RSSI is relative to the golden receive range, not LE dBm.
            // Never apply LE proximity thresholds to a classic connection.
            let link = connections
                .iter()
                .find(|a| a[..6] == target.address && matches!(a[6], 1 | 2));
            let rssi = if let Some(link) = link {
                let (status, data) = self.command(index, GET_INFO, link).await?;
                if matches!(status, 0x02 | 0x0f) {
                    None
                } else {
                    check_status(status)?;
                    info(&data, link)?
                }
            } else {
                None
            };
            readings.push(Reading {
                key: target.key.clone(),
                epoch: target.epoch,
                rssi,
                observed: Instant::now(),
            });
        }
        Ok(readings)
    }
}
fn check_status(status: u8) -> io::Result<()> {
    if status == 0 {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "Bluetooth management status {status:#04x}"
        )))
    }
}
fn reply(packet: &[u8], index: u16, opcode: u16) -> io::Result<Option<(u8, Vec<u8>)>> {
    if packet.len() < 6 {
        return Err(invalid("short management header"));
    }
    let word = |at| u16::from_le_bytes([packet[at], packet[at + 1]]);
    if packet.len() != 6 + usize::from(word(4)) {
        return Err(invalid("invalid management packet length"));
    }
    if word(2) != index || !matches!(word(0), 1 | 2) {
        return Ok(None);
    }
    if packet.len() < 9 {
        return Err(invalid("short management command reply"));
    }
    if word(6) != opcode {
        return Ok(None);
    }
    Ok(Some((packet[8], packet[9..].to_vec())))
}
fn connections(data: &[u8]) -> io::Result<Vec<[u8; 7]>> {
    if data.len() < 2 {
        return Err(invalid("short connections reply"));
    }
    let count = usize::from(u16::from_le_bytes([data[0], data[1]]));
    if data.len() != 2 + count * 7 {
        return Err(invalid("invalid connections count"));
    }
    data[2..]
        .as_chunks::<7>()
        .0
        .iter()
        .map(|c| {
            if c[6] > 2 {
                return Err(invalid("invalid Bluetooth address type"));
            }
            Ok(*c)
        })
        .collect()
}
fn info(data: &[u8], link: &[u8; 7]) -> io::Result<Option<i16>> {
    if data.len() != 10 || data[..7] != *link {
        return Err(invalid("mismatched connection info"));
    }
    let rssi = i16::from(data[7] as i8);
    Ok((-127..=20).contains(&rssi).then_some(rssi))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn packet(event: u16, index: u16, opcode: u16, status: u8, data: &[u8]) -> Vec<u8> {
        let mut p = Vec::new();
        for word in [event, index, (3 + data.len()) as u16, opcode] {
            p.extend_from_slice(&word.to_le_bytes());
        }
        p.push(status);
        p.extend_from_slice(data);
        p
    }
    fn pair() -> (Reader, tokio::net::UnixDatagram) {
        let (client, server) = std::os::unix::net::UnixDatagram::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        server.set_nonblocking(true).unwrap();
        (
            Reader(AsyncFd::new(client.into()).unwrap()),
            tokio::net::UnixDatagram::from_std(server).unwrap(),
        )
    }
    #[test]
    fn validates_identity_and_wire_shapes() {
        assert_eq!(
            address("12:34:56:78:9A:BC").unwrap(),
            [0xbc, 0x9a, 0x78, 0x56, 0x34, 0x12]
        );
        for bad in [
            "",
            "1:22:33:44:55:66",
            "GG:22:33:44:55:66",
            "11:22:33:44:55:66:77",
        ] {
            assert!(address(bad).is_err());
        }
        assert_eq!(adapter_index("/org/bluez/hci12").unwrap(), 12);
        for bad in [
            "hci0",
            "/org/bluez/hci65535",
            "/org/bluez/hci-1",
            "/org/bluez/hci0/dev_00",
        ] {
            assert!(adapter_index(bad).is_err());
        }
        assert!(connections(&[1, 0]).is_err());
        assert!(connections(&[0, 0, 1]).is_err());
        assert!(connections(&[1, 0, 0, 0, 0, 0, 0, 0, 3]).is_err());
        let link = [1, 2, 3, 4, 5, 6, 2];
        let mut data = link.to_vec();
        data.extend_from_slice(&[127, 127, 127]);
        assert_eq!(info(&data, &link).unwrap(), None);
        data[7] = (-64i8) as u8;
        assert_eq!(info(&data, &link).unwrap(), Some(-64));
        data[0] = 0;
        assert!(info(&data, &link).is_err());
    }
    #[tokio::test]
    async fn reads_only_requested_le_links_and_ignores_unrelated_events() {
        let (reader, server) = pair();
        let a = address("12:34:56:78:9A:BC").unwrap();
        let mut link = a.to_vec();
        link.push(2);
        let targets = [
            Target {
                key: "le".into(),
                address: a,
                epoch: 7,
            },
            Target {
                key: "classic".into(),
                address: [3; 6],
                epoch: 1,
            },
            Target {
                key: "absent".into(),
                address: [9; 6],
                epoch: 2,
            },
        ];
        let responder = async {
            let mut buf = [0; 64];
            let n = server.recv(&mut buf).await.unwrap();
            assert_eq!(&buf[..n], &[0x15, 0, 4, 0, 0, 0]);
            let mut payload = vec![3, 0];
            payload.extend_from_slice(&link);
            payload.extend_from_slice(&[3, 3, 3, 3, 3, 3, 0]);
            payload.extend_from_slice(&[8, 8, 8, 8, 8, 8, 1]);
            for unrelated in [
                packet(1, 5, GET_CONNECTIONS, 0, &payload),
                packet(1, 4, 0x9999, 0, &[]),
            ] {
                server.send(&unrelated).await.unwrap();
            }
            server
                .send(&packet(1, 4, GET_CONNECTIONS, 0, &payload))
                .await
                .unwrap();
            let n = server.recv(&mut buf).await.unwrap();
            assert_eq!(&buf[..6], &[0x31, 0, 4, 0, 7, 0]);
            assert_eq!(&buf[6..n], link);
            let mut payload = link.clone();
            payload.extend_from_slice(&[(-61i8) as u8, 127, 127]);
            server
                .send(&packet(1, 4, GET_INFO, 0, &payload))
                .await
                .unwrap();
        };
        let (result, ()) = tokio::join!(reader.sample(4, &targets), responder);
        let readings = result.unwrap();
        assert_eq!(
            readings.iter().map(|r| r.rssi).collect::<Vec<_>>(),
            [Some(-61), None, None]
        );
        assert_eq!(readings[0].epoch, 7);
        assert!(
            tokio::time::timeout(Duration::from_millis(10), server.recv(&mut [0; 64]))
                .await
                .is_err()
        );
    }
    #[tokio::test]
    async fn timeouts_and_error_statuses_do_not_supply_signal() {
        let (reader, server) = pair();
        let responder = async {
            server.recv(&mut [0; 64]).await.unwrap();
            server
                .send(&packet(2, 0, GET_CONNECTIONS, 0x0f, &[]))
                .await
                .unwrap();
        };
        let (result, ()) = tokio::join!(reader.sample(0, &[]), responder);
        assert!(result.unwrap().is_empty());
        let (reader, _server) = pair();
        assert_eq!(
            reader
                .command(0, GET_CONNECTIONS, &[])
                .await
                .unwrap_err()
                .kind(),
            io::ErrorKind::TimedOut
        );
    }
    #[test]
    fn stress_malformed_datagrams_and_all_rssi_values() {
        let link = [1, 2, 3, 4, 5, 6, 1];
        for byte in 0..=255u8 {
            let mut data = link.to_vec();
            data.extend_from_slice(&[byte, 0, 0]);
            assert_eq!(
                info(&data, &link).unwrap(),
                (-127..=20)
                    .contains(&i16::from(byte as i8))
                    .then_some(i16::from(byte as i8))
            );
        }
        let mut random = 41u64;
        for len in 0..256 {
            for _ in 0..256 {
                let data: Vec<_> = (0..len)
                    .map(|_| {
                        random = random.wrapping_mul(6364136223846793005).wrapping_add(1);
                        (random >> 32) as u8
                    })
                    .collect();
                let _ = reply(&data, 0, GET_INFO);
                let _ = connections(&data);
                let _ = info(&data, &link);
            }
        }
    }
}
