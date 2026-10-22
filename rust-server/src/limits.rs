use axum::{
    body::{Body, to_bytes},
    extract::Request,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Response},
};
use futures_util::StreamExt;
use std::{
    collections::HashMap,
    future::Future,
    io,
    net::{IpAddr, SocketAddr},
    pin::Pin,
    sync::{Arc, Mutex},
    task::{Context, Poll},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    net::{TcpListener, TcpStream},
    sync::{OwnedSemaphorePermit, Semaphore},
};
pub struct Socket {
    socket: TcpStream,
    _permit: OwnedSemaphorePermit,
    deadline: Pin<Box<tokio::time::Sleep>>,
}
impl AsyncRead for Socket {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        if self.deadline.as_mut().poll(cx).is_ready() {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "connection lifetime exceeded",
            )));
        }
        Pin::new(&mut self.socket).poll_read(cx, buf)
    }
}
impl AsyncWrite for Socket {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.socket).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.socket).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.socket).poll_shutdown(cx)
    }
}
pub struct Listener {
    socket: TcpListener,
    slots: Arc<Semaphore>,
}
impl Listener {
    pub async fn bind(addr: &str) -> io::Result<Self> {
        Ok(Self {
            socket: TcpListener::bind(addr).await?,
            slots: Arc::new(Semaphore::new(128)),
        })
    }
}
impl axum::serve::Listener for Listener {
    type Io = Socket;
    type Addr = SocketAddr;
    async fn accept(&mut self) -> (Socket, SocketAddr) {
        loop {
            match self.socket.accept().await {
                Ok((socket, addr)) => match self.slots.clone().try_acquire_owned() {
                    Ok(permit) => {
                        return (
                            Socket {
                                socket,
                                _permit: permit,
                                deadline: Box::pin(tokio::time::sleep(Duration::from_secs(60))),
                            },
                            addr,
                        );
                    }
                    Err(_) => {
                        drop(socket);
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                },
                Err(_) => tokio::time::sleep(Duration::from_millis(100)).await,
            }
        }
    }
    fn local_addr(&self) -> io::Result<SocketAddr> {
        self.socket.local_addr()
    }
}
struct Bucket {
    tokens: f64,
    at: Instant,
}
impl Bucket {
    fn allow(&mut self, now: Instant, rate: f64, burst: f64) -> bool {
        self.tokens = (self.tokens + now.duration_since(self.at).as_secs_f64() * rate).min(burst);
        self.at = now;
        if self.tokens < 1.0 {
            return false;
        }
        self.tokens -= 1.0;
        true
    }
}
pub struct Gate {
    bots: Arc<crate::bots::Bots>,
    slots: Arc<Semaphore>,
    clients: Mutex<HashMap<(IpAddr, u8), Bucket>>,
    global: Mutex<Bucket>,
    site: Arc<crate::site::Site>,
}
impl Gate {
    pub fn new(site: Arc<crate::site::Site>) -> Arc<Self> {
        let bots = crate::bots::Bots::new(site.clone());
        let _ = site.bots.set(bots.clone());
        Arc::new(Self {
            bots,
            slots: Arc::new(Semaphore::new(16)),
            clients: Mutex::new(HashMap::new()),
            global: Mutex::new(Bucket {
                tokens: 400.0,
                at: Instant::now(),
            }),
            site,
        })
    }
}
fn reject(status: StatusCode, message: &'static str) -> Response {
    (
        status,
        [("Retry-After", "2"), ("Cache-Control", "no-store")],
        message,
    )
        .into_response()
}
pub async fn guard(
    axum::extract::State(gate): axum::extract::State<Arc<Gate>>,
    request: Request,
    next: Next,
) -> Response {
    let permit = match gate.slots.clone().try_acquire_owned() {
        Ok(p) => p,
        Err(_) => {
            return reject(
                StatusCode::SERVICE_UNAVAILABLE,
                "잠시 후 다시 시도해주세요.",
            );
        }
    };
    let ip = request
        .headers()
        .get("cf-connecting-ip")
        .and_then(|h| h.to_str().ok())
        .and_then(|s| s.parse::<IpAddr>().ok())
        .unwrap_or(IpAddr::from([127, 0, 0, 1]));
    {
        let controls = gate.site.controls.read().unwrap();
        if controls.banned.contains(&ip.to_string()) {
            return reject(StatusCode::FORBIDDEN, "서버에서 IP가 차단되었습니다.");
        }
        if controls.blocked.iter().any(|p| {
            request.uri().path() == p || p.ends_with('/') && request.uri().path().starts_with(p)
        }) {
            return reject(StatusCode::NOT_FOUND, "요청한 주소를 찾을 수 없습니다.");
        }
    }
    if crate::bots::attack_path(request.uri().path()) {
        let reason = format!(
            "자동 차단: 공격 탐색 경로 {}",
            request.uri().path().chars().take(160).collect::<String>()
        );
        if let Err(err) = gate.site.auto_ban(ip, &reason).await {
            eprintln!("공격 탐색 IP 차단 저장 실패: {err}");
        }
        return reject(StatusCode::FORBIDDEN, "서버에서 IP가 차단되었습니다.");
    }
    if gate.bots.matches(ip)
        || crate::bots::known_agent(
            request
                .headers()
                .get("user-agent")
                .and_then(|v| v.to_str().ok())
                .unwrap_or(""),
        )
    {
        if let Err(err) = gate.site.auto_ban(ip, "자동 차단: 알려진 봇").await {
            eprintln!("봇 IP 차단 저장 실패: {err}");
        }
        return reject(StatusCode::FORBIDDEN, "서버에서 IP가 차단되었습니다.");
    }
    if !ip.is_loopback() {
        let country = request
            .headers()
            .get("cf-ipcountry")
            .and_then(|v| v.to_str().ok())
            .map(str::to_ascii_uppercase)
            .unwrap_or_else(|| gate.site.country(&ip.to_string()));
        if country != "KR" {
            return reject(StatusCode::FORBIDDEN, "한국에서만 접속할 수 있습니다.");
        }
    }
    let class = if request.uri().path().starts_with("/static/") {
        0
    } else if ["/api/login", "/api/register"].contains(&request.uri().path()) {
        2
    } else {
        1
    };
    let now = Instant::now();
    if !gate.global.lock().unwrap().allow(now, 200.0, 400.0) {
        return reject(StatusCode::TOO_MANY_REQUESTS, "요청이 많습니다.");
    }
    if !matches!(request.method().as_str(), "GET" | "HEAD" | "OPTIONS") {
        let (total, free) = crate::supervisor::disk_bytes(&gate.site.root);
        if total > 0 && free < 2 * 1024 * 1024 * 1024 {
            return reject(
                StatusCode::INSUFFICIENT_STORAGE,
                "디스크 공간이 부족해 저장을 일시 중단했습니다.",
            );
        }
    }
    {
        let mut clients = gate.clients.lock().unwrap();
        if clients.len() >= 4096 && !clients.contains_key(&(ip, class)) {
            clients.retain(|_, b| now.duration_since(b.at) < Duration::from_secs(120));
            if clients.len() >= 4096 {
                return reject(StatusCode::TOO_MANY_REQUESTS, "요청이 많습니다.");
            }
        }
        let (rate, burst) = match class {
            0 => (100.0, 200.0),
            2 => (0.1, 5.0),
            _ => (8.0, 64.0),
        };
        if !clients
            .entry((ip, class))
            .or_insert(Bucket {
                tokens: burst,
                at: now,
            })
            .allow(now, rate, burst)
        {
            return reject(StatusCode::TOO_MANY_REQUESTS, "요청이 많습니다.");
        }
    }
    if request
        .headers()
        .get("content-length")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<u64>().ok())
        .is_some_and(|size| size > 8 * 1024 * 1024)
    {
        return reject(
            StatusCode::PAYLOAD_TOO_LARGE,
            "업로드는 8MB까지 가능합니다.",
        );
    }
    let (parts, body) = request.into_parts();
    let bytes = match tokio::time::timeout(Duration::from_secs(10), to_bytes(body, 8 * 1024 * 1024))
        .await
    {
        Ok(Ok(b)) => b,
        Ok(Err(_)) => return reject(StatusCode::PAYLOAD_TOO_LARGE, "요청이 너무 큽니다."),
        Err(_) => return reject(StatusCode::REQUEST_TIMEOUT, "업로드 시간이 초과되었습니다."),
    };
    let is_static = parts.uri.path().starts_with("/static/");
    let versioned = is_static
        && parts
            .uri
            .query()
            .is_some_and(|q| q.split('&').any(|p| p.starts_with("v=")));
    let request = Request::from_parts(parts, Body::from(bytes));
    let response_timeout=if request.uri().path()=="/api/lumi/chat" {180}else{30};
    let response = match tokio::time::timeout(Duration::from_secs(response_timeout), next.run(request)).await {
        Ok(r) => r,
        Err(_) => return reject(StatusCode::GATEWAY_TIMEOUT, "처리 시간이 초과되었습니다."),
    };
    let (mut parts, body) = response.into_parts();
    parts
        .headers
        .insert("x-content-type-options", "nosniff".parse().unwrap());
    parts
        .headers
        .insert("x-frame-options", "SAMEORIGIN".parse().unwrap());
    parts.headers.insert(
        "referrer-policy",
        "strict-origin-when-cross-origin".parse().unwrap(),
    );
    if is_static && parts.status.is_success() {
        parts.headers.insert(
            "cache-control",
            if versioned {
                "public, max-age=31536000, immutable"
            } else {
                "public, max-age=86400"
            }
            .parse()
            .unwrap(),
        );
    }
    let stream = async_stream::stream! {let _permit=permit;let mut stream=body.into_data_stream();while let Some(chunk)=stream.next().await{yield chunk;}};
    Response::from_parts(parts, Body::from_stream(stream))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn token_bucket_is_bounded() {
        let start = Instant::now();
        let mut b = Bucket {
            tokens: 2.0,
            at: start,
        };
        assert!(b.allow(start, 1.0, 2.0));
        assert!(b.allow(start, 1.0, 2.0));
        assert!(!b.allow(start, 1.0, 2.0));
        assert!(b.allow(start + Duration::from_secs(1), 1.0, 2.0));
    }
}
