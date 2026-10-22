mod bots;
mod limits;
mod site;
mod supervisor;
use axum::{Router, routing::any};
use sqlx::mysql::{MySqlConnectOptions, MySqlPoolOptions};
use std::{sync::Arc, time::Duration};
use tower_http::{compression::CompressionLayer, services::ServeDir};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let root = if let Ok(root) = std::env::var("PROJECT_DIR") {
        std::path::PathBuf::from(root)
    } else {
        let cwd = std::env::current_dir()?;
        if cwd.join(".env").is_file() {
            cwd
        } else {
            std::env::current_exe()?
                .ancestors()
                .find(|p| p.join(".env").is_file())
                .ok_or("프로젝트 설정 파일을 찾을 수 없습니다.")?
                .to_path_buf()
        }
    };
    dotenvy::from_path(root.join(".env"))?;
    if !std::env::args().any(|a| a == "--serve") {
        supervisor::run(&root)?;
        return Ok(());
    }
    if std::env::args().any(|a| a == "--serve") {
        use std::io::BufRead;
        let mut ready = String::new();
        std::io::stdin().lock().read_line(&mut ready)?;
        if ready.trim() != "ready" {
            return Err("자원 제한 확인 실패".into());
        }
    }
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(2)
        .enable_all()
        .build()?
        .block_on(serve(root))
}
async fn serve(root: std::path::PathBuf) -> Result<(), Box<dyn std::error::Error>> {
    dotenvy::from_path(root.join(".env"))?;
    let addr = site::env("RUST_TEST_ADDR", &site::env("LISTEN_ADDR", "127.0.0.1:80"));
    let socket = addr.parse::<std::net::SocketAddr>()?;
    if !socket.ip().is_loopback() {
        return Err("서버는 Cloudflare 터널용 로컬 주소에서만 실행할 수 있습니다.".into());
    }
    let production_database = site::env("DB_NAME", "luminus_db");
    let database = site::env("RUST_TEST_DB", &production_database);
    if database != production_database
        && (socket.port() == 80 || database != "luminus_db_migration_test")
    {
        return Err("테스트 DB는 별도 테스트 포트에서만 사용할 수 있습니다.".into());
    }
    let options = MySqlConnectOptions::new()
        .host(&site::env("DB_HOST", "127.0.0.1"))
        .port(site::env("DB_PORT", "3306").parse()?)
        .username(&site::env("DB_USER", "root"))
        .password(&site::env("DB_PASSWORD", ""))
        .database(&database);
    let db = MySqlPoolOptions::new()
        .max_connections(4)
        .min_connections(1)
        .acquire_timeout(Duration::from_secs(3))
        .after_connect(|conn, _| {
            Box::pin(async move {
                sqlx::query("SET SESSION max_execution_time=3000, innodb_lock_wait_timeout=5")
                    .execute(conn)
                    .await?;
                Ok(())
            })
        })
        .connect_with(options)
        .await?;
    let state = Arc::new(site::Site::new(root.clone(), db)?);
    state.refresh_controls().await?;
    site::tasks::start(state.clone());
    let controls = state.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(30)).await;
            if controls.refresh_controls().await.is_err() {
                eprintln!("사이트 설정 갱신 실패");
            }
        }
    });
    let router = Router::new()
        .nest_service("/static", ServeDir::new(root.join("static")))
        .fallback(any(site::dispatch))
        .layer(CompressionLayer::new())
        .layer(axum::middleware::from_fn_with_state(
            limits::Gate::new(state.clone()),
            limits::guard,
        ))
        .with_state(state);
    println!("Rust site: {addr}; MySQL={database}");
    axum::serve(limits::Listener::bind(&addr).await?, router).await?;
    Ok(())
}
